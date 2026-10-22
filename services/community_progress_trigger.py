"""진행 보고(2-1) 보내는 때 판단과 값 만들기 — 규칙 문서 backfill-resume-20261022/CLIENT-RULES.md 4-1.

이 모듈은 "언제(trigger)·무엇(값)"만 정하고, 실제 전송·60초 간격·마지막 전송 저장(주기 15분·하루 1번 판정)은
services/community_progress_report.py(`maybe_send`)가 맡는다.
- trigger: `start`(서버 기동 뒤 게이트 통과 첫 확인), `state_change`((state, phase, reason, official_login, optimize.*) 가 바뀜,
  개수만 바뀐 것은 아님), `periodic`(진행 중 상태 — 15분 판정은 보고 쪽), `daily`(done·run 없음 — 하루 1번 판정은 보고 쪽).
- 업로드 일시 중지(2-9)·필수 버전 잠금(2-10) 중에는 부르지 않는다. 연결(활성 context)이 없으면 부르지 않는다(설정 단계 보고가 맡는다).
- PC 는 pace 가 없다(모바일만).
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone

IN_PROGRESS = ("listing", "filling", "uploading", "paused", "waiting_optimize", "holds")
STATES = IN_PROGRESS + ("done",)

_lock = threading.Lock()
_started = False
_last_key: tuple | None = None


def _iso(epoch) -> str | None:
    if not isinstance(epoch, (int, float)) or isinstance(epoch, bool) or epoch <= 0:
        return None
    try:
        return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    except (OverflowError, OSError, ValueError):
        return None


def _marker(store) -> dict | None:
    try:
        marker = json.loads(store.meta("raw_backfill") or "null")
    except (TypeError, ValueError):
        return None
    if not isinstance(marker, dict) or marker.get("local_dataset_id") != store.local_dataset_id():
        return None  # 다른(이전) 데이터셋의 run 은 지금 run 이 아니다
    return marker


def official_login(marker: dict | None) -> str:
    """ok | retry(일시 실패로 기다리는 중) | manual_required(사용자가 로그인해야 함)."""
    from services.community_raw_backfill import LOGIN_REASONS
    if not marker or marker.get("state") != "paused" or marker.get("reason") not in LOGIN_REASONS:
        return "ok"
    if marker.get("reason") == "official_login_required" and marker.get("login_fp") is not None:
        return "manual_required"
    return "retry"


def backfill_view(store, marker: dict | None = None) -> dict:
    """progress.schema.json 의 backfill 객체. run 이 없으면 state='none' 이고 나머지는 모두 null."""
    marker = _marker(store) if marker is None else marker
    state = (marker or {}).get("state")
    keys = ("total", "stored", "queued", "pending", "holds", "stale_holds", "raw_unavailable", "not_eligible")
    if not marker or state not in STATES:
        return {"state": "none", "phase": None, "reason": None, "next_attempt_at": None, **{k: None for k in keys},
                "run_started_at": None, "updated_at": None}
    counts: dict = {k: None for k in keys}
    try:
        from services.community_backfill_store import SqliteItems
        got = SqliteItems(store).counts(marker["run_id"])
        if got.get("total"):
            counts = {k: int(got[k]) for k in keys}
        elif isinstance(marker.get("items"), dict):  # 아주 옛 marker(항목을 marker 안에 둔 것)
            from services.community_raw_backfill import count_states
            counts = count_states(marker["items"].values())
        elif marker.get("listing_complete"):
            counts = {k: int(got[k]) for k in keys}  # 목록이 끝났는데 0건 — 진짜 0
    except Exception as exc:
        from core.utils.fallback import note_fallback
        note_fallback("progress_trigger.counts", exc)
    phase = marker.get("phase")
    return {"state": state, "phase": phase if isinstance(phase, str) else None,
            "reason": marker.get("reason") if state == "paused" else None,
            "next_attempt_at": _iso(marker.get("next_attempt_at")) if state in ("paused", "holds") else None,
            **counts, "run_started_at": _iso(marker.get("run_started_at")), "updated_at": _iso(marker.get("updated_at"))}


def optimize_view() -> dict:
    try:
        from core.database.engine import get_engine
        from services import db_optimize
        engine = get_engine()
        return {"required": bool(db_optimize.needs_run(engine)), "running": bool(db_optimize.is_running()),
                "holds_only": bool(db_optimize.only_holds_left(engine))}
    except Exception as exc:
        from core.utils.fallback import note_fallback
        note_fallback("progress_trigger.optimize", exc)
        return {"required": False, "running": False, "holds_only": False}


def notice_inputs(store=None) -> dict:
    """사용자 조치 알림(2-8, services/user_action_notice.evaluate)에 넘길 값."""
    if store is None:
        from services.community_store import CommunityStore
        store = CommunityStore.open()
    marker = _marker(store)
    view = backfill_view(store, marker)
    from services import community_update_lock
    return {"backfill_reason": view["reason"], "official_login_manual": official_login(marker) == "manual_required",
            "stale_holds": int(view.get("stale_holds") or 0), "update_required": community_update_lock.locked(store)}


def decide_trigger(key: tuple, state: str) -> str:
    """이번 확인의 trigger. 같은 프로세스에서 처음이면 start."""
    global _started, _last_key
    with _lock:
        if not _started:
            trigger = "start"
            _started = True
        elif key != _last_key:
            trigger = "state_change"
        elif state in IN_PROGRESS:
            trigger = "periodic"
        else:
            trigger = "daily"
        _last_key = key
    return trigger


def tick(store=None) -> str | None:
    """60초 게이트 확인·게이트 통과 때 부른다. 부른 trigger 를 돌려준다(안 불렀으면 None)."""
    try:
        if store is None:
            from services.community_store import CommunityStore
            store = CommunityStore.open()
        from services import community_update_lock, community_upload_pause
        if community_update_lock.locked(store) or community_upload_pause.paused(store):
            return None
        if store.active_context() is None:
            return None
        marker = _marker(store)
        backfill = backfill_view(store, marker)
        optimize = optimize_view()
        login = official_login(marker)
        key = (backfill["state"], backfill["phase"], backfill["reason"], login,
               optimize["required"], optimize["running"], optimize["holds_only"])
        trigger = decide_trigger(key, backfill["state"])
        from services import community_progress_report
        community_progress_report.maybe_send(store=store, trigger=trigger, backfill=backfill, optimize=optimize,
                                             official_login=login)
        # 진행 보고가 503 maintenance 를 받았으면 업로드 일시 중지로 옮긴다(1회 전송은 다음 run 에서 paused/maintenance).
        community_upload_pause.note_report_maintenance(store, community_progress_report.last_maintenance(store))
        return trigger
    except Exception as exc:  # 보고 실패는 조용히 넘긴다(다음 주기)
        from core.utils.fallback import note_fallback
        note_fallback("progress_trigger.tick", exc)
        return None


def _reset_for_tests() -> None:
    global _started, _last_key
    with _lock:
        _started, _last_key = False, None
