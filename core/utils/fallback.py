"""실패를 기본값으로 대체하는 지점의 공통 진단(EO R-17).

정책은 docs/architecture/overview.md 의 "실패 대체 정책" 절을 따른다.
- 대체는 기존 동작(best-effort 또는 fail-closed)을 바꾸지 않는다. 이 모듈은 기록만 한다.
- 기록에는 동작명과 예외 종류만 남긴다. 예외 메시지·인자·SQL 값에는 경로·쿠키·계정이
  섞일 수 있어 남기지 않는다.
- 같은 (동작, 예외 종류)는 일정 시간에 한 번만 남겨, 화면 polling 이 로그를 채우지 않게 한다.
"""

import logging
import threading
import time
import traceback

_REPEAT_SECONDS = 60.0
_last_logged: dict = {}
_lock = threading.Lock()


def _log() -> logging.Logger:
    try:
        from core.utils import logger
        if logger.LoggerFactory.logbot is not None:
            return logger.LoggerFactory.logbot
    except Exception:
        pass
    return logging.getLogger("safetyreport.core")


def note_fallback(action: str, exc: BaseException, *, level: int = logging.WARNING) -> None:
    """`action` 이 실패해 기본값으로 대체했음을 남긴다. 호출자는 원래 기본값을 그대로 반환한다."""
    key = (action, type(exc).__name__)
    now = time.monotonic()
    with _lock:
        last = _last_logged.get(key)
        if last is not None and now - last < _REPEAT_SECONDS:
            return
        _last_logged[key] = now
    try:
        _log().log(level, "[fallback] %s: %s", action, type(exc).__name__)
    except Exception:
        pass


def log_request_exception(path: str, exc: BaseException) -> None:
    """요청 경계에서 처리하지 못한 예외를 남긴다. 호출 위치(traceback 프레임)와 종류만 남기고 메시지는 뺀다."""
    frames = "".join(traceback.format_tb(exc.__traceback__)) if exc.__traceback__ else ""
    try:
        _log().error("[request] %s 처리 중 예외: %s\n%s", path, type(exc).__name__, frames.rstrip())
    except Exception:
        pass


def reset_for_tests() -> None:
    with _lock:
        _last_logged.clear()
