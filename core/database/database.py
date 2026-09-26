import settings.settings as settings
import pandas as pd
from sqlalchemy import select, func, exists, update, text, inspect, bindparam, or_
from sqlalchemy.dialects.sqlite import insert
from core.utils import logger
import os
from datetime import datetime
from dateutil.relativedelta import relativedelta
import re

from .models import (metadata, title_table, detail_traffic_table, detail_parking_table, detail_other_table,
                     merge_traffic_table, merge_parking_table, merge_other_table, watchlist_table, admin_users_table,
                     api_keys_table, entry_value_table, raw_content_table, sync_meta_table,
                     duplicate_group_table, duplicate_member_table)


def _current_epoch_millis() -> int:
    return int(datetime.now().timestamp() * 1000)


_DATE_ONLY_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DETAIL_TABLES = [detail_traffic_table, detail_parking_table, detail_other_table]
_FINAL_RAW_STATUSES = ("수용", "일부수용", "불수용", "기타", "답변완료", "취하", "이송")
_LEGACY_ONGOING_STATUSES = ("", "진행", "진행중", "처리중", "검토중")


def _parse_epoch_millis_from_text(value, *, prefer_end_of_day: bool = False):
    raw = str(value or "").strip()
    if not raw:
        return None

    parsed = pd.to_datetime(raw, errors="coerce")
    if pd.isna(parsed):
        return None

    timestamp = pd.Timestamp(parsed)
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_convert(None)

    if prefer_end_of_day and _DATE_ONLY_PATTERN.match(raw):
        timestamp = timestamp + pd.Timedelta(days=1) - pd.Timedelta(milliseconds=1)

    return int(timestamp.to_pydatetime().timestamp() * 1000)


def _derive_backfill_synced_at(answer_date, report_date):
    return (
        _parse_epoch_millis_from_text(answer_date, prefer_end_of_day=True)
        or _parse_epoch_millis_from_text(report_date, prefer_end_of_day=True)
    )


def _refresh_duplicate_groups(engine, *, track_changes: bool = False):
    try:
        from services import duplicate_group_service
        return duplicate_group_service.refresh_duplicate_groups(engine, track_changes=track_changes)
    except Exception as exc:
        logger.LoggerFactory.logbot.error(f"[duplicate] 중복군 재생성 실패: {exc}")
        return {"group_count": 0, "member_count": 0, "changes": []}


def backfill_synced_at(engine):
    """기존 서버 DB의 synced_at 공백을 답변일/신고일 기준으로 채운다."""
    updated_total = 0
    unresolved_total = 0

    with engine.begin() as conn:
        for detail_table in _DETAIL_TABLES:
            join_stmt = detail_table.outerjoin(title_table, detail_table.c.ID == title_table.c.ID)
            rows = conn.execute(
                select(
                    detail_table.c.ID,
                    detail_table.c.답변일,
                    title_table.c.신고일,
                )
                .select_from(join_stmt)
                .where(detail_table.c.synced_at.is_(None))
            ).fetchall()

            updates = []
            unresolved = 0
            for row in rows:
                synced_at = _derive_backfill_synced_at(row.답변일, row.신고일)
                if synced_at is None:
                    unresolved += 1
                    continue
                updates.append({"target_id": row.ID, "synced_at": synced_at})

            if updates:
                conn.execute(
                    update(detail_table)
                    .where(detail_table.c.ID == bindparam("target_id"))
                    .values(synced_at=bindparam("synced_at")),
                    updates,
                )
                updated_total += len(updates)

            if rows:
                unresolved_total += unresolved
                logger.LoggerFactory.logbot.info(
                    f"[migration] {detail_table.name} synced_at 백필: {len(updates)}건, 미해결 {unresolved}건"
                )

    if updated_total:
        logger.LoggerFactory.logbot.info(
            f"[migration] synced_at 백필 완료: 총 {updated_total}건 갱신, merge 재생성 시작"
        )
        merge_final(engine)
    elif unresolved_total:
        logger.LoggerFactory.logbot.warning(
            f"[migration] synced_at 백필 대상이 있었지만 날짜 파싱 불가로 {unresolved_total}건은 유지되었습니다."
        )
    else:
        logger.LoggerFactory.logbot.debug("[migration] synced_at 백필 대상 없음.")

    return updated_total


def _normalize_processing_layers(engine):
    updated_total = 0
    final_status_ids = select(title_table.c.ID).where(title_table.c.상태.in_(_FINAL_RAW_STATUSES))

    with engine.begin() as conn:
        for detail_table in _DETAIL_TABLES:
            raw_status_value = (
                select(title_table.c.상태)
                .where(title_table.c.ID == detail_table.c.ID)
                .scalar_subquery()
            )
            supplement_status_ids = select(title_table.c.ID).where(title_table.c.상태 == "보완요청")
            result = conn.execute(
                update(detail_table)
                .where(detail_table.c.ID.in_(final_status_ids))
                .where(
                    or_(
                        detail_table.c.처리상태.is_(None),
                        detail_table.c.처리상태.in_(_LEGACY_ONGOING_STATUSES),
                        detail_table.c.보완_미응답 == 'Y',
                    )
                )
                .values(
                    처리상태=raw_status_value,
                    종결여부='Y',
                    보완_미응답='N',
                )
            )
            updated_total += result.rowcount or 0

            result = conn.execute(
                update(detail_table)
                .where(detail_table.c.ID.in_(supplement_status_ids))
                .where(~detail_table.c.ID.in_(final_status_ids))
                .where(
                    or_(
                        detail_table.c.처리상태.is_(None),
                        detail_table.c.처리상태.in_(_LEGACY_ONGOING_STATUSES),
                        detail_table.c.처리상태 == '보완요청',
                    )
                )
                .values(처리상태='보완요청', 종결여부='N', 보완_미응답='Y')
            )
            updated_total += result.rowcount or 0

            result = conn.execute(
                update(detail_table)
                .where(detail_table.c.보완_미응답 == 'Y')
                .where(~detail_table.c.ID.in_(final_status_ids))
                .where(func.coalesce(detail_table.c.처리상태, '') != '보완요청')
                .values(처리상태='보완요청', 종결여부='N')
            )
            updated_total += result.rowcount or 0

            result = conn.execute(
                update(detail_table)
                .where(~detail_table.c.ID.in_(final_status_ids))
                .where(func.coalesce(detail_table.c.보완_미응답, '') != 'Y')  # NULL 행도 포함(S-20)
                .where(
                    or_(
                        detail_table.c.처리상태.is_(None),
                        detail_table.c.처리상태.in_(_LEGACY_ONGOING_STATUSES),
                    )
                )
                .values(처리상태='처리중', 종결여부='N')
            )
            updated_total += result.rowcount or 0

    if updated_total:
        logger.LoggerFactory.logbot.info(
            f"[migration] raw/canonical 상태 정규화 완료: 총 {updated_total}건 갱신"
        )
    else:
        logger.LoggerFactory.logbot.debug("[migration] raw/canonical 상태 정규화 대상 없음.")

    return updated_total

def normalize_police_agency(x: str) -> str:
    idx = x.find('경찰서')
    return x[:idx + 3] if idx != -1 else x

def category_from_entry_value(entry_value: str) -> str:
    """entry_value 문자열로부터 카테고리를 결정합니다."""
    if "자동차·교통위반" in entry_value:
        return "traffic"
    elif "불법주정차신고" in entry_value:
        return "parking"
    else:
        return "other"

def migrate_by_entry_value(engine):
    """entry_value 테이블 기반으로 잘못 분류된 신고를 올바른 detail 테이블로 이동합니다."""
    detail_tables = {
        "traffic": detail_traffic_table,
        "other":   detail_other_table,
        "parking": detail_parking_table,
    }

    with engine.connect() as conn:
        rows = conn.execute(select(entry_value_table)).fetchall()
        if not rows:
            return

        moved = 0
        for row in rows:
            record_id = row.ID
            correct_cat = category_from_entry_value(row.entry_value)
            correct_table = detail_tables[correct_cat]

            # 현재 어느 테이블에 있는지 찾기
            current_table = None
            current_cat = None
            for cat, tbl in detail_tables.items():
                try:
                    res = conn.execute(select(tbl).where(tbl.c.ID == record_id)).first()
                    if res:
                        current_table = tbl
                        current_cat = cat
                        break
                except Exception:
                    continue

            if current_table is None or current_cat == correct_cat:
                continue  # 이미 올바른 테이블이거나 DB에 없음

            # 올바른 테이블로 이동
            record = dict(conn.execute(select(current_table).where(current_table.c.ID == record_id)).first()._mapping)
            ins = insert(correct_table).values(record)
            ins = ins.on_conflict_do_update(
                index_elements=['ID'],
                set_={col.name: getattr(ins.excluded, col.name) for col in correct_table.c if col.name != 'ID'}
            )
            conn.execute(ins)
            conn.execute(current_table.delete().where(current_table.c.ID == record_id))
            moved += 1
            logger.LoggerFactory.logbot.info(
                f"[migrate] ID={record_id} {current_cat} → {correct_cat} (entry_value: {row.entry_value[:40]})"
            )

        conn.commit()

    if moved:
        logger.LoggerFactory.logbot.info(f"[migrate] entry_value 기반 {moved}건 재분류 완료. merge_final 재실행.")
        merge_final(engine)
    else:
        logger.LoggerFactory.logbot.debug("[migrate] entry_value 기반 재분류: 이동할 항목 없음.")


def upgrade_schema(engine, *, maintenance: bool = True, backup_dir: str | None = None):
    """표 만들기(새 DB)·빠진 표 만들기·인덱스, 그리고 maintenance=True 일 때(서버 시작·복원) 중복군 재계산.
    크롤링 서브프로세스는 maintenance=False 로 가볍게 부른다(S-22).

    이번 릴리스(초기화 크롤링 `source-rebuild-2026-09-26.1`)는 **이전 DB 업데이트 로직을 끈다**: 열 추가(ALTER), 번호 붙은 마이그레이션,
    감시목록 열 이관, entry_value 재분류, synced_at 백필, 상태 정규화, 업그레이드 전 백업은 아래에 주석으로 남겼다.
    이 버전보다 낮은 DB 는 여기서 고치지 않고 LegacyDatabase 로 멈춘다 — 서버 시작은 reset_legacy_database() 로 백업 뒤 비우고
    초기화 크롤링이 다시 채운다. 복원(가져오기)은 거절한다. backup_dir 은 호출 호환용으로만 받는다."""
    _refuse_newer_schema(engine)
    _refuse_legacy_schema(engine)
    # [이전 DB 업데이트 비활성 — 2026-09-26 초기화 크롤링 릴리스]
    # if backup_dir:
    #     backup_before_upgrade(engine, backup_dir)
    inspector = inspect(engine)
    with engine.connect() as connection:
        try:
            connection.execute(text("PRAGMA journal_mode=WAL;"))
            logger.LoggerFactory.logbot.debug("SQLite WAL 모드 활성화됨 (동시성 최적화)")
        except Exception:
            pass

        existing_tables = inspector.get_table_names()
        for table in metadata.sorted_tables:
            if table.name not in existing_tables:
                logger.LoggerFactory.logbot.info(f"테이블 '{table.name}' 생성 중...")
                table.create(connection)
                # [이전 DB 업데이트 비활성 — 2026-09-26 초기화 크롤링 릴리스] 옛 merge 표의 감시목록 열 → 감시목록 표 이관
                # if table.name == 'mysafety_watchlist':
                #     if settings.table_merge_traffic in existing_tables and settings.table_merge_other in existing_tables:
                #         try:
                #             migrate_query = text(f"""
                #                 INSERT OR IGNORE INTO mysafety_watchlist (신고번호)
                #                 SELECT 신고번호 FROM {settings.table_merge_traffic} WHERE 감시목록 = 'Y'
                #                 UNION
                #                 SELECT 신고번호 FROM {settings.table_merge_other} WHERE 감시목록 = 'Y'
                #             """)
                #             connection.execute(migrate_query)
                #             logger.LoggerFactory.logbot.info("기존 감시목록 데이터를 완벽하게 이관했습니다.")
                #         except Exception as e:
                #             logger.LoggerFactory.logbot.error(f"감시목록 데이터 이관 중 오류 발생: {e}")
            # [이전 DB 업데이트 비활성 — 2026-09-26 초기화 크롤링 릴리스] 있는 표에 빠진 열 추가
            # else:
            #     existing_columns = [col['name'] for col in inspector.get_columns(table.name)]
            #     for column in table.columns:
            #         if column.name not in existing_columns:
            #             logger.LoggerFactory.logbot.warning(f"'{table.name}' 테이블에 '{column.name}' 컬럼을 추가합니다.")
            #             column_type = column.type.compile(engine.dialect)
            #             alter_query = text(f'ALTER TABLE {table.name} ADD COLUMN {column.name} {column_type}')
            #             try:
            #                 connection.execute(alter_query)
            #             except Exception as e:
            #                 logger.LoggerFactory.logbot.error(f"스키마 업그레이드 오류: {e}")
        for statement in _index_statements():
            connection.execute(text(statement))
        if not existing_tables:
            # 새 DB: 지금 스키마로 만들었으니 버전만 적는다(마이그레이션을 돌리지 않는다).
            connection.execute(text(f"PRAGMA user_version = {SCHEMA_VERSION}"))
        connection.commit()

    # [이전 DB 업데이트 비활성 — 2026-09-26 초기화 크롤링 릴리스]
    # _apply_versioned_migrations(engine)
    if not maintenance:
        return
    # [이전 DB 업데이트 비활성 — 2026-09-26 초기화 크롤링 릴리스] 옛 형식 자료 정리(재분류·synced_at 백필·상태 정규화)
    # migrate_by_entry_value(engine)
    # backfill_synced_at(engine)
    # normalized_rows = _normalize_processing_layers(engine)
    # if normalized_rows:
    #     merge_final(engine)
    # else:
    #     _refresh_duplicate_groups(engine)
    _refresh_duplicate_groups(engine)


# 서버 DB 스키마 버전(PRAGMA user_version). contracts/storage-contract.json 의 schema_version.server 와 같아야 한다.
# 위의 열 추가식 upgrade 는 그대로 두고, 이후 데이터 이동이 필요한 변경은 번호 붙은 단계로 쌓는다(저장 계층 재설계 R1).
SCHEMA_VERSION = 4


def _migration_1_storage_tables(conn):
    """R1: 수정값·중복 판단·변경 기록 표. 표 생성은 위 metadata 경로가 하므로 여기서는 확인만 한다."""
    names = {row[0] for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
    missing = {"mysafety_report_override", "mysafety_duplicate_decision", "mysafety_change_log", "mysafety_change_cursor"} - names
    if missing:
        raise RuntimeError(f"스키마 버전 1 표 누락: {sorted(missing)}")


def _migration_2_sync_meta_and_watch_flags(conn):
    """R1b: sync_meta.value 를 NULL 허용으로(모바일과 같게, 표 재생성), 서버 sync_meta 의 감시목록 사본 삭제(원천은 mysafety_watchlist),
    title/merge 의 `감시목록` 열을 감시목록 표 기준으로 다시 계산."""
    conn.execute(text("CREATE TABLE mysafety_sync_meta_new (key VARCHAR NOT NULL PRIMARY KEY, value VARCHAR)"))
    conn.execute(text("INSERT INTO mysafety_sync_meta_new (key, value) SELECT key, value FROM mysafety_sync_meta WHERE key != 'watchlist'"))
    conn.execute(text("DROP TABLE mysafety_sync_meta"))
    conn.execute(text("ALTER TABLE mysafety_sync_meta_new RENAME TO mysafety_sync_meta"))
    refresh_watch_flags(conn)


def _migration_3_duplicate_decisions(conn):
    """R2c: 기존 중복군 행의 사용자 판단(상태·대표건 모드·대표건·메모)을 판단 표로 옮긴다. 이후 재생성은 판단 표를 우선한다."""
    conn.execute(text(
        "INSERT OR IGNORE INTO mysafety_duplicate_decision "
        "(group_id, status, representative_mode, representative_id, apply_globally, note, updated_at) "
        "SELECT group_id, status, representative_mode, representative_id, apply_globally, note, "
        "COALESCE(updated_at, created_at, 0) FROM mysafety_duplicate_group"
    ))


def _index_statements() -> list[str]:
    """자주 거르는 열의 인덱스(S-33). 신고번호(감시목록·별점·큐), 종결여부(재크롤링 대상), 중복 멤버의 신고 ID.
    스키마의 일부라 새 DB·--reset 뒤에도 upgrade_schema 가 매번(IF NOT EXISTS) 만든다."""
    statements = [
        'CREATE INDEX IF NOT EXISTS ix_mysafety_report_number ON mysafety ("신고번호")',
        'CREATE INDEX IF NOT EXISTS ix_duplicate_member_report ON mysafety_duplicate_member (report_id)',
    ]
    for category in ("traffic", "parking", "other"):
        statements.append(f'CREATE INDEX IF NOT EXISTS ix_merge_{category}_report_number ON mysafetymerge_{category} ("신고번호")')
        statements.append(f'CREATE INDEX IF NOT EXISTS ix_detail_{category}_closed ON mysafetydetail_{category} ("종결여부")')
    return statements


def _migration_4_indexes(conn):
    """R2d: 인덱스(_index_statements)."""
    for statement in _index_statements():
        conn.execute(text(statement))


_VERSIONED_MIGRATIONS = {
    1: _migration_1_storage_tables,
    2: _migration_2_sync_meta_and_watch_flags,
    3: _migration_3_duplicate_decisions,
    4: _migration_4_indexes,
}


def refresh_watch_flags(conn, report_numbers=None):
    """title·merge 의 `감시목록` 열 = mysafety_watchlist 에 있으면 'Y' 아니면 'N' (계산값, 계약 owner=derived)."""
    for table in (title_table, merge_traffic_table, merge_parking_table, merge_other_table):
        flag = text(
            f"UPDATE {table.name} SET 감시목록 = CASE WHEN 신고번호 IN (SELECT 신고번호 FROM mysafety_watchlist) THEN 'Y' ELSE 'N' END"
            + (" WHERE 신고번호 IN :numbers" if report_numbers else "")
        )
        if report_numbers:
            flag = flag.bindparams(bindparam("numbers", expanding=True))
            conn.execute(flag, {"numbers": list(report_numbers)})
        else:
            conn.execute(flag)


def get_schema_version(engine) -> int:
    with engine.connect() as conn:
        return int(conn.execute(text("PRAGMA user_version")).scalar() or 0)


def _refuse_newer_schema(engine):
    current = get_schema_version(engine)
    if current > SCHEMA_VERSION:
        # 더 새 서버가 만든 DB. 모르는 구조를 건드리지 않도록 upgrade 전에 멈춘다.
        raise RuntimeError(f"DB 스키마 버전 {current} 은 이 서버({SCHEMA_VERSION})보다 새 버전입니다. 서버를 업데이트하세요.")


# ── 이전 버전 DB (2026-09-26 초기화 크롤링 릴리스) ─────────────────────────────
# 이번 릴리스는 이전 DB 를 새 구조로 옮기지 않는다(upgrade_schema 의 업데이트 로직 주석 처리). 이 버전보다 낮은 DB 는
# 서버 시작 때 reset_legacy_database() 가 통째로 백업한 뒤 신고 자료를 비우고, 초기화 크롤링이 안전신문고에서 다시 채운다.
# 가져오기(복원)는 거절한다(core/storage/exchange.py). 모바일 LocalDbService.resetLegacyDatabase 와 같은 규칙이다.

#: 구조가 그대로라 옮기는 코드 없이 남기는 표. admin_users·api_keys 는 서버 전용(관리자 로그인·모바일 연결),
#: 감시목록·지오코딩 캐시는 모바일과 같다(모바일은 sync_meta 의 watchlist 값과 geocode_cache 표).
LEGACY_KEEP_TABLES = ("admin_users", "api_keys", "mysafety_watchlist", "mysafety_geocode_cache")
#: 없어지면 서버에 들어갈 수 없거나 모바일 연결이 끊기는 표 — 구조가 다르면 비우지 않고 멈춘다.
LEGACY_REQUIRED_KEEP = ("admin_users", "api_keys")
LEGACY_RESET_META_KEY = "legacy_reset"
LEGACY_BACKUP_PREFIX = "legacy_v"


class LegacyDatabase(RuntimeError):
    """이번 버전보다 낮은 스키마의 DB. 이번 릴리스는 옮기지 않는다."""


def _user_tables(conn) -> list[str]:
    return [row[0] for row in conn.execute(text(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"))]


def is_legacy_database(engine) -> bool:
    """표가 하나라도 있는데 스키마 버전이 이 서버보다 낮으면 True(새 DB·이미 최신은 False)."""
    with engine.connect() as conn:
        version = int(conn.execute(text("PRAGMA user_version")).scalar() or 0)
        return version < SCHEMA_VERSION and bool(_user_tables(conn))


def _refuse_legacy_schema(engine):
    if is_legacy_database(engine):
        raise LegacyDatabase(
            f"DB 스키마 버전 {get_schema_version(engine)} 은 이 서버({SCHEMA_VERSION})보다 이전 버전입니다. "
            "이번 업데이트는 이전 DB 를 옮기지 않습니다 — 초기화 크롤링으로 다시 수집하세요.")


def _keepable(conn, name: str) -> bool:
    """남길 표의 열(이름·타입·NOT NULL·기본키)이 지금 스키마와 정확히 같을 때만 True."""
    table = metadata.tables[name]
    actual = [(r[1], str(r[2]).upper(), bool(r[3]), bool(r[5]))
              for r in conn.exec_driver_sql(f'PRAGMA table_info("{name}")')]
    expected = [(c.name, str(c.type.compile(dialect=conn.dialect)).upper(), not c.nullable or c.primary_key, c.primary_key)
                for c in table.columns]
    return actual == expected


def _backup_file_db(db_path: str, target: str) -> None:
    """sqlite backup API 로 복사(WAL 에만 있던 내용 포함) + integrity_check. 실패하면 사본을 지우고 예외."""
    import sqlite3

    source = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    dest = sqlite3.connect(target)
    try:
        source.backup(dest)
    finally:
        dest.close()
        source.close()
    check = sqlite3.connect(target)
    try:
        result = check.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        check.close()
    if result != "ok":
        try:
            os.remove(target)
        except OSError:
            pass
        raise RuntimeError(f"이전 DB 백업 무결성 검사 실패: {result}")


def reset_legacy_database(engine, backup_dir: str, *, before_reset=None) -> dict | None:
    """이전 버전 DB 면: 통째로 백업 → (before_reset 호출) → 한 트랜잭션으로 남길 표 외 전부 지우고 지금 스키마로 다시 만든다.
    반환: {from_version, backup, kept, dropped, at} (이전 버전 DB 가 아니면 None). 백업이 실패하면 아무것도 지우지 않고 예외.

    남기는 표는 LEGACY_KEEP_TABLES 중 구조가 지금과 같은 것. 관리자·API 키 표의 구조가 다르면 지우지 않고 멈춘다.
    결과는 sync_meta[legacy_reset] 에 남겨 초기화 크롤링 안내 화면이 보여 준다."""
    import json
    import sqlite3
    from sqlalchemy.schema import CreateIndex, CreateTable

    _refuse_newer_schema(engine)
    if not is_legacy_database(engine):
        return None
    db_path = engine.url.database
    if not db_path or db_path == ":memory:" or not os.path.exists(db_path):
        raise LegacyDatabase("파일이 아닌 이전 버전 DB 는 비울 수 없습니다.")
    from_version = get_schema_version(engine)
    with engine.connect() as conn:
        tables = _user_tables(conn)
        kept = [name for name in LEGACY_KEEP_TABLES if name in tables and _keepable(conn, name)]
    broken = [name for name in LEGACY_REQUIRED_KEEP if name in tables and name not in kept]
    if broken:
        raise LegacyDatabase(f"이전 DB 의 {', '.join(broken)} 표 구조가 달라 비우지 않았습니다. DB 를 그대로 두고 멈춥니다.")

    os.makedirs(backup_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = os.path.join(backup_dir, f"{LEGACY_BACKUP_PREFIX}{from_version}_{stamp}.db")
    _backup_file_db(db_path, backup)
    logger.LoggerFactory.logbot.info(f"[schema] 이전 버전 DB(v{from_version}) 백업: {backup}")
    if before_reset is not None:
        before_reset()

    dropped = [name for name in tables if name not in kept]
    at = datetime.now().isoformat(timespec="seconds")
    info = {"from_version": from_version, "backup": backup, "kept": kept, "dropped": dropped, "at": at}
    engine.dispose()
    dialect = engine.dialect
    conn = sqlite3.connect(db_path, isolation_level=None)  # 명시적 BEGIN — DDL 까지 한 트랜잭션(중간에 멈춰 반쯤 지운 DB 없음)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            views = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='view'")]
            for name in views:
                conn.execute(f'DROP VIEW "{name}"')
            for name in dropped:
                conn.execute(f'DROP TABLE "{name}"')
            for table in metadata.sorted_tables:
                if table.name in kept:
                    continue
                conn.execute(str(CreateTable(table).compile(dialect=dialect)))
                for index in table.indexes:
                    conn.execute(str(CreateIndex(index).compile(dialect=dialect)))
            for statement in _index_statements():
                conn.execute(statement)
            conn.execute(f"INSERT INTO {sync_meta_table.name} (key, value) VALUES (?, ?)",
                         (LEGACY_RESET_META_KEY, json.dumps(info, ensure_ascii=False)))
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    logger.LoggerFactory.logbot.warning(
        f"[schema] 이전 버전 DB(v{from_version})를 옮기지 않고 비웠습니다. 남긴 표: {kept}. 초기화 크롤링으로 다시 수집합니다.")
    return info


def legacy_reset_info(engine) -> dict | None:
    """reset_legacy_database 가 남긴 기록(없으면 None)."""
    import json

    try:
        with engine.connect() as conn:
            value = conn.execute(select(sync_meta_table.c.value).where(
                sync_meta_table.c.key == LEGACY_RESET_META_KEY)).scalar()
    except Exception:
        return None
    if not value:
        return None
    try:
        info = json.loads(value)
    except ValueError:
        return None
    return info if isinstance(info, dict) else None


PRE_UPGRADE_BACKUP_PREFIX = "before_schema_v"
PRE_UPGRADE_BACKUP_KEEP = 5


def backup_before_upgrade(engine, backup_dir: str) -> str | None:
    """스키마를 올리기 전 DB 사본. 반환: 만든 파일 경로(할 일이 없으면 None).

    파일 DB 이고, 표가 이미 있고(새로 만드는 빈 DB 가 아님), 버전이 코드보다 낮을 때만 만든다.
    SQLite backup API 로 복사하므로 WAL 에만 있던 내용도 들어간다. 이 접두어 파일은 최근 PRE_UPGRADE_BACKUP_KEEP 개만 남긴다.
    """
    import sqlite3

    db_path = engine.url.database
    if not db_path or db_path == ":memory:" or not os.path.exists(db_path):
        return None
    current = get_schema_version(engine)
    if current >= SCHEMA_VERSION:
        return None
    with engine.connect() as conn:
        has_tables = conn.execute(text("SELECT count(*) FROM sqlite_master WHERE type='table'")).scalar()
    if not has_tables:
        return None
    os.makedirs(backup_dir, exist_ok=True)
    target = os.path.join(backup_dir, f"{PRE_UPGRADE_BACKUP_PREFIX}{current}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db")
    source = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    dest = sqlite3.connect(target)
    try:
        source.backup(dest)
    finally:
        dest.close()
        source.close()
    logger.LoggerFactory.logbot.info(f"[schema] 버전 {current} → {SCHEMA_VERSION} 올리기 전 DB 백업: {target}")
    old = sorted(f for f in os.listdir(backup_dir) if f.startswith(PRE_UPGRADE_BACKUP_PREFIX) and f.endswith(".db"))
    for name in old[:-PRE_UPGRADE_BACKUP_KEEP]:
        try:
            os.remove(os.path.join(backup_dir, name))
        except OSError:
            pass
    return target


def _apply_versioned_migrations(engine):
    current = get_schema_version(engine)
    for version in range(current + 1, SCHEMA_VERSION + 1):
        with engine.begin() as conn:
            _VERSIONED_MIGRATIONS[version](conn)
            conn.execute(text(f"PRAGMA user_version = {version}"))
        logger.LoggerFactory.logbot.info(f"[schema] DB 스키마 버전 {version} 적용")

def _get_title_ids_for_scan(conn, *, message: str):
    logger.LoggerFactory.logbot.info(message)
    query = select(title_table.c.ID)
    return pd.read_sql_query(query, conn)

def _get_new_and_incomplete_ids(conn):
    logger.LoggerFactory.logbot.info("신규, 미종결 신고 건 스캔 시작")
    query_new = select(title_table.c.ID).where(
        ~exists().where(title_table.c.ID == detail_traffic_table.c.ID)
    ).where(
        ~exists().where(title_table.c.ID == detail_parking_table.c.ID)
    ).where(
        ~exists().where(title_table.c.ID == detail_other_table.c.ID)
    )

    incomplete_queries = [
        # 종결여부 NULL(모름)도 미종결로 본다. `!= 'Y'` 만 쓰면 NULL 행이 영영 빠진다(S-19).
        select(t.c.ID).where(func.coalesce(t.c.종결여부, '') != 'Y')
        for t in (detail_traffic_table, detail_parking_table, detail_other_table)
    ]

    df_new = pd.read_sql_query(query_new, conn)
    df_incomplete = pd.concat(
        [pd.read_sql_query(q, conn) for q in incomplete_queries],
        ignore_index=True,
    )

    merged = pd.concat([
        df_new, df_incomplete,
    ]).drop_duplicates()
    return merged

def _labels_differ(left, right) -> bool:
    """목록 라벨 비교. None 을 != 로 직접 비교하지 않는다(계약 list-refetch-v1)."""
    if left is None or right is None:
        return (left is None) != (right is None)
    return str(left) != str(right)


def should_refetch_list_item(*, in_personal_detail: bool, list_label,
                             detail_status_label, closed, supplement_open,
                             in_capture_retry: bool = False,
                             rebuild_failed_permanent: bool = False,
                             failed_list_label=None) -> bool:
    """contracts/community-ingest/vectors/list_refetch.json 규칙 그대로.

    closed/supplement_open 은 detail 표 원본(사용자 override 무시 — 호출자가
    detail 표에서 읽어 넘긴다). 처리상태 canonical 은 비교하지 않는다.
    """
    if not in_personal_detail:
        return True
    if (closed or "") != "Y":
        return True
    if (supplement_open or "") == "Y":
        return True
    if in_capture_retry:
        return True
    if detail_status_label is None:
        if rebuild_failed_permanent and not _labels_differ(list_label, failed_list_label):
            return False
        return True
    return _labels_differ(list_label, detail_status_label)


def _community_detail_status_labels() -> dict:
    """community.db detail_status {report_id: c_now_label}. 실패하면 빈 dict."""
    try:
        from services.community_store import CommunityStore
        store = CommunityStore.open()
        dataset_id = store.local_dataset_id()
        rows = store.connect().execute(
            "SELECT source_report_id, c_now_label FROM detail_status WHERE local_dataset_id=?",
            (dataset_id,)).fetchall()
        return {row["source_report_id"]: row["c_now_label"] for row in rows}
    except Exception:
        return {}


def _community_rebuild_permanent_labels() -> dict:
    """마지막 rebuild 의 failed_permanent {report_id: last_list_label}. 실패하면 빈 dict."""
    try:
        from services.community_store import CommunityStore
        store = CommunityStore.open()
        rows = store.connect().execute(
            "SELECT i.source_report_id AS rid, i.last_list_label AS label, j.updated_at AS updated"
            " FROM rebuild_items i JOIN rebuild_jobs j ON j.run_id=i.run_id"
            " WHERE i.state='failed_permanent' ORDER BY j.updated_at DESC").fetchall()
        labels = {}
        for row in rows:
            labels.setdefault(row["rid"], row["label"])
        return labels
    except Exception:
        return {}


def _community_capture_retry_ids() -> set:
    """T4 community_capture.capture_retry_ids(). 없으면 빈 집합(파일 직접 읽기 금지)."""
    try:
        from services import community_capture as capture
        fn = getattr(capture, "capture_retry_ids", None)
        if fn is None:
            return set()
        return set(fn() or set())
    except Exception:
        return set()


def _list_refetch_extra_ids(conn) -> set:
    """list_refetch 벡터로 다시 읽을 ID. detail 표 원본만 본다(override 무시)."""
    title_rows = conn.execute(select(title_table.c.ID, title_table.c.상태)).fetchall()
    if not title_rows:
        return set()
    detail_site: dict = {}
    for table in (detail_traffic_table, detail_parking_table, detail_other_table):
        for row in conn.execute(
                select(table.c.ID, table.c.종결여부, table.c.보완_미응답)).fetchall():
            detail_site.setdefault(str(row[0]), (row[1], row[2]))
    status_labels = _community_detail_status_labels()
    permanent_labels = _community_rebuild_permanent_labels()
    retry_ids = _community_capture_retry_ids()
    extra = set()
    for report_id, list_label in title_rows:
        key = str(report_id)
        in_detail = key in detail_site
        closed, supplement = detail_site.get(key, (None, None))
        if should_refetch_list_item(
                in_personal_detail=in_detail, list_label=list_label,
                detail_status_label=status_labels.get(key),
                closed=closed, supplement_open=supplement,
                in_capture_retry=(key in retry_ids),
                rebuild_failed_permanent=(key in permanent_labels),
                failed_list_label=permanent_labels.get(key)):
            extra.add(report_id)
    return extra


def get_pending_detail_ids(engine, force=False):
    with engine.connect() as conn:
        if force:
            df = _get_title_ids_for_scan(conn, message="전체 신고 건을 다시 스캔합니다.")
        else:
            detail_rows = sum(
                conn.execute(select(func.count()).select_from(t)).scalar()
                for t in (detail_traffic_table, detail_parking_table, detail_other_table)
            )
            if detail_rows == 0:  # 주정차 표도 센다(S-19)
                df = _get_title_ids_for_scan(conn, message="detail 테이블 비어 있어 전체 스캔 시작")
            else:
                df = _get_new_and_incomplete_ids(conn)
                try:
                    # 목록 상태 변경 재조회(벡터 list-refetch-v1) — 기존 후보와 합집합.
                    extra = _list_refetch_extra_ids(conn)
                    if extra:
                        df = pd.concat([df, pd.DataFrame({"ID": list(extra)})],
                                       ignore_index=True).drop_duplicates()
                except Exception as exc:
                    logger.LoggerFactory.logbot.warning(f"목록 재조회 선정 생략: {exc}")
        
        if df.empty:
            return []

        df_sorted = df.sort_values(by='ID', ascending=True)
        detaillist = df_sorted['ID'].tolist()
        logger.LoggerFactory.logbot.debug("스캔대상 ID 리스트화 완료")
        logger.LoggerFactory.logbot.info(f"스캔대상 ID 총 {len(detaillist)}건")
        return detaillist


def title_to_sql(dataframes, engine, conn=None):
    if not dataframes:
        return []

    combined_df = pd.concat(dataframes, ignore_index=True)
    if combined_df.empty:
        return []

    if '만족도조사여부' not in combined_df.columns:
        combined_df['만족도조사여부'] = ""
    if '감시목록' not in combined_df.columns:
        combined_df['감시목록'] = "N"

    incoming_ids = combined_df['ID'].tolist()
    new_report_numbers = []

    # SQLite SQLITE_MAX_VARIABLE_NUMBER 한계 대응 (구버전 999, 신버전 32766)
    # mysafety 테이블 컬럼 수 = 7 → 배치당 최대 100행 (700 변수)
    TITLE_COLS = 7
    BATCH_SIZE = 100
    ID_CHUNK = 500  # in_() 쿼리용

    with engine.connect() as conn:
        # 기존 ID 조회 - in_() 변수 한계 대응을 위해 청크로 분할
        existing_ids = set()
        for i in range(0, len(incoming_ids), ID_CHUNK):
            chunk_ids = incoming_ids[i:i + ID_CHUNK]
            q = select(title_table.c.ID).where(title_table.c.ID.in_(chunk_ids))
            chunk_result = pd.read_sql(q, conn)
            existing_ids.update(chunk_result['ID'].tolist())

        new_df = combined_df[~combined_df['ID'].isin(existing_ids)]
        if not new_df.empty:
            new_report_numbers = new_df['신고번호'].tolist()

        from sqlalchemy import case as sa_case
        records = combined_df.to_dict('records')

        # 배치 단위로 upsert (too many SQL variables 방지)
        for i in range(0, len(records), BATCH_SIZE):
            batch = records[i:i + BATCH_SIZE]
            insert_stmt = insert(title_table).values(batch)
            # 만족도조사여부: 새 값이 비어있으면 기존 값을 유지 (재크롤링 시 덮어쓰기 방지)
            # 새 값이 비었으면 유지, '참여 완료' → 다른 값으로 되돌리기는 금지(결정 D-3, S-26).
            # 확정 미참여 재분류는 상세 저장(만족도 조회 결과)만 할 수 있다.
            poll_update = sa_case(
                (func.coalesce(insert_stmt.excluded.만족도조사여부, '') == '', title_table.c.만족도조사여부),
                (title_table.c.만족도조사여부 == '참여 완료', title_table.c.만족도조사여부),
                else_=insert_stmt.excluded.만족도조사여부
            )
            # 식별 정보(상태·신고번호·신고명·신고일)는 빈 값으로 덮지 않는다 — 상세 저장(reports_repo)·모바일
            # updateTitlesFromList 와 같은 규칙(2026-09-25 동등성 검수).
            def keep_if_empty(column):
                return sa_case(
                    (func.coalesce(getattr(insert_stmt.excluded, column), '') == '', getattr(title_table.c, column)),
                    else_=getattr(insert_stmt.excluded, column),
                )

            update_dict = {
                '상태': keep_if_empty('상태'),
                '신고번호': keep_if_empty('신고번호'),
                '신고명': keep_if_empty('신고명'),
                '신고일': keep_if_empty('신고일'),
                '만족도조사여부': poll_update,
            }
            upsert_query = insert_stmt.on_conflict_do_update(
                index_elements=['ID'],
                set_=update_dict
            )
            conn.execute(upsert_query)
        conn.commit()

    logger.LoggerFactory.logbot.info(f"총 {len(combined_df)}건 title 테이블 upsert 완료. (신규: {len(new_report_numbers)}건)")
    return new_report_numbers

_TITLE_STATUS_FROM_PROGRESS = {
    '답변완료': '답변완료',
    '수용':     '답변완료',  # 레거시 HTML '진행상황' 텍스트
    '불수용':   '불수용',
    '일부수용': '일부수용',
    '기타':     '기타',
    '취하':     '취하',
    '이송':     '이송',
}

def detail_to_sql(dataframes_with_category, engine, conn=None):
    """크롤러 튜플(2~7개 원소) → core/storage/reports_repo.save_crawled. 반환: 변경 목록 [{'id', 'change_type'}].

    저장 규칙(열 주인·트랜잭션·변경 판정)은 reports_repo 에 있다(저장 계층 재설계 R2).
    """
    from core.storage import reports_repo

    records = []
    for item in dataframes_with_category or []:
        try:
            records.append(reports_repo.CrawledDetail.from_legacy_tuple(item))
        except ValueError:
            continue
    if not records:
        return []
    result = reports_repo.save_crawled(engine, records)
    logger.LoggerFactory.logbot.info(
        f"총 {result.saved}건 detail 저장 완료. (변경/신규: {len(result.changed)}건, 실패: {len(result.failed)}건)"
    )
    return result.changed


def merge_final(engine, conn=None, *, track_duplicate_changes: bool = False):
    """화면용 표 전체 재생성. 1건 저장과 같은 규칙(reports_repo.refresh_merge_rows): 상세 + 수정값 + 감시목록 + 6개월 첨부 가림."""
    from core.storage import reports_repo

    with engine.connect() as conn:
        reports_repo.refresh_merge_rows(conn)
        conn.commit()
        logger.LoggerFactory.logbot.info("최종 데이터 병합 완료 (Traffic/Parking/Other 분리)")
    return _refresh_duplicate_groups(engine, track_changes=track_duplicate_changes)

def load_results(engine, conn=None):
    """전체 카테고리 합본 (레거시 호환). 새 코드는 load_results_by_category 사용 권장."""
    cats = load_results_by_category(engine)
    parts = [df for df in cats.values() if not df.empty]
    return pd.concat(parts) if parts else pd.DataFrame()


def load_results_by_category(engine):
    """카테고리별 분리 결과. 엑셀/구글시트 시트별 저장용.
    반환: {"교통위반": df_t, "주정차위반": df_p, "기타위반": df_o}"""
    with engine.connect() as conn:
        df_watch = pd.read_sql_query(select(watchlist_table.c.신고번호), conn)
        watch_ids = set(df_watch['신고번호'].tolist())

        result = {}
        for label, t in [("교통위반", merge_traffic_table),
                         ("주정차위반", merge_parking_table),
                         ("기타위반", merge_other_table)]:
            df = pd.DataFrame(pd.read_sql_query(select(t), conn))
            if not df.empty:
                df['감시목록'] = df['신고번호'].apply(lambda x: 'Y' if x in watch_ids else 'N')
                if settings.exclude_withdraw:
                    df = df[df['처리상태'] != '취하']
                if settings.normalize_police and '처리기관' in df.columns:
                    df['처리기관'] = df['처리기관'].apply(normalize_police_agency)
            result[label] = df
        return result

def get_merged_records_by_report_numbers(engine, report_numbers):
    """신고번호(SPP-…)로 병합 표 행을 읽는다. 별점 작업은 신고번호로 움직인다."""
    if not report_numbers:
        return []
    res = []
    with engine.connect() as conn:
        for t in [merge_traffic_table, merge_parking_table, merge_other_table]:
            result = conn.execute(select(t).where(t.c["신고번호"].in_(list(report_numbers))))
            col_names = result.keys()
            res.extend(dict(zip(col_names, row)) for row in result.fetchall())
    return res


def get_merged_records_by_ids(engine, id_list):
    if not id_list:
        return []
    res = []
    with engine.connect() as conn:
        for t in [merge_traffic_table, merge_parking_table, merge_other_table]:
            query = select(t).where(t.c.ID.in_(id_list))
            result = conn.execute(query)
            rows = result.fetchall()
            if rows:
                col_names = result.keys()
                res.extend([dict(zip(col_names, row)) for row in rows])
    return res

def search_by_car_number(engine, car_number: str):
    res = []
    with engine.connect() as conn:
        for t in [merge_traffic_table, merge_parking_table, merge_other_table]:
            query = select(t).where(t.c.차량번호.like(f"%{car_number}%"))
            result = conn.execute(query)
            rows = result.fetchall()
            if rows:
                col_names = result.keys()
                res.extend([dict(zip(col_names, row)) for row in rows])
    return res

def search_by_report_number(engine, report_number: str):
    res = []
    with engine.connect() as conn:
        for t in [merge_traffic_table, merge_parking_table, merge_other_table]:
            query = select(t).where(t.c.신고번호.like(f"%{report_number}%"))
            result = conn.execute(query)
            rows = result.fetchall()
            if rows:
                col_names = result.keys()
                res.extend([dict(zip(col_names, row)) for row in rows])
    return res

# ── 관리자 계정 CRUD ─────────────────────────────────────────────────────────

def has_admin_user(engine) -> bool:
    with engine.connect() as conn:
        count = conn.execute(select(func.count()).select_from(admin_users_table)).scalar()
        return count > 0


def get_admin_user(engine, username: str):
    with engine.connect() as conn:
        result = conn.execute(
            select(admin_users_table).where(admin_users_table.c.username == username)
        ).first()
        return dict(result._mapping) if result else None


def create_admin_user(engine, username: str, password: str):
    from core.utils.security import hash_password
    salt, pwd_hash = hash_password(password)
    with engine.begin() as conn:
        conn.execute(admin_users_table.insert().values(
            username=username, password_hash=pwd_hash, salt=salt
        ))


def update_admin_user(engine, old_username: str, new_username: str, new_password: str):
    from core.utils.security import hash_password
    salt, pwd_hash = hash_password(new_password)
    with engine.begin() as conn:
        conn.execute(
            update(admin_users_table)
            .where(admin_users_table.c.username == old_username)
            .values(username=new_username, password_hash=pwd_hash, salt=salt)
        )


def sync_rating_status(engine, report_id, status_str="참여 완료", *, score=None, cause=None):
    """별점 제출·확인 결과를 목록(title)에 기록하고 그 신고의 화면용 표를 다시 만든다(S-25, 결정 D-2).
    report_id 는 신고번호. score/cause 가 주어질 때만 별점·별점사유를 바꾼다(모바일 updateReportRatingByNumber 와 같음)."""
    from core.storage import reports_repo

    values = {"만족도조사여부": status_str}
    if score is not None:
        values["별점"] = int(score)
    if cause is not None:
        values["별점사유"] = cause
    with engine.begin() as conn:
        conn.execute(update(title_table).where(title_table.c.신고번호 == report_id).values(**values))
        ids = conn.execute(select(title_table.c.ID).where(title_table.c.신고번호 == report_id)).scalars().all()
        if ids:
            reports_repo.refresh_merge_rows(conn, ids)


# ── API Key CRUD ──────────────────────────────────────────────────────────────

def create_api_key(engine, name: str) -> str:
    import uuid
    key = "sk-" + uuid.uuid4().hex
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with engine.begin() as conn:
        conn.execute(api_keys_table.insert().values(key=key, name=name, created_at=created_at))
    return key


def get_all_api_keys(engine) -> list:
    with engine.connect() as conn:
        result = conn.execute(select(api_keys_table).order_by(api_keys_table.c.created_at.desc()))
        return [dict(row._mapping) for row in result]


def delete_api_key(engine, key: str):
    with engine.begin() as conn:
        conn.execute(api_keys_table.delete().where(api_keys_table.c.key == key))


def validate_api_key(engine, key: str) -> bool:
    with engine.connect() as conn:
        result = conn.execute(
            select(api_keys_table).where(api_keys_table.c.key == key)
        ).first()
        return result is not None

def get_api_key_name(engine, key: str) -> str:
    with engine.connect() as conn:
        result = conn.execute(
            select(api_keys_table.c.name).where(api_keys_table.c.key == key)
        ).first()
        return result[0] if result else "알 수 없는 기기"
