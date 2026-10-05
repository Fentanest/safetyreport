import pandas as pd
from sqlalchemy import select, func, update, text, inspect, bindparam, or_
from sqlalchemy.dialects.sqlite import insert
from core.utils import logger
from core.utils.fallback import note_fallback
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
                except Exception as exc:
                    note_fallback("database.find_detail_table", exc)
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
    감시목록 열 이관, entry_value 재분류, synced_at 백필, 상태 정규화, 업그레이드 전 백업은 core/database/disabled_upgrade.py 에 보관했다.
    이 버전보다 낮은 DB 는 여기서 고치지 않고 LegacyDatabase 로 멈춘다 — 서버 시작은 reset_legacy_database() 로 백업 뒤 비우고
    초기화 크롤링이 다시 채운다. 복원(가져오기)은 거절한다. backup_dir 은 호출 호환용으로만 받는다."""
    _refuse_newer_schema(engine)
    _refuse_legacy_schema(engine)
    # 이전 DB 업데이트(업그레이드 전 백업)는 비활성 — core/database/disabled_upgrade.py ①
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
                # 옛 merge 표 감시목록 열 이관(비활성) — core/database/disabled_upgrade.py ②
            # 있는 표에 빠진 열 추가(비활성) — core/database/disabled_upgrade.py ③
        for statement in _index_statements():
            connection.execute(text(statement))
        if not existing_tables:
            # 새 DB: 지금 스키마로 만들었으니 버전만 적는다(마이그레이션을 돌리지 않는다).
            connection.execute(text(f"PRAGMA user_version = {SCHEMA_VERSION}"))
        connection.commit()

    # 번호 붙은 마이그레이션(비활성) — core/database/disabled_upgrade.py ④
    if not maintenance:
        return
    # 옛 형식 자료 정리(재분류·synced_at 백필·상태 정규화, 비활성) — core/database/disabled_upgrade.py ⑤
    _refresh_duplicate_groups(engine)


# 서버 DB 스키마 버전(PRAGMA user_version). contracts/storage-contract.json 의 schema_version.server 와 같아야 한다.
# 위의 열 추가식 upgrade 는 그대로 두고, 이후 데이터 이동이 필요한 변경은 번호 붙은 단계로 쌓는다(저장 계층 재설계 R1).
SCHEMA_VERSION = 5


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
        statements.append(f'CREATE INDEX IF NOT EXISTS ix_merge_{category}_answer ON mysafetymerge_{category} ("답변일", synced_at, "신고번호")')
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


def _keepable(conn, name: str, dialect) -> bool:
    """남길 표의 열(이름·타입·NOT NULL·기본키)이 지금 스키마와 정확히 같을 때만 True. conn 은 sqlite3 연결."""
    table = metadata.tables[name]
    actual = [(r[1], str(r[2]).upper(), bool(r[3]), bool(r[5]))
              for r in conn.execute(f'PRAGMA table_info("{name}")')]
    expected = [(c.name, str(c.type.compile(dialect=dialect)).upper(), not c.nullable or c.primary_key, c.primary_key)
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


#: 다른 프로세스가 비우는 중이면 쓰기 잠금을 이만큼 기다린다(백업 복사 시간 포함).
LEGACY_RESET_LOCK_TIMEOUT = 300.0


def reset_legacy_database(engine, backup_dir: str, *, before_reset=None) -> dict | None:
    """이전 버전 DB 면: 쓰기 잠금(BEGIN IMMEDIATE)을 먼저 잡고 그 안에서 버전을 다시 확인한 뒤 → 통째로 백업 → (before_reset 호출)
    → 남길 표 외 전부 지우고 지금 스키마로 다시 만든다 → COMMIT. 반환: {from_version, backup, kept, dropped, at}
    (이전 버전 DB 가 아니거나 다른 프로세스가 먼저 끝냈으면 None). 백업·before_reset 이 실패하면 아무것도 지우지 않고 예외.

    잠금 안에서 다시 확인하므로 서버를 동시에 두 번 띄워도 두 번째는 이미 비운(그 뒤 수집이 시작됐을 수 있는) DB 를 다시 비우지 않는다.
    남기는 표는 LEGACY_KEEP_TABLES 중 구조가 지금과 같은 것. 관리자·API 키 표의 구조가 다르면 비우지 않고 멈춘다.
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
    dialect = engine.dialect
    engine.dispose()
    # 명시적 BEGIN — DDL 까지 한 트랜잭션(중간에 멈춰 반쯤 지운 DB 없음). 잠금을 잡은 채 백업한다(읽기는 막히지 않는다).
    conn = sqlite3.connect(db_path, isolation_level=None, timeout=LEGACY_RESET_LOCK_TIMEOUT)
    backup = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            from_version = int(conn.execute("PRAGMA user_version").fetchone()[0] or 0)
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
            if from_version > SCHEMA_VERSION:
                raise RuntimeError(f"DB 스키마 버전 {from_version} 은 이 서버({SCHEMA_VERSION})보다 새 버전입니다. 서버를 업데이트하세요.")
            if from_version == SCHEMA_VERSION or not tables:
                conn.execute("ROLLBACK")  # 다른 프로세스가 먼저 끝냈다
                return None
            kept = [name for name in LEGACY_KEEP_TABLES if name in tables and _keepable(conn, name, dialect)]
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
            views = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='view'")]
            for name in views:
                conn.execute(f'DROP VIEW "{name}"')
            # 가상 표(FTS 등)를 먼저 지운다 — 그 보조(shadow) 표가 함께 지워지므로 나머지는 IF EXISTS(Sol 재검증 6, 모바일과 같음).
            virtual = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND sql LIKE 'CREATE VIRTUAL TABLE%'")]
            for name in virtual:
                if name in dropped:
                    conn.execute(f'DROP TABLE "{name}"')
            for name in dropped:
                conn.execute(f'DROP TABLE IF EXISTS "{name}"')
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
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    logger.LoggerFactory.logbot.warning(
        f"[schema] 이전 버전 DB(v{from_version})를 옮기지 않고 비웠습니다. 남긴 표: {kept}. 초기화 크롤링으로 다시 수집합니다.")
    return info


# ── 카카오 계정(데이터 주인)과 로그아웃 초기화 (2026-09-27 사용자 결정) ─────────────────────────────────
#: 이 DB 의 주인인 카카오 회원번호(카카오가 준 숫자 ID 원문). 모바일 앱 DB sync_meta 와 같은 키 — 교환 때 그대로 옮겨진다.
#: 가져오기·복원은 이 값이 지금 로그인한 카카오 회원번호와 같은 DB 만 받는다(core/storage/exchange.refuse_foreign_owner).
KAKAO_MEMBER_META_KEY = "kakao_member_id"


def get_meta(engine, key: str) -> str | None:
    with engine.connect() as conn:
        return conn.execute(select(sync_meta_table.c.value).where(sync_meta_table.c.key == key)).scalar()


def set_meta(engine, key: str, value: str) -> None:
    with engine.begin() as conn:
        conn.execute(sync_meta_table.delete().where(sync_meta_table.c.key == key))
        conn.execute(sync_meta_table.insert().values(key=key, value=value))


def stamp_meta_if_missing(engine, key: str, value: str) -> str | None:
    """한 트랜잭션에서: 값이 있으면 그 값을 돌려주고 바꾸지 않는다, 없으면(NULL·빈 문자열 포함) value 를 적고 None."""
    with engine.begin() as conn:
        current = conn.execute(select(sync_meta_table.c.value).where(sync_meta_table.c.key == key)).scalar()
        if current:
            return current
        conn.execute(sync_meta_table.delete().where(sync_meta_table.c.key == key))
        conn.execute(sync_meta_table.insert().values(key=key, value=value))
        return None


def empty_report_data(engine, *, before_empty=None) -> dict:
    """카카오 로그아웃(또는 다른 카카오 계정으로 시작)할 때: 신고 자료만 비운다. 남기는 것은 이전 DB 초기화와 같다
    (LEGACY_KEEP_TABLES — 관리자·API 키·감시목록·지오코딩 캐시). sync_meta 도 비우므로 데이터 주인 표시(KAKAO_MEMBER_META_KEY)가
    지워지고, 다음 로그인 계정이 새 주인이 된다. 백업은 만들지 않는다(사용자에게 지운다고 알린 자료).

    쓰기 잠금(BEGIN IMMEDIATE) 한 트랜잭션 — 중간에 멈춰 반쯤 지운 DB 가 없다. before_empty 는 잠금 안에서 지우기 직전에 부른다
    (community.db 데이터셋 선회전). 호출자는 크롤링·지도 변환이 없고 이 프로세스의 쓰기가 막힌 상태에서 부른다(account_data.wipe_report_data)."""
    import sqlite3
    from sqlalchemy.schema import CreateIndex, CreateTable

    db_path = engine.url.database
    if not db_path or db_path == ":memory:" or not os.path.exists(db_path):
        raise RuntimeError("파일이 아닌 DB 는 비울 수 없습니다.")
    dialect = engine.dialect
    engine.dispose()
    conn = sqlite3.connect(db_path, isolation_level=None, timeout=LEGACY_RESET_LOCK_TIMEOUT)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
            kept = [name for name in LEGACY_KEEP_TABLES if name in tables and _keepable(conn, name, dialect)]
            broken = [name for name in LEGACY_REQUIRED_KEEP if name in tables and name not in kept]
            if broken:
                raise RuntimeError(f"{', '.join(broken)} 표 구조가 달라 비우지 않았습니다.")
            if before_empty is not None:
                before_empty()
            dropped = [name for name in tables if name not in kept]
            for name in [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='view'")]:
                conn.execute(f'DROP VIEW "{name}"')
            virtual = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND sql LIKE 'CREATE VIRTUAL TABLE%'")]
            for name in virtual:
                if name in dropped:
                    conn.execute(f'DROP TABLE "{name}"')
            for name in dropped:
                conn.execute(f'DROP TABLE IF EXISTS "{name}"')
            for table in metadata.sorted_tables:
                if table.name in kept:
                    continue
                conn.execute(str(CreateTable(table).compile(dialect=dialect)))
                for index in table.indexes:
                    conn.execute(str(CreateIndex(index).compile(dialect=dialect)))
            for statement in _index_statements():
                conn.execute(statement)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    logger.LoggerFactory.logbot.warning(f"[account] 신고 자료를 비웠습니다(카카오 로그아웃). 남긴 표: {kept}")
    return {"kept": kept, "dropped": dropped}


def legacy_reset_info(engine) -> dict | None:
    """reset_legacy_database 가 남긴 기록(없으면 None)."""
    import json

    try:
        with engine.connect() as conn:
            value = conn.execute(select(sync_meta_table.c.value).where(
                sync_meta_table.c.key == LEGACY_RESET_META_KEY)).scalar()
    except Exception as exc:
        note_fallback("database.legacy_reset_info", exc)
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


def repair_stale_merge(engine, *, raise_on_error: bool = False) -> bool:
    """기동/복원 시 오래된 화면용 표만 재생성. 기동 실패 로그에는 개인정보를 넣지 않는다."""
    from core.storage import reports_repo

    try:
        with engine.connect() as conn:
            drift = reports_repo.merge_drift(conn)
        if not drift:
            return False
        merge_final(engine)
        logger.LoggerFactory.logbot.info(f"[merge] ID/기관코드 불일치 재생성 완료: {drift}")
        return True
    except Exception as exc:
        logger.LoggerFactory.logbot.warning(f"[merge] 화면용 표 검사/재생성 실패: {type(exc).__name__}")
        if raise_on_error:
            raise
        return False


def merge_final(engine, conn=None, *, track_duplicate_changes: bool = False):
    """화면용 표 전체 재생성. 1건 저장과 같은 규칙(reports_repo.refresh_merge_rows): 상세 + 수정값 + 감시목록 + 6개월 첨부 가림."""
    from core.storage import reports_repo

    with engine.connect() as conn:
        reports_repo.refresh_merge_rows(conn)
        conn.commit()
        logger.LoggerFactory.logbot.info("최종 데이터 병합 완료 (Traffic/Parking/Other 분리)")
    return _refresh_duplicate_groups(engine, track_changes=track_duplicate_changes)

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
    # 사이트가 별점을 확인한 뒤 다음 증분 수집에서 공식 상세를 반드시 다시 읽는다.
    # 개인 DB의 별점/사유를 공유 DTO로 승격하지 않고 상세 파서의 숫자만 캡처한다.
    if score is not None and ids:
        from services import community_capture
        for source_id in ids:
            community_capture.add_retry_id(str(source_id), "rating_confirmed_refetch")


# ── 분리한 모듈(EO R-06): 예전 이름으로 쓰는 호출자를 위해 다시 내보낸다 ──────────────────────────
from .accounts_repo import (  # noqa: E402,F401
    create_admin_user, create_api_key, create_first_admin_user, delete_api_key, get_admin_user, get_all_api_keys,
    get_api_key_name, has_admin_user, update_admin_user, validate_api_key,
)
from .report_reads import (  # noqa: E402,F401
    get_merged_records_by_ids, get_merged_records_by_report_numbers, load_results, load_results_by_category,
    search_by_car_number, search_by_report_number,
)


def get_pending_detail_ids(engine, force=False):
    """상세 수집 대상 ID(services.collection_policy). services 를 늦게 불러 순환 import 를 피한다."""
    from services import collection_policy

    return collection_policy.get_pending_detail_ids(engine, force)


def should_refetch_list_item(**kwargs) -> bool:
    from services import collection_policy

    return collection_policy.should_refetch_list_item(**kwargs)
