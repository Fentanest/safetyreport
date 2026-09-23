"""개발/테스트용 런타임 seam.

환경변수 두 개로만 동작하며, 둘 다 없으면 운영 동작은 그대로다.

- SAFETYREPORT_DATA_DIR: 데이터 루트(config.ini, data.db, auth/, logs/ ...)를 이 경로로 바꾼다.
  settings 가 import 되기 전에 설정돼 있어야 한다.
- SAFETYREPORT_FIXTURE_MODE=1: 외부 전송·크롤러·별점·업데이트 조회 등 운영 부작용을
  서버 쪽에서 차단한다. 차단된 호출은 ExternalSideEffectBlocked(RuntimeError) 를 던지고
  blocked_actions() 에 기록된다.

fixture 모드는 반드시 SAFETYREPORT_DATA_DIR 과 함께 써야 한다(운영 data/ 오염 방지).
"""
from __future__ import annotations

import logging
import os
import threading

DATA_DIR_ENV = "SAFETYREPORT_DATA_DIR"
FIXTURE_ENV = "SAFETYREPORT_FIXTURE_MODE"

_TRUE_VALUES = {"1", "true", "yes", "on"}
_blocked_lock = threading.Lock()
_blocked: list[str] = []
_log = logging.getLogger("safetyreport.runtime_mode")


class ExternalSideEffectBlocked(RuntimeError):
    """fixture 모드에서 외부 부작용이 있는 동작을 막았을 때 발생한다."""


def data_dir_override() -> str | None:
    value = os.environ.get(DATA_DIR_ENV, "").strip()
    return os.path.abspath(value) if value else None


def is_fixture_mode() -> bool:
    return os.environ.get(FIXTURE_ENV, "").strip().lower() in _TRUE_VALUES


def block_if_fixture(action: str) -> None:
    """fixture 모드면 action 을 기록하고 ExternalSideEffectBlocked 를 던진다."""
    if not is_fixture_mode():
        return
    with _blocked_lock:
        _blocked.append(action)
    _log.warning("[fixture] blocked: %s", action)
    raise ExternalSideEffectBlocked(f"테스트(fixture) 모드에서는 '{action}' 동작을 실행하지 않습니다.")


def skip_in_fixture(action: str) -> bool:
    """예외 없이 건너뛸 백그라운드 작업용. fixture 모드면 기록 후 True."""
    if not is_fixture_mode():
        return False
    with _blocked_lock:
        _blocked.append(action)
    _log.info("[fixture] skipped: %s", action)
    return True


def blocked_actions() -> list[str]:
    with _blocked_lock:
        return list(_blocked)


def reset_blocked_actions() -> None:
    with _blocked_lock:
        _blocked.clear()
