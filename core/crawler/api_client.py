from __future__ import annotations

import threading
import time
from time import sleep

import settings.settings as settings
from core.utils import logger

from core.crawler import direct_login


def ensure_browser_context(driver):
    if driver is None:
        raise RuntimeError("브라우저 API fallback에는 Selenium driver가 필요합니다.")
    if "safetyreport.go.kr" not in (driver.current_url or ""):
        driver.get(settings.myreporturl)
        sleep(2)


# ── 공식 사이트 요청 속도(2026-10-24 실측) ──
# 상세를 간격 없이 보내면 약 19건, 1초 간격 27건, 3초 간격 60건(186초) 뒤 429 `detail_rate_limited`(Retry-After 없음).
# 막히면 60초 쉬어도 그대로, 120초 뒤 다시 받는다. 정확한 값에 기대지 않고 간격을 자동 조절한다:
# 429 면 두 배(최대 15초), 20건 연속 성공마다 15%씩 줄인다(최소 기본값). 모바일 StandaloneApiService 와 같은 규칙.
REQUEST_INTERVAL = 3.0  # 초 — 기본 간격
MAX_REQUEST_INTERVAL = 15.0
RATE_LIMIT_WAITS = (120, 180, 300)  # 초 — 다 쓰면 "HTTP 429" 오류로 돌려준다
_pace_lock = threading.Lock()
_next_at = 0.0
_interval: float | None = None
_ok_streak = 0


def current_interval() -> float:
    return _interval if _interval is not None else REQUEST_INTERVAL


def _pace() -> None:
    """다음 요청 자리를 먼저 잡고 기다린다 — 여러 스레드가 불러도 간격이 겹치지 않는다."""
    global _next_at
    with _pace_lock:
        now = time.monotonic()
        at = max(now, _next_at)
        _next_at = at + current_interval()
    if at > now:
        sleep(at - now)


def _note_limited() -> None:
    global _interval, _ok_streak
    with _pace_lock:
        _ok_streak = 0
        _interval = min(current_interval() * 2, MAX_REQUEST_INTERVAL)


def _note_ok() -> None:
    global _interval, _ok_streak
    with _pace_lock:
        if current_interval() <= REQUEST_INTERVAL:
            return
        _ok_streak += 1
        if _ok_streak >= 20:
            _ok_streak = 0
            nxt = current_interval() * 0.85
            _interval = None if nxt <= REQUEST_INTERVAL else nxt


def _limit_reason(response) -> str:
    try:
        body = response.json()
        code = body.get("reason") or body.get("error") if isinstance(body, dict) else None
        return str(code)[:64] if code else "none"
    except Exception:
        return "none"


def get_authorized_json(session, url: str, *, params: dict | None = None, timeout: int = 20):
    payload, session, response = _get_authorized_once(session, url, params=params, timeout=timeout)
    for seconds in RATE_LIMIT_WAITS:
        if response is None or response.status_code != 429:
            break
        _note_limited()
        if logger.LoggerFactory.logbot:
            logger.LoggerFactory.logbot.warning(
                f"[API] 안전신문고가 요청이 많다며 잠시 막았습니다(429 {_limit_reason(response)}). {seconds}초 기다린 뒤 이어서 받습니다.")
        sleep(seconds)
        payload, session, response = _get_authorized_once(session, url, params=params, timeout=timeout)
    if response is not None and response.status_code == 200:
        _note_ok()
    return payload, session


def _get_authorized_once(session, url: str, *, params: dict | None = None, timeout: int = 20):
    _pace()
    try:
        response = direct_login.request_with_retry(
            session,
            "GET",
            url,
            params=params,
            timeout=timeout,
        )
    except Exception as exc:
        return {"error": f"network: {exc}"}, session, None

    if response.status_code == 401:
        direct_login.get_valid_token(force_refresh=True)
        session, _ = direct_login.make_authorized_session()
        _pace()
        try:
            response = direct_login.request_with_retry(
                session,
                "GET",
                url,
                params=params,
                timeout=timeout,
            )
        except Exception as exc:
            return {"error": f"network after relogin: {exc}"}, session, None

    if response.status_code != 200:
        return {"error": f"HTTP {response.status_code}"}, session, response

    try:
        return response.json(), session, response
    except Exception as exc:
        return {"error": f"JSON parse: {exc}"}, session, response
