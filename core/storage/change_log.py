"""변경 기록과 기기별 읽은 위치 (저장 계층 재설계 R5, 결정 D-5).

예전: 크롤링 변경을 crawl_changes.json 하나에 덮어쓰고, /api/v1/crawl/results 를 처음 부른 기기가 읽으면 지웠다 → 기기가 여럿이면 첫 기기만 받음(S-18).
지금: 변경을 mysafety_change_log 에 쌓고, 기기마다 읽은 위치(mysafety_change_cursor)를 따로 둔다.
- 크롤링 한 번의 변경은 같은 created_at 으로 들어간다(= 한 묶음).
- 처음 보는 기기는 가장 최근 묶음부터 받는다(예전 파일 방식과 같은 첫 응답).
- 기기 식별자를 보내지 않는 구앱은 device_id='legacy' 한 줄을 함께 쓴다(예전과 같은 동작, 나빠지지 않음).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.sqlite import insert

from core.database import models

LEGACY_DEVICE = "legacy"
_RETENTION_DAYS = 60
_MAX_ROWS = 5000
_IDLE_DEVICE_DAYS = 180


def _now_ms() -> int:
    return int(datetime.now().timestamp() * 1000)


def append_batch(engine, changes: list[dict]) -> int:
    """크롤링 한 번의 변경 목록을 쌓는다. 반환: 쌓은 건수."""
    if not changes:
        return 0
    table = models.change_log_table
    created_at = _now_ms()
    rows = [{
        "created_at": created_at,
        "kind": str(change.get("notification_kind") or "report"),
        "report_id": str(change.get("ID") or change.get("report_id") or "") or None,
        "payload": json.dumps(change, ensure_ascii=False),
    } for change in changes]
    with engine.begin() as conn:
        conn.execute(table.insert(), rows)
        cutoff = created_at - _RETENTION_DAYS * 24 * 3600 * 1000
        conn.execute(delete(table).where(table.c.created_at < cutoff))
        overflow = conn.execute(select(func.count()).select_from(table)).scalar() - _MAX_ROWS
        if overflow > 0:
            oldest = select(table.c.seq).order_by(table.c.seq).limit(overflow).scalar_subquery()
            conn.execute(delete(table).where(table.c.seq.in_(oldest)))
        # 오래 안 온 기기의 읽은 위치도 여기서 정리한다(다시 오면 최근 묶음부터).
        idle_cutoff = created_at - _IDLE_DEVICE_DAYS * 24 * 3600 * 1000
        conn.execute(delete(models.change_cursor_table).where(models.change_cursor_table.c.updated_at < idle_cutoff))
    return len(rows)


def _latest_batch_start(conn) -> int | None:
    table = models.change_log_table
    latest = conn.execute(select(func.max(table.c.created_at))).scalar()
    if latest is None:
        return None
    return conn.execute(select(func.min(table.c.seq)).where(table.c.created_at == latest)).scalar()


def read_for_device(engine, device_id: str | None) -> list[dict]:
    """그 기기가 아직 안 읽은 변경을 오래된 순으로 돌려주고 읽은 위치를 옮긴다."""
    device = (device_id or "").strip()[:128] or LEGACY_DEVICE
    log, cursor = models.change_log_table, models.change_cursor_table
    with engine.begin() as conn:
        last_seq = conn.execute(select(cursor.c.last_seq).where(cursor.c.device_id == device)).scalar()
        if last_seq is None:
            start = _latest_batch_start(conn)
            last_seq = (start - 1) if start is not None else (conn.execute(select(func.max(log.c.seq))).scalar() or 0)
        rows = conn.execute(select(log.c.seq, log.c.payload).where(log.c.seq > last_seq).order_by(log.c.seq)).all()
        new_last = rows[-1].seq if rows else last_seq
        stmt = insert(cursor).values(device_id=device, last_seq=new_last, updated_at=_now_ms())
        conn.execute(stmt.on_conflict_do_update(index_elements=["device_id"], set_={"last_seq": new_last, "updated_at": _now_ms()}))
    return [json.loads(row.payload) for row in rows]


def forget_idle_devices(engine, *, days: int = _IDLE_DEVICE_DAYS) -> int:
    """오래 안 온 기기의 읽은 위치를 지운다(다시 오면 최근 묶음부터)."""
    cutoff = int((datetime.now() - timedelta(days=days)).timestamp() * 1000)
    with engine.begin() as conn:
        result = conn.execute(delete(models.change_cursor_table).where(models.change_cursor_table.c.updated_at < cutoff))
    return int(result.rowcount or 0)
