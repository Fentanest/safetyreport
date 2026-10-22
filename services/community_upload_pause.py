"""업로드 일시 중지(서버 점검) — 2-9, 규칙 문서 backfill-resume-20261022/CLIENT-RULES.md 5절.

서버가 본체다(중지 중이면 업로드·manifest·진행 보고가 DB 쓰기 전에 503 `maintenance`). 이 앱은 미리 알고 덜 두드린다.
받는 곳: status 의 `ingest.paused`·`ingest.resume_at`·`ingest.pause_message`, 그리고 503 `maintenance` 응답 본문.
저장: community.db meta `upload_pause` = {paused, resume_at(epoch|None), message, source('status'|'503'), saved_at, wake_at, fallback_at}.
- 중지 동안 업로더·1회 전송·진행 보고는 요청을 보내지 않는다. 로컬 기능(수집·조회)은 그대로.
- 재개 시각이 있으면 그 시각 + 무작위 0~120초(wake_at)에 스케줄러 1회 작업이 status 를 한 번 확인한다.
  재개 시각이 없으면 타이머 없이 평소 60초 status 확인을 기다린다. 점검 때문에 status 를 더 자주 부르지 않는다.
- status 에 `ingest.paused` 키가 있으면 그 값이 정본이다. 키가 없으면 저장된 판정을 그대로 둔다.
  (판단: 503 으로만 알게 된 중지는 status 가 끝내 키를 주지 않을 수 있다 — 그때는 재개 시각(없으면 Retry-After)이 지나면
   저장을 지워 한 번만 다시 시도해 본다. 아직 중지면 그 요청이 다시 503 을 받아 새 시각으로 저장된다.)
"""
from __future__ import annotations

import json
import random
import threading
import time
from datetime import datetime, timedelta, timezone

META_KEY = "upload_pause"
JITTER_MAX_SECONDS = 120
NO_RESUME_RETRY_SECONDS = 600  # 503 에 재개 시각이 없을 때 서버가 주는 Retry-After 기본값
RESUME_JOB_ID = "community-upload-resume"
_SEOUL = timezone(timedelta(hours=9))

_rand = random.random
_now = time.time
_cache_lock = threading.Lock()
_cache: dict[str, dict | None] = {}


def _parse_iso(value) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()


def _clean_message(value) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text[:500] if text else None


def _load(store) -> dict | None:
    with _cache_lock:
        if store.path in _cache:
            return _cache[store.path]
    try:
        value = json.loads(store.meta(META_KEY) or "null")
    except (TypeError, ValueError):
        value = None
    value = value if isinstance(value, dict) and value.get("paused") else None
    with _cache_lock:
        _cache[store.path] = value
    return value


def forget_cache() -> None:  # 테스트 전용
    with _cache_lock:
        _cache.clear()


def read(store=None) -> dict | None:
    """지금 지키는 중지 판정 또는 None."""
    if store is None:
        from services.community_store import CommunityStore
        store = CommunityStore.open()
    return _load(store)


def paused(store=None) -> bool:
    try:
        return read(store) is not None
    except Exception as exc:
        from core.utils.fallback import note_fallback
        note_fallback("upload_pause.read", exc)
        return False


def _save(store, *, resume_at: float | None, message: str | None, source: str, retry_after: float | None = None) -> dict:
    now = _now()
    previous = _load(store)
    # 같은 재개 시각이면 지터를 다시 뽑지 않는다(재시작·반복 수신에도 깨우는 시각이 흔들리지 않게).
    if previous and previous.get("resume_at") == resume_at and previous.get("wake_at") is not None:
        wake_at = previous["wake_at"]
    else:
        wake_at = None if resume_at is None else resume_at + JITTER_MAX_SECONDS * _rand()
    fallback = resume_at if resume_at is not None else now + (retry_after or NO_RESUME_RETRY_SECONDS)
    value = {"paused": True, "resume_at": resume_at, "message": message, "source": source, "saved_at": now,
             "wake_at": wake_at, "fallback_at": fallback}
    store.set_meta(META_KEY, json.dumps(value, ensure_ascii=False))
    with _cache_lock:
        _cache[store.path] = value
    if wake_at is not None:
        _schedule_resume(wake_at)
    return value


def clear(store) -> bool:
    """중지 판정을 지운다. 지운 것이 있으면 True(업로더·1회 전송을 깨운다)."""
    had = _load(store) is not None
    with store.transaction() as tx:
        tx.execute("DELETE FROM meta WHERE key=?", (META_KEY,))
        if had:
            # 503 maintenance 로 재개 시각까지 미룬 행: 일찍 재개되면 그 시각을 기다리지 않고 바로 보낼 수 있게 한다.
            now = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            tx.execute("UPDATE outbox SET next_retry_at=? WHERE state='retry_wait' AND last_error_code='maintenance'"
                       " AND (next_retry_at IS NULL OR next_retry_at > ?)", (now, now))
    with _cache_lock:
        _cache[store.path] = None
    if had:
        _on_resume()
    return had


def apply_status(store, status) -> str:
    """정상 status 응답에서 부른다. 'paused' | 'resumed' | 'unchanged' | 'kept'(키 없음)."""
    ingest = status.get("ingest") if isinstance(status, dict) else None
    if not isinstance(ingest, dict) or "paused" not in ingest or not isinstance(ingest.get("paused"), bool):
        current = _load(store)
        # 서버가 판정을 주지 않는다. 503 으로만 알게 된 중지는 재개 시각이 지나면 한 번 다시 시도해 본다(모듈 설명).
        if current and current.get("source") == "503" and _now() >= (current.get("fallback_at") or 0):
            clear(store)
            return "resumed"
        return "kept"
    if ingest["paused"]:
        resume_at = _parse_iso(ingest.get("resume_at"))
        current = _load(store)
        message = _clean_message(ingest.get("pause_message"))
        if current and current.get("resume_at") == resume_at and current.get("message") == message and current.get("source") == "status":
            return "unchanged"
        _save(store, resume_at=resume_at, message=message, source="status")
        return "paused"
    return "resumed" if clear(store) else "unchanged"


def note_maintenance(body, headers=None, store=None) -> bool:
    """503 응답 본문이 `maintenance` 면 중지를 저장하고 True. 그 밖이면 False(일반 일시 장애)."""
    if isinstance(body, (bytes, bytearray, str)):
        try:
            body = json.loads(body)
        except (TypeError, ValueError, UnicodeError):
            return False
    err = body.get("error") if isinstance(body, dict) else None
    if not isinstance(err, dict) or err.get("code") != "maintenance":
        return False
    retry_after = None
    seconds = err.get("retry_after_seconds")
    if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) and seconds >= 0:
        retry_after = float(seconds)
    else:
        try:
            raw = (headers or {}).get("Retry-After") or (headers or {}).get("retry-after")
            retry_after = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            retry_after = None
    try:
        if store is None:
            from services.community_store import CommunityStore
            store = CommunityStore.open()
        _save(store, resume_at=_parse_iso(err.get("resume_at")), message=_clean_message(err.get("pause_message")),
              source="503", retry_after=retry_after)
    except Exception as exc:
        from core.utils.fallback import note_fallback
        note_fallback("upload_pause.note_maintenance", exc)
    return True


REPORT_SEEN_KEY = "upload_pause_report_seen"


def note_report_maintenance(store, value) -> bool:
    """진행 보고(E, services/community_progress_report.last_maintenance)가 받은 503 maintenance 를 중지로 옮긴다.
    value = {resume_at(epoch|None), retry_after(초|None), message, seen_at}. 같은 기록(seen_at)은 한 번만 옮긴다
    (status 가 이미 재개를 알린 뒤 옛 기록으로 다시 멈추지 않게)."""
    if not isinstance(value, dict) or not isinstance(value.get("seen_at"), (int, float)):
        return False
    try:
        seen = float(store.meta(REPORT_SEEN_KEY) or 0)
    except (TypeError, ValueError):
        seen = 0.0
    if value["seen_at"] <= seen:
        return False
    resume = value.get("resume_at")
    resume = float(resume) if isinstance(resume, (int, float)) and not isinstance(resume, bool) else None
    retry = value.get("retry_after")
    retry = float(retry) if isinstance(retry, (int, float)) and not isinstance(retry, bool) and retry >= 0 else None
    store.set_meta(REPORT_SEEN_KEY, str(value["seen_at"]))
    if resume is not None and resume <= _now():
        return False  # 이미 지난 재개 시각 — 멈출 이유가 없다
    _save(store, resume_at=resume, message=_clean_message(value.get("message")), source="503", retry_after=retry)
    return True


def is_maintenance(body) -> bool:
    """본문이 503 `maintenance` 오류인가(저장하지 않는다)."""
    if isinstance(body, (bytes, bytearray, str)):
        try:
            body = json.loads(body)
        except (TypeError, ValueError, UnicodeError):
            return False
    err = body.get("error") if isinstance(body, dict) else None
    return isinstance(err, dict) and err.get("code") == "maintenance"


# ── 깨우기 ────────────────────────────────────────────────────────────────────

def _schedule_resume(wake_at: float) -> None:
    try:
        from services import community_schedule
        community_schedule.schedule_once(RESUME_JOB_ID, wake_at, check_resume)
    except Exception as exc:
        from core.utils.fallback import note_fallback
        note_fallback("upload_pause.schedule", exc)


def check_resume() -> None:
    """재개 시각(+지터)의 1회 작업: status 를 한 번 확인한다(게이트 갱신이 apply_status 를 부른다).
    아직 중지(연장)면 apply_status 가 새 재개 시각으로 다시 예약한다."""
    try:
        from services import community_gate
        community_gate.refresh_now()
    except Exception as exc:
        from core.utils.fallback import note_fallback
        note_fallback("upload_pause.check_resume", exc)


def reschedule_on_start(store=None) -> None:
    """서버 시작: 저장된 재개 시각이 있으면 1회 작업을 다시 건다(앱 재시작에도 지킨다)."""
    try:
        value = read(store)
        if value and value.get("wake_at") is not None:
            _schedule_resume(max(value["wake_at"], _now() + 1))
    except Exception as exc:
        from core.utils.fallback import note_fallback
        note_fallback("upload_pause.reschedule", exc)


def _on_resume() -> None:
    try:
        from services import community_uploader, community_raw_backfill
        community_uploader.wake()
        community_raw_backfill.start()
    except Exception as exc:
        from core.utils.fallback import note_fallback
        note_fallback("upload_pause.resume_wake", exc)


# ── 화면 문구(2-13) ───────────────────────────────────────────────────────────

def _clock_text(epoch: float, now: float) -> str:
    """KST "오후 3시 30분"(분이 0이면 "오후 3시"), 날짜가 오늘이 아니면 "10월 23일 오전 9시"."""
    moment = datetime.fromtimestamp(epoch, _SEOUL)
    today = datetime.fromtimestamp(now, _SEOUL).date()
    hour = moment.hour % 12 or 12
    text = f"{'오전' if moment.hour < 12 else '오후'} {hour}시" + (f" {moment.minute}분" if moment.minute else "")
    if moment.date() != today:
        text = f"{moment.month}월 {moment.day}일 " + text
    return text


def banner_text(value: dict | None, now: float | None = None) -> str | None:
    if not value:
        return None
    resume_at = value.get("resume_at")
    if resume_at is not None:
        return ("서버 점검 중입니다. 신고 업로드를 잠시 멈췄고, "
                f"{_clock_text(resume_at, _now() if now is None else now)}에 자동으로 다시 올립니다.")
    return "서버 점검 중입니다. 신고 업로드를 잠시 멈췄고, 점검이 끝나면 자동으로 다시 올립니다."


def view(store=None) -> dict:
    """화면용 요약. message 는 평문(화면은 textContent 로만 넣는다)."""
    try:
        value = read(store)
    except Exception:
        value = None
    if not value:
        return {"paused": False}
    resume_at = value.get("resume_at")
    return {"paused": True, "text": banner_text(value), "message": value.get("message"),
            "resume_at": None if resume_at is None else
            datetime.fromtimestamp(resume_at, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")}
