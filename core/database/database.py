import pandas as pd
from sqlalchemy import select, func, update, text, inspect, bindparam
from sqlalchemy.dialects.sqlite import insert
from core.utils import logger
from core.utils.fallback import note_fallback
import os
from datetime import datetime

from .models import (metadata, report_table, report_effective_table, report_body_table, report_detail_raw_table,
                     watchlist_table,
                     admin_users_table, api_keys_table, sync_meta_table, duplicate_group_table, duplicate_member_table)


def _current_epoch_millis() -> int:
    return int(datetime.now().timestamp() * 1000)


def _refresh_duplicate_groups(engine, *, track_changes: bool = False):
    try:
        from services import duplicate_group_service
        return duplicate_group_service.refresh_duplicate_groups(engine, track_changes=track_changes)
    except Exception as exc:
        logger.LoggerFactory.logbot.error(f"[duplicate] 중복군 재생성 실패: {exc}")
        return {"group_count": 0, "member_count": 0, "changes": []}


def upgrade_schema(engine, *, maintenance: bool = True, backup_dir: str | None = None):
    """표 만들기(새 DB)·빠진 표 만들기·인덱스, 그리고 maintenance=True 일 때(서버 시작·복원) 중복군 재계산.
    크롤링 서브프로세스는 maintenance=False 로 가볍게 부른다(S-22).

    스키마 올리기는 여기서 하지 않는다: 서버 시작이 먼저 reset_legacy_database()(버전 5 미만 — 백업 뒤 비우고 초기화 크롤링)와
    migrate_schema()(버전 5 → 6 제자리 변환)를 부른다. 그 뒤에도 이 서버보다 낮은 DB 면 LegacyDatabase 로 멈춘다.
    backup_dir 은 호출 호환용으로만 받는다."""
    _refuse_newer_schema(engine)
    _refuse_legacy_schema(engine)
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
        for statement in _index_statements():
            connection.execute(text(statement))
        if not existing_tables:
            # 새 DB: 지금 스키마로 만들었으니 버전만 적는다.
            connection.execute(text(f"PRAGMA user_version = {SCHEMA_VERSION}"))
        connection.commit()

    if not maintenance:
        return
    _refresh_duplicate_groups(engine)


# 서버 DB 스키마 버전(PRAGMA user_version). contracts/storage-contract.json 의 schema_version.server 와 같아야 한다.
# 6(2026-10-11): 신고 한 건 = mysafety_report 한 행, category 열(5분류), SSNPC 코드 열, 화면용 mysafety_report_effective.
# 7(2026-10-17): 상세 원본 표 mysafety_report_detail_raw 추가(명세 1-2). 자료를 그대로 두는 추가만 하는 제자리 변환.
SCHEMA_VERSION = 7
#: 이 버전부터는 제자리 변환(migrate_schema)으로 올린다. 더 낮은 DB 는 reset_legacy_database 로 백업 뒤 비운다.
IN_PLACE_FROM = 5
#: 제자리 변환 체인: 5 → 6 → 7. 6 미만 규칙 유지(5 미만은 백업 뒤 비우기).
MIGRATE_CHAIN = (5, 6)


def _index_statements() -> list[str]:
    """자주 거르는 열의 인덱스(S-33). 신고번호(감시목록·별점·큐), 분류·종결여부(재크롤링 대상), 답변일(화면 정렬),
    중복 멤버의 신고 ID. 스키마의 일부라 새 DB·--reset 뒤에도 upgrade_schema 가 매번(IF NOT EXISTS) 만든다."""
    return [
        'CREATE INDEX IF NOT EXISTS ix_report_number ON mysafety_report ("신고번호")',
        'CREATE INDEX IF NOT EXISTS ix_report_category_closed ON mysafety_report (category, "종결여부")',
        'CREATE INDEX IF NOT EXISTS ix_effective_category_number ON mysafety_report_effective (category, "신고번호")',
        'CREATE INDEX IF NOT EXISTS ix_effective_report_number ON mysafety_report_effective ("신고번호")',
        'CREATE INDEX IF NOT EXISTS ix_effective_category_number_id ON mysafety_report_effective (category, "신고번호", "ID")',
        'CREATE INDEX IF NOT EXISTS ix_effective_number_id ON mysafety_report_effective ("신고번호", "ID")',
        'CREATE INDEX IF NOT EXISTS ix_effective_answer ON mysafety_report_effective ("답변일", synced_at, "신고번호")',
        'CREATE INDEX IF NOT EXISTS ix_duplicate_member_report ON mysafety_duplicate_member (report_id)',
    ]


def refresh_watch_flags(conn, report_numbers=None):
    """신고·화면용 표의 `감시목록` 열 = mysafety_watchlist 에 있으면 'Y' 아니면 'N' (계산값, 계약 owner=derived)."""
    for table in (report_table, report_effective_table):
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


# ── 이전 버전 DB ────────────────────────────────────────────────────────────────
# 버전 5(2026-09-26 초기화 크롤링 릴리스)는 migrate_schema() 가 제자리 변환한다. 그보다 낮은 DB 는 서버 시작 때
# reset_legacy_database() 가 통째로 백업한 뒤 신고 자료를 비우고, 초기화 크롤링이 안전신문고에서 다시 채운다.
# 가져오기(복원)는 같은 버전만 받는다(core/storage/exchange.py). 모바일 LocalDbService 와 같은 규칙이다.

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


def needs_legacy_reset(engine) -> bool:
    """제자리 변환(IN_PLACE_FROM)보다 낮은 버전이라 백업 뒤 비워야 하는 DB 면 True."""
    with engine.connect() as conn:
        version = int(conn.execute(text("PRAGMA user_version")).scalar() or 0)
        return version < IN_PLACE_FROM and bool(_user_tables(conn))


def _refuse_legacy_schema(engine):
    if is_legacy_database(engine):
        raise LegacyDatabase(
            f"DB 스키마 버전 {get_schema_version(engine)} 은 이 서버({SCHEMA_VERSION})보다 이전 버전입니다. "
            "서버를 시작하면 먼저 변환(버전 5)하거나 백업 뒤 비웁니다(그보다 낮은 버전 — 초기화 크롤링으로 다시 수집).")


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
    if not needs_legacy_reset(engine):
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
            if from_version >= IN_PLACE_FROM or not tables:
                conn.execute("ROLLBACK")  # 다른 프로세스가 먼저 끝냈거나 제자리 변환 대상(migrate_schema)
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


#: 제자리 변환(migrate_schema) 전 백업 파일 이름 앞부분
PRE_UPGRADE_BACKUP_PREFIX = "before_schema_v"


# ── 스키마 5 → 6 제자리 변환 (2026-10-11) ──────────────────────────────────────
# 5: 목록 mysafety + 상세 mysafetydetail_{traffic,parking,other} + 화면용 mysafetymerge_* + mysafety_entry_value + mysafety_raw_content.
# 6: mysafety_report(한 건 한 행) + mysafety_report_body + mysafety_report_effective. 사용자 소유 표(수정값·중복 판단·감시목록)·
#    지오코딩 캐시·sync_meta·관리자·API 키·변경 기록은 그대로 둔다. 분류는 entry_value 로 다시 정하고(SSNPC 코드는 다음 수집이 채운다),
#    옮긴 값은 같은 트랜잭션 안에서 행 수·값 해시로 대조한 뒤에만 커밋한다.
_V5_DETAIL_TABLES = ("mysafetydetail_traffic", "mysafetydetail_parking", "mysafetydetail_other")
_V5_DROP = ("mysafety", *_V5_DETAIL_TABLES, "mysafetymerge_traffic", "mysafetymerge_parking", "mysafetymerge_other",
            "mysafety_entry_value", "mysafety_raw_content")


# 신고가 없는 옛 메뉴(entry_value) 행은 옮길 신고 행이 없다. 버리지 않고 sync_meta 이 키에 JSON([[ID, entry_value], …])으로
# 보존한다(Astra 재검수 #2, 2026-10-11). sync_meta 는 교환·백업이 그대로 나르는 표라 새 표를 만들지 않는다.
ORPHAN_ENTRY_VALUES_META_KEY = "legacy_orphan_entry_values"


class MigrationVerifyFailed(RuntimeError):
    """옮긴 값이 원본과 달라 변환을 되돌렸다. DB 는 그대로다."""


def _v5_rows(conn) -> list[dict]:
    """버전 5 DB 의 신고를 새 행 모양으로(분류 전). conn 은 sqlite3 연결(row_factory=Row)."""
    from .models import DETAIL_COLUMN_NAMES, TITLE_COLUMN_NAMES

    rows: dict[str, dict] = {}
    title_cols = ", ".join(f'"{c}"' for c in TITLE_COLUMN_NAMES)
    for r in conn.execute(f'SELECT "ID", {title_cols} FROM mysafety'):
        rows[r["ID"]] = {"ID": r["ID"], **{c: r[c] for c in TITLE_COLUMN_NAMES}, "category": None, "entry_value": None}
    detail_cols = ", ".join(f'"{c}"' for c in DETAIL_COLUMN_NAMES)
    for table in _V5_DETAIL_TABLES:
        for r in conn.execute(f'SELECT "ID", {detail_cols} FROM "{table}"'):
            row = rows.setdefault(r["ID"], {"ID": r["ID"], **{c: None for c in TITLE_COLUMN_NAMES}, "category": None,
                                            "entry_value": None})
            row.update({c: r[c] for c in DETAIL_COLUMN_NAMES})
            row["_old_category"] = table.removeprefix("mysafetydetail_")
    for r in conn.execute("SELECT ID, entry_value FROM mysafety_entry_value"):
        if r["ID"] in rows:
            rows[r["ID"]]["entry_value"] = r["entry_value"]
    return list(rows.values())


def _v5_preflight(conn) -> dict:
    """변환 전에 손실 위험을 본다(Astra 2026-10-11 지적 2). 멈춰야 하는 것은 MigrationVerifyFailed:
    - 값이 든 모르는 열(옮길 자리가 없어 사라진다)
    - 같은 ID 가 두 상세 표에 있음(한쪽 값이 다른 쪽을 덮는다)
    신고가 없는 entry_value 행(고아)은 옮길 신고가 없으므로 sync_meta 에 따로 보존한다(`_v5_orphan_entry_values`)."""
    from .models import DETAIL_COLUMN_NAMES, TITLE_COLUMN_NAMES

    expected = {
        "mysafety": {"ID", *TITLE_COLUMN_NAMES},
        **{t: {"ID", *DETAIL_COLUMN_NAMES} for t in _V5_DETAIL_TABLES},
        "mysafety_entry_value": {"ID", "entry_value"},
        "mysafety_raw_content": {"ID", "raw_content", "raw_type", "saved_at"},
    }
    unknown = []
    for table, known in expected.items():
        for col in [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]:
            if col in known:
                continue
            n = conn.execute(f'SELECT count(*) FROM "{table}" WHERE "{col}" IS NOT NULL').fetchone()[0]
            if n:
                unknown.append(f"{table}.{col}({n}행)")
    union = " UNION ALL ".join(f'SELECT ID FROM "{t}"' for t in _V5_DETAIL_TABLES)
    dup = [r[0] for r in conn.execute(f"SELECT ID FROM ({union}) GROUP BY ID HAVING count(*) > 1 LIMIT 5")]
    problems = []
    if unknown:
        problems.append("값이 든 모르는 열 " + ", ".join(unknown))
    if dup:
        problems.append("두 분류 상세 표에 같은 신고 " + ", ".join(map(str, dup)))
    if problems:
        raise MigrationVerifyFailed(f"스키마 5→6 변환 전 검사 실패(옮기면 값이 사라짐): {'; '.join(problems)}. DB 는 그대로입니다.")
    return {"orphan_entry_values": len(_v5_orphan_entry_values(conn))}


def _v5_orphan_entry_values(conn) -> list[list]:
    """목록·상세 어디에도 신고가 없는 mysafety_entry_value 행. [[ID, entry_value], …] (ID 순)."""
    union = " UNION ".join(f'SELECT ID FROM "{t}"' for t in ("mysafety", *_V5_DETAIL_TABLES))
    return [[r[0], r[1]] for r in conn.execute(
        f"SELECT ID, entry_value FROM mysafety_entry_value WHERE ID NOT IN ({union}) ORDER BY ID")]


def _preserve_orphan_entry_values(conn, orphans: list[list], dialect) -> None:
    """고아 메뉴 행을 sync_meta 에 쓴다. 같은 키가 이미 있으면 그 목록에 합친다(같은 ID 는 이번 값)."""
    import json
    from sqlalchemy.schema import CreateTable

    if not orphans:
        return
    conn.execute(str(CreateTable(sync_meta_table, if_not_exists=True).compile(dialect=dialect)))
    found = conn.execute("SELECT value FROM mysafety_sync_meta WHERE key = ?", (ORPHAN_ENTRY_VALUES_META_KEY,)).fetchone()
    merged = {row[0]: row for row in (json.loads(found[0]) if found and found[0] else [])}
    merged.update({row[0]: row for row in orphans})
    value = json.dumps([merged[k] for k in sorted(merged, key=str)], ensure_ascii=False)
    conn.execute("INSERT OR REPLACE INTO mysafety_sync_meta (key, value) VALUES (?, ?)", (ORPHAN_ENTRY_VALUES_META_KEY, value))


def _v5_source_checks(conn, new_by_id: dict) -> list[str]:
    """원본 표마다(목록·상세 3개·메뉴) 그 표의 행이 새 표의 같은 ID 행과 값까지 같은지. 어긋난 표 이름 목록."""
    from .models import DETAIL_COLUMN_NAMES, TITLE_COLUMN_NAMES

    sources = [("mysafety", ("ID", *TITLE_COLUMN_NAMES))] + [(t, ("ID", *DETAIL_COLUMN_NAMES)) for t in _V5_DETAIL_TABLES]
    bad = []
    for table, columns in sources:
        cols = ", ".join(f'"{c}"' for c in columns)
        old = [dict(r) for r in conn.execute(f'SELECT {cols} FROM "{table}"')]
        new = [new_by_id.get(r["ID"]) for r in old]
        if any(n is None for n in new) or _digest(old, columns) != _digest(new, columns):
            bad.append(table)
    old_ev = [dict(r) for r in conn.execute("SELECT ID, entry_value FROM mysafety_entry_value")
              if r["ID"] in new_by_id]
    if _digest(old_ev, ("ID", "entry_value")) != _digest([new_by_id[r["ID"]] for r in old_ev], ("ID", "entry_value")):
        bad.append("mysafety_entry_value")
    return bad


def _digest(rows, columns) -> str:
    import hashlib
    import json

    h = hashlib.sha256()
    for row in sorted(rows, key=lambda r: str(r["ID"])):
        h.update(json.dumps([row.get(c) for c in columns], ensure_ascii=False, default=str).encode("utf-8"))
    return h.hexdigest()


def _migrate_6_to_7(engine, backup_dir: str) -> dict | None:
    """버전 6 DB 에 상세 원본 표(mysafety_report_detail_raw)를 더해 7 로 올린다(명세 1-2·2절).

    추가만 하는 제자리 변환: 업그레이드 전 백업(`before_schema_v7_*.db`) → 새 표 생성 → 버전 기록.
    실패하면 DB 를 바꾸지 않는다(ROLLBACK). 자료는 그대로 둔다. 반환: {from_version, backup} (할 일 없으면 None)."""
    import sqlite3
    from sqlalchemy.schema import CreateTable

    from .models import report_detail_raw_table

    _refuse_newer_schema(engine)
    if get_schema_version(engine) != 6:
        return None
    db_path = engine.url.database
    if not db_path or db_path == ":memory:" or not os.path.exists(db_path):
        raise LegacyDatabase("파일이 아닌 DB 는 제자리 변환하지 않습니다.")
    dialect = engine.dialect
    engine.dispose()
    conn = sqlite3.connect(db_path, isolation_level=None, timeout=LEGACY_RESET_LOCK_TIMEOUT)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            from_version = int(conn.execute("PRAGMA user_version").fetchone()[0] or 0)
            if from_version != 6:
                conn.execute("ROLLBACK")  # 다른 프로세스가 먼저 끝냈다
                return None
            os.makedirs(backup_dir, exist_ok=True)
            backup = os.path.join(backup_dir, f"before_schema_v7_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db")
            _backup_file_db(db_path, backup)
            logger.LoggerFactory.logbot.info(f"[schema] 버전 6 → 7 변환 전 DB 백업: {backup}")
            conn.execute(str(CreateTable(report_detail_raw_table, if_not_exists=True).compile(dialect=dialect)))
            conn.execute("PRAGMA user_version = 7")
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    info = {"from_version": from_version, "backup": backup}
    logger.LoggerFactory.logbot.warning(f"[schema] DB 스키마 6 → 7 제자리 변환 완료(상세 원본 표 추가): {info}")
    return info


def migrate_schema(engine, backup_dir: str) -> dict | None:
    """버전 5 DB 를 6 으로, 버전 6 DB 를 7 로 제자리 변환한다. 체인: 5 → 6 → 7 (6 미만 규칙 유지).

    반환: 5 → 6 일 때 {from_version, backup, reports, detailed, moved_categories, orphan_entry_values, raw_upgrade},
    6 → 7 일 때 {from_version, backup} (할 일 없으면 None).
    쓰기 잠금(BEGIN IMMEDIATE) 한 트랜잭션: 먼저 통째로 백업 → 새 표 만들고 옮기기 → 같은 트랜잭션 안에서 행 수·값 해시 대조 →
    옛 표 지우기 → 버전 올리기 → COMMIT. 대조가 어긋나면 되돌리고 MigrationVerifyFailed. 화면용 표·중복군은 커밋 뒤 다시 만든다."""
    import sqlite3
    from sqlalchemy.schema import CreateIndex, CreateTable

    from services import report_category
    from .models import REPORT_COLUMN_NAMES, DETAIL_COLUMN_NAMES, TITLE_COLUMN_NAMES

    _refuse_newer_schema(engine)
    if get_schema_version(engine) == 6:
        return _migrate_6_to_7(engine, backup_dir)
    if get_schema_version(engine) != IN_PLACE_FROM:
        return None
    db_path = engine.url.database
    if not db_path or db_path == ":memory:" or not os.path.exists(db_path):
        raise LegacyDatabase("파일이 아닌 DB 는 제자리 변환하지 않습니다.")
    dialect = engine.dialect
    engine.dispose()
    conn = sqlite3.connect(db_path, isolation_level=None, timeout=LEGACY_RESET_LOCK_TIMEOUT)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            from_version = int(conn.execute("PRAGMA user_version").fetchone()[0] or 0)
            if from_version != IN_PLACE_FROM:
                conn.execute("ROLLBACK")  # 다른 프로세스가 먼저 끝냈다
                return None
            os.makedirs(backup_dir, exist_ok=True)
            backup = os.path.join(backup_dir, f"{PRE_UPGRADE_BACKUP_PREFIX}{from_version}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db")
            _backup_file_db(db_path, backup)
            logger.LoggerFactory.logbot.info(f"[schema] 버전 {from_version} → {SCHEMA_VERSION} 변환 전 DB 백업: {backup}")

            pre = _v5_preflight(conn)
            orphans = _v5_orphan_entry_values(conn)
            old_rows = _v5_rows(conn)
            old_body = [dict(r) for r in conn.execute("SELECT ID, raw_content, raw_type, saved_at FROM mysafety_raw_content")]
            moved = 0
            for row in old_rows:
                old_category = row.pop("_old_category", None)
                if old_category is not None:  # 상세가 있는 신고만 분류한다(없으면 목록만 본 신고 — category NULL)
                    row["category"] = report_category.category_of(None, None, row.get("entry_value"))
                    moved += row["category"] != old_category
                row["ssnpc_cd"] = None
                row["ssnpc_type"] = None

            for table in (report_table, report_body_table, report_effective_table):
                conn.execute(f'DROP TABLE IF EXISTS "{table.name}"')
                conn.execute(str(CreateTable(table).compile(dialect=dialect)))
            cols = ", ".join(f'"{c}"' for c in REPORT_COLUMN_NAMES)
            marks = ", ".join("?" for _ in REPORT_COLUMN_NAMES)
            conn.executemany(f'INSERT INTO mysafety_report ({cols}) VALUES ({marks})',
                             [[row.get(c) for c in REPORT_COLUMN_NAMES] for row in old_rows])
            conn.execute("INSERT INTO mysafety_report_body (ID, raw_content, raw_type, saved_at) "
                         "SELECT ID, raw_content, raw_type, saved_at FROM mysafety_raw_content")
            # 중복 멤버의 category 사본도 새 분류로(재계산 전까지 화면이 옛 값을 쓰지 않게)
            _preserve_orphan_entry_values(conn, orphans, dialect)
            conn.execute("UPDATE mysafety_duplicate_member SET category = COALESCE("
                         "(SELECT category FROM mysafety_report r WHERE r.ID = mysafety_duplicate_member.report_id), category)")

            # 대조: 원본 표마다 그 행이 새 표에 값까지 그대로 있어야 하고(합친 결과끼리가 아니라 원본 기준), 행 수·본문도 같아야 한다.
            new_rows = [dict(r) for r in conn.execute(f"SELECT {cols} FROM mysafety_report")]
            new_by_id = {r["ID"]: r for r in new_rows}
            new_body = [dict(r) for r in conn.execute("SELECT ID, raw_content, raw_type, saved_at FROM mysafety_report_body")]
            source_ids = {r[0] for r in conn.execute(
                "SELECT ID FROM mysafety UNION " + " UNION ".join(f'SELECT ID FROM "{t}"' for t in _V5_DETAIL_TABLES))}
            detail_ids = {r[0] for r in conn.execute(" UNION ".join(f'SELECT ID FROM "{t}"' for t in _V5_DETAIL_TABLES))}
            detailed_old = len(detail_ids)
            detailed_new = sum(1 for r in new_rows if r["category"] is not None)
            checks = {
                "reports": (len(source_ids), len(new_rows)),
                "detailed": (detailed_old, detailed_new),
                "body": (_digest(old_body, ("ID", "raw_content", "raw_type", "saved_at")),
                         _digest(new_body, ("ID", "raw_content", "raw_type", "saved_at"))),
            }
            if orphans:
                import json
                kept = conn.execute("SELECT value FROM mysafety_sync_meta WHERE key = ?",
                                    (ORPHAN_ENTRY_VALUES_META_KEY,)).fetchone()
                kept_by_id = {row[0]: row for row in json.loads(kept[0])} if kept and kept[0] else {}
                checks["orphan_entry_values"] = (orphans, [kept_by_id.get(row[0]) for row in orphans])
            bad = [name for name, (a, b) in checks.items() if a != b] + _v5_source_checks(conn, new_by_id)
            if bad:
                raise MigrationVerifyFailed(f"스키마 {from_version}→{SCHEMA_VERSION} 변환 대조 실패: {', '.join(bad)}. DB 는 그대로입니다.")

            for name in _V5_DROP:
                conn.execute(f'DROP TABLE IF EXISTS "{name}"')
            for statement in _index_statements():
                conn.execute(statement)
            for index in (*report_table.indexes, *report_effective_table.indexes, *report_body_table.indexes):
                conn.execute(str(CreateIndex(index).compile(dialect=dialect)))
            conn.execute("PRAGMA user_version = 6")
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    rebuild_effective(engine)
    info = {"from_version": from_version, "backup": backup, "reports": len(old_rows), "detailed": detailed_old,
            "moved_categories": moved, "orphan_entry_values": pre["orphan_entry_values"]}
    logger.LoggerFactory.logbot.warning(f"[schema] DB 스키마 {from_version} → 6 제자리 변환 완료: {info}")
    raw_upgrade = _migrate_6_to_7(engine, backup_dir)
    info["raw_upgrade"] = raw_upgrade
    return info


def title_to_sql(dataframes, engine, conn=None):
    """목록 API 결과 → mysafety_report. 처음 보는 신고는 목록 값만 가진 행(category NULL, 상세 미저장)으로 넣는다.
    반환: 새 신고의 신고번호 목록."""
    if not dataframes:
        return []

    combined_df = pd.concat(dataframes, ignore_index=True)
    if combined_df.empty:
        return []

    if '만족도조사여부' not in combined_df.columns:
        combined_df['만족도조사여부'] = ""
    combined_df['감시목록'] = "N"  # 계산값 — 저장 뒤 refresh_watch_flags 가 다시 정한다
    columns = [c for c in ("ID", "상태", "신고번호", "신고명", "신고일", "만족도조사여부", "별점", "별점사유", "감시목록")
               if c in combined_df.columns]

    incoming_ids = combined_df['ID'].tolist()
    new_report_numbers = []

    # SQLite SQLITE_MAX_VARIABLE_NUMBER 한계 대응 (구버전 999, 신버전 32766): 배치당 100행
    BATCH_SIZE = 100
    ID_CHUNK = 500  # in_() 쿼리용

    with engine.connect() as conn:
        existing_ids = set()
        for i in range(0, len(incoming_ids), ID_CHUNK):
            chunk_ids = incoming_ids[i:i + ID_CHUNK]
            existing_ids.update(conn.execute(select(report_table.c.ID).where(report_table.c.ID.in_(chunk_ids))).scalars())

        new_df = combined_df[~combined_df['ID'].isin(existing_ids)]
        if not new_df.empty:
            new_report_numbers = new_df['신고번호'].tolist()

        from sqlalchemy import case as sa_case
        records = combined_df[columns].to_dict('records')

        for i in range(0, len(records), BATCH_SIZE):
            batch = records[i:i + BATCH_SIZE]
            insert_stmt = insert(report_table).values(batch)
            # 만족도조사여부: 새 값이 비었으면 유지, '참여 완료' → 다른 값으로 되돌리기는 금지(결정 D-3, S-26).
            # 확정 미참여 재분류는 상세 저장(만족도 조회 결과)만 할 수 있다.
            poll_update = sa_case(
                (func.coalesce(insert_stmt.excluded.만족도조사여부, '') == '', report_table.c.만족도조사여부),
                (report_table.c.만족도조사여부 == '참여 완료', report_table.c.만족도조사여부),
                else_=insert_stmt.excluded.만족도조사여부
            )
            # 식별 정보(상태·신고번호·신고명·신고일)는 빈 값으로 덮지 않는다 — 상세 저장(reports_repo)·모바일
            # updateTitlesFromList 와 같은 규칙(2026-09-25 동등성 검수).
            def keep_if_empty(column):
                return sa_case(
                    (func.coalesce(getattr(insert_stmt.excluded, column), '') == '', getattr(report_table.c, column)),
                    else_=getattr(insert_stmt.excluded, column),
                )

            update_dict = {
                '상태': keep_if_empty('상태'),
                '신고번호': keep_if_empty('신고번호'),
                '신고명': keep_if_empty('신고명'),
                '신고일': keep_if_empty('신고일'),
                '만족도조사여부': poll_update,
            }
            conn.execute(insert_stmt.on_conflict_do_update(index_elements=['ID'], set_=update_dict))
        refresh_watch_flags(conn)
        conn.commit()

    logger.LoggerFactory.logbot.info(f"총 {len(combined_df)}건 목록 upsert 완료. (신규: {len(new_report_numbers)}건)")
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
    """크롤러 튜플(2~9개 원소) → core/storage/reports_repo.save_crawled. 반환: 변경 목록 [{'id', 'change_type', 'category'}].

    저장 규칙(열 주인·트랜잭션·변경 판정·분류)은 reports_repo 에 있다.
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


def repair_stale_effective(engine, *, raise_on_error: bool = False) -> bool:
    """기동/복원 시 화면용 표가 신고 행과 어긋났을 때만 다시 만든다. 기동 실패 로그에는 개인정보를 넣지 않는다."""
    from core.storage import reports_repo

    try:
        with engine.connect() as conn:
            drift = reports_repo.effective_drift(conn)
        if not drift:
            return False
        rebuild_effective(engine)
        logger.LoggerFactory.logbot.info(f"[effective] 화면용 표 불일치 재생성 완료: {drift}")
        return True
    except Exception as exc:
        logger.LoggerFactory.logbot.warning(f"[effective] 화면용 표 검사/재생성 실패: {type(exc).__name__}")
        if raise_on_error:
            raise
        return False


def rebuild_effective(engine, conn=None, *, track_duplicate_changes: bool = False):
    """화면용 표 전체 재생성. 1건 저장과 같은 규칙(reports_repo.refresh_effective_rows): 신고 + 수정값 + 감시목록 + 6개월 첨부 가림.
    중복군도 다시 계산한다."""
    from core.storage import reports_repo

    with engine.connect() as conn:
        reports_repo.refresh_effective_rows(conn)
        conn.commit()
        logger.LoggerFactory.logbot.info("화면용 표(mysafety_report_effective) 재생성 완료")
    return _refresh_duplicate_groups(engine, track_changes=track_duplicate_changes)


def sync_rating_status(engine, report_id, status_str="참여 완료", *, score=None, cause=None):
    """별점 제출·확인 결과를 신고 행에 기록하고 그 신고의 화면용 표를 다시 만든다(S-25, 결정 D-2).
    report_id 는 신고번호. score/cause 가 주어질 때만 별점·별점사유를 바꾼다(모바일 updateReportRatingByNumber 와 같음)."""
    from core.storage import reports_repo

    values = {"만족도조사여부": status_str}
    if score is not None:
        values["별점"] = int(score)
    if cause is not None:
        values["별점사유"] = cause
    with engine.begin() as conn:
        conn.execute(update(report_table).where(report_table.c.신고번호 == report_id).values(**values))
        ids = conn.execute(select(report_table.c.ID).where(report_table.c.신고번호 == report_id)).scalars().all()
        if ids:
            reports_repo.refresh_effective_rows(conn, ids)
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
