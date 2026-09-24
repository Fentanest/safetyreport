"""서버↔모바일 DB 교환과 교체 방식 복원 (저장 계층 재설계 R1, docs/plans/storage-refactor-plan.md §3-7).

원칙
- 값은 바꾸지 않는다: NULL 은 NULL, '' 는 '' (계약 null_rule). 열 목록은 SQLAlchemy 모델에서 가져오며,
  모델 = contracts/storage-contract.json 은 tests/test_storage_contract.py 가 보장한다.
- 감시목록의 원천은 mysafety_watchlist 하나다. title/merge 의 `감시목록` 열은 거기서 계산한 값이고,
  서버 sync_meta 에는 'watchlist' 사본을 두지 않는다(모바일은 sync_meta 'watchlist' 키가 원천이라 교환 때 변환).
- 모바일 DB 로 복원해도 서버 전용 데이터(관리자·API 키·변경 기록·지오코딩 캐시)는 지우지 않는다.
- 복원은 현재 DB 의 사본(또는 업로드 파일)에 먼저 적용 → 무결성 검사 → 백업 → 원자적 교체.
  크롤링이나 지도 좌표 변환이 도는 중에는 거부한다.
"""
from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import create_engine, func, select

import settings.settings as settings
from core.database import models
from core.utils import logger

BATCH = 200
SERVER_RUNTIME_META_KEYS = {"map_backfill_state"}
WATCHLIST_META_KEY = "watchlist"
_DETAIL_BY_CATEGORY = {
    "traffic": models.detail_traffic_table,
    "parking": models.detail_parking_table,
    "other": models.detail_other_table,
}


class RestoreRefused(RuntimeError):
    """지금은 복원할 수 없음(크롤링·지도 변환 중 등). 사용자에게 그대로 보여 줄 문장."""


# ── 모바일 DB 읽기 ──────────────────────────────────────────────────────────

@dataclass
class MobileSnapshot:
    report_columns: set[str]
    reports: list[dict]
    raw: list[dict] | None
    sync_meta: list[dict]
    geocode_cache: list[dict] | None
    duplicate_group: list[dict] | None
    duplicate_member: list[dict] | None
    report_override: list[dict] | None
    duplicate_decision: list[dict] | None
    tables: set[str] = field(default_factory=set)


def _read_table(conn: sqlite3.Connection, tables: set[str], name: str) -> list[dict] | None:
    """표가 없으면 None(구버전 앱). 있으면 모든 행을 원시 값 그대로. 읽기 오류는 삼키지 않는다."""
    if name not in tables:
        return None
    return [dict(r) for r in conn.execute(f'SELECT * FROM "{name}"')]


def read_mobile_db(path: str) -> MobileSnapshot:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "reports" not in tables:
            raise ValueError("모바일 DB 에 reports 표가 없습니다.")
        return MobileSnapshot(
            report_columns={r[1] for r in conn.execute('PRAGMA table_info("reports")')},
            reports=_read_table(conn, tables, "reports") or [],
            raw=_read_table(conn, tables, "report_raw"),
            sync_meta=_read_table(conn, tables, "sync_meta") or [],
            geocode_cache=_read_table(conn, tables, "geocode_cache"),
            duplicate_group=_read_table(conn, tables, "duplicate_group"),
            duplicate_member=_read_table(conn, tables, "duplicate_member"),
            report_override=_read_table(conn, tables, "report_override"),
            duplicate_decision=_read_table(conn, tables, "duplicate_decision"),
            tables=tables,
        )
    finally:
        conn.close()


def parse_watchlist(value) -> list[str]:
    return [s.strip() for s in str(value or "").split(",") if s.strip()]


# ── 중복군 id 정규화 (duplicate_group_service.normalize_raw_content 와 같은 규칙) ──

def _normalize_raw(raw_content) -> str:
    text = str(raw_content or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n+", "\n", text)
    return text.strip()


def _canonical_group_ids(snapshot: MobileSnapshot, raw_by_id: dict[str, str]) -> dict[str, str]:
    """모바일 레거시 해시 group_id 를 서버 기준(본문 sha256)으로 바꾼다."""
    members_by_group: dict[str, list[str]] = {}
    for m in snapshot.duplicate_member or []:
        members_by_group.setdefault(m["group_id"], []).append(m["report_id"])
    mapping = {}
    for g in snapshot.duplicate_group or []:
        gid = g["group_id"]
        mapping[gid] = gid
        for report_id in members_by_group.get(gid, []):
            normalized = _normalize_raw(raw_by_id.get(report_id))
            if normalized:
                mapping[gid] = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
                break
    return mapping


# ── 모바일 → 서버 쓰기 ───────────────────────────────────────────────────────

def _insert(conn, table, rows):
    for i in range(0, len(rows), BATCH):
        conn.execute(table.insert(), rows[i:i + BATCH])


def _columns(table) -> list[str]:
    return [c.name for c in table.columns]


def apply_mobile_snapshot(engine, snapshot: MobileSnapshot) -> int:
    """스냅숏을 engine 의 서버 DB 에 적용한다(호출자는 스테이징 DB 를 넘긴다). 반환: 신고 수."""
    from core.database import database

    meta = {row["key"]: row["value"] for row in snapshot.sync_meta if row.get("key")}
    watchlist = parse_watchlist(meta[WATCHLIST_META_KEY]) if WATCHLIST_META_KEY in meta else None

    raw_by_id: dict[str, str] = {}
    raw_rows: list[dict] = []
    if snapshot.raw is not None:
        for r in snapshot.raw:
            raw_rows.append({"ID": r["ID"], "raw_content": r["raw_content"], "raw_type": r["raw_type"], "saved_at": r["saved_at"]})
            raw_by_id[r["ID"]] = r["raw_content"]
    else:  # v5 이전 앱: 본문이 reports.raw_content 에 있었다
        for r in snapshot.reports:
            if r.get("raw_content"):
                raw_rows.append({"ID": r["ID"], "raw_content": r["raw_content"], "raw_type": "", "saved_at": r.get("synced_at")})
                raw_by_id[r["ID"]] = r["raw_content"]

    title_cols = _columns(models.title_table)
    detail_cols = _columns(models.detail_traffic_table)
    report_ids = set()

    with engine.begin() as conn:
        if watchlist is None:
            watchlist = [r[0] for r in conn.execute(select(models.watchlist_table.c["신고번호"]))]
        watch_set = set(watchlist)

        title_rows, detail_rows = [], {k: [] for k in _DETAIL_BY_CATEGORY}
        for r in snapshot.reports:
            report_ids.add(r["ID"])
            title = {c: r.get(c) for c in title_cols}
            title["감시목록"] = "Y" if r.get("신고번호") in watch_set else "N"
            title_rows.append(title)
            category = str(r.get("category") or "other").strip().lower()
            detail_rows[category if category in detail_rows else "other"].append({c: r.get(c) for c in detail_cols})

        for table in (models.merge_traffic_table, models.merge_parking_table, models.merge_other_table,
                      models.detail_traffic_table, models.detail_parking_table, models.detail_other_table,
                      models.title_table, models.raw_content_table, models.duplicate_member_table, models.duplicate_group_table):
            conn.execute(table.delete())
        _insert(conn, models.title_table, title_rows)
        for category, rows in detail_rows.items():
            _insert(conn, _DETAIL_BY_CATEGORY[category], rows)
        _insert(conn, models.raw_content_table, raw_rows)

        # 감시목록: 앱에 키가 있으면 그것이 원천, 없으면(구앱) 서버 것을 유지.
        if WATCHLIST_META_KEY in meta:
            conn.execute(models.watchlist_table.delete())
            _insert(conn, models.watchlist_table, [{"신고번호": n} for n in dict.fromkeys(watchlist)])

        # entry_value: 앱 값이 있는 신고는 앱 값, 없으면 서버 값 유지, 사라진 신고의 행은 삭제.
        if "entry_value" in snapshot.report_columns:
            for r in snapshot.reports:
                if r.get("entry_value"):
                    conn.execute(models.entry_value_table.delete().where(models.entry_value_table.c.ID == r["ID"]))
                    conn.execute(models.entry_value_table.insert().values(ID=r["ID"], entry_value=r["entry_value"]))
        existing_ev = [row[0] for row in conn.execute(select(models.entry_value_table.c.ID))]
        orphan = [i for i in existing_ev if i not in report_ids]
        for i in range(0, len(orphan), BATCH):
            conn.execute(models.entry_value_table.delete().where(models.entry_value_table.c.ID.in_(orphan[i:i + BATCH])))

        # sync_meta: 서버 런타임 키는 유지, 감시목록 사본은 두지 않음, 나머지는 앱 값(NULL 포함 그대로).
        conn.execute(models.sync_meta_table.delete().where(models.sync_meta_table.c.key.notin_(SERVER_RUNTIME_META_KEYS)))
        _insert(conn, models.sync_meta_table, [
            {"key": k, "value": v} for k, v in meta.items() if k not in SERVER_RUNTIME_META_KEYS and k != WATCHLIST_META_KEY
        ])

        # 지오코딩 캐시: 서버 캐시를 지우지 않고 앱 캐시를 합친다(S-13).
        if snapshot.geocode_cache:
            cache_cols = _columns(models.geocode_cache_table)
            for row in snapshot.geocode_cache:
                conn.execute(models.geocode_cache_table.delete().where(models.geocode_cache_table.c["주소정규화"] == row["주소정규화"]))
            _insert(conn, models.geocode_cache_table, [{c: row.get(c) for c in cache_cols} for row in snapshot.geocode_cache])

        # 사용자 소유 새 표: 앱에 표가 있으면 앱 것이 원천, 없으면(구앱) 서버 것 유지.
        # 중복 판단은 앱이 아직 기록하지 않는 동안(모바일 R3 전) 빈 표가 오므로, 비어 있으면 서버 판단을 지우지 않는다.
        for rows, table in ((snapshot.report_override, models.report_override_table),
                            (snapshot.duplicate_decision or None, models.duplicate_decision_table)):
            if rows is not None:
                conn.execute(table.delete())
                _insert(conn, table, [{c: row.get(c) for c in _columns(table)} for row in rows])

    # merge 재생성(중복군도 여기서 재계산) 뒤, 앱의 중복군을 그대로 덮는다.
    database.merge_final(engine)
    if snapshot.duplicate_group is not None:
        mapping = _canonical_group_ids(snapshot, raw_by_id)
        group_cols = _columns(models.duplicate_group_table)
        member_cols = _columns(models.duplicate_member_table)
        groups, seen = [], set()
        for g in snapshot.duplicate_group:
            gid = mapping.get(g["group_id"], g["group_id"])
            if gid in seen:
                continue
            seen.add(gid)
            row = {c: g.get(c) for c in group_cols}
            row["group_id"] = gid
            if mapping.get(g["group_id"]) != g["group_id"]:
                row["fingerprint"] = gid
            groups.append(row)
        members = []
        for m in snapshot.duplicate_member or []:
            gid = mapping.get(m["group_id"], m["group_id"])
            if gid in seen:
                members.append({**{c: m.get(c) for c in member_cols}, "group_id": gid})
        with engine.begin() as conn:
            conn.execute(models.duplicate_member_table.delete())
            conn.execute(models.duplicate_group_table.delete())
            _insert(conn, models.duplicate_group_table, groups)
            _insert(conn, models.duplicate_member_table, members)
    return len(snapshot.reports)


# ── 교체 방식 복원 ────────────────────────────────────────────────────────────

def ensure_restore_allowed(engine) -> None:
    from services.crawl_manager import crawl_manager
    from services import geocode_service

    if crawl_manager.is_crawling():
        raise RestoreRefused("크롤링이 진행 중입니다. 끝난 뒤 다시 복원하세요.")
    try:
        running = bool(geocode_service.get_backfill_progress(engine).get("running"))
    except Exception:
        running = False
    if running:
        raise RestoreRefused("지도 좌표 변환이 진행 중입니다. 끝난 뒤 다시 복원하세요.")


def _copy_sqlite(src: str, dst: str) -> None:
    source = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    target = sqlite3.connect(dst)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


def _integrity_check(path: str) -> None:
    conn = sqlite3.connect(path)
    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
    if result != "ok":
        raise RuntimeError(f"복원 준비 DB 무결성 검사 실패: {result}")


def _backup_live(dst: str) -> str:
    if not os.path.exists(dst):
        return ""
    backup_dir = os.path.join(settings.datapath, "backups")
    os.makedirs(backup_dir, exist_ok=True)
    backup_path = os.path.join(backup_dir, f"data_before_restore_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db")
    _copy_sqlite(dst, backup_path)
    return backup_path


def _swap_in(staged: str, dst: str) -> None:
    """캐시된 엔진 연결을 닫고, 현재 파일의 WAL 을 합친 뒤 스테이징 파일로 원자적으로 바꾼다."""
    from core.database.engine import get_engine

    get_engine().dispose()
    if os.path.exists(dst):
        conn = sqlite3.connect(dst)
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()
    for ext in ("-wal", "-shm"):
        side = dst + ext
        if os.path.exists(side):
            os.remove(side)
    os.replace(staged, dst)


def restore(uploaded_path: str, kind: str) -> tuple[str, int]:
    """kind='server' | 'mobile'. (백업 경로, 신고 수) 반환. 실패하면 현재 DB 는 그대로다."""
    from core.database import database
    from core.database.engine import get_engine

    dst = settings.db_path
    ensure_restore_allowed(get_engine())
    staged = os.path.join(os.path.dirname(dst), f".restore_staging_{os.getpid()}_{datetime.now().strftime('%Y%m%d%H%M%S%f')}.db")
    try:
        if kind == "server":
            _copy_sqlite(uploaded_path, staged)
            engine = create_engine(f"sqlite:///{staged}")
            try:
                database.upgrade_schema(engine)
                with engine.connect() as conn:
                    count = conn.execute(select(func.count()).select_from(models.title_table)).scalar()
            finally:
                engine.dispose()
        elif kind == "mobile":
            snapshot = read_mobile_db(uploaded_path)
            if os.path.exists(dst):
                _copy_sqlite(dst, staged)  # 서버 전용 표(관리자·API 키·변경 기록·캐시)를 가진 현재 DB 위에 적용
            engine = create_engine(f"sqlite:///{staged}")
            try:
                database.upgrade_schema(engine)
                count = apply_mobile_snapshot(engine, snapshot)
            finally:
                engine.dispose()
        else:
            raise ValueError(f"알 수 없는 DB 종류: {kind}")
        _integrity_check(staged)
        backup = _backup_live(dst)
        _swap_in(staged, dst)
    finally:
        for path in (staged, staged + "-wal", staged + "-shm"):
            if os.path.exists(path):
                os.remove(path)
    logger.LoggerFactory.logbot.info(f"{kind} DB 복원 완료. 백업: {backup}, 신고건수: {count}")
    return backup, count
