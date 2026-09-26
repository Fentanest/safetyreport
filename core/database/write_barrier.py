"""운영 DB 교체(복원) 동안의 연결 장벽 (2026-09-26 감사 SOL-04).

복원은 현재 DB 를 스테이징 사본으로 복사한 뒤 파일을 통째로 바꾼다. 그 사이에 다른 요청이 운영 DB 에 커밋하면
새 파일에는 그 변경이 없다. 그래서 운영 DB 를 여는 엔진(`engine.create_sqlite_engine`)의 연결 풀에 장벽을 붙인다.

- `exclusive()` 동안 다른 스레드의 새 연결은 끝날 때까지 기다린다. 기다리는 동안 이미 열린 연결은 옛 파일을
  가리키므로 버리고 새로 연다(세대 번호) — 교체 뒤 쓰기는 새 파일에 들어간다.
- `exclusive()` 는 이미 빌려 간 연결이 모두 반납될 때까지 기다린 뒤 들어간다. 제한 시간 안에 반납되지 않으면
  복원을 거절한다(데이터를 잃지 않는 쪽).
- 크롤러는 별도 프로세스라 여기 걸리지 않는다 — 복원은 크롤링 중이면 거부한다(`exchange.ensure_restore_allowed`).
  텔레그램 봇 프로세스는 읽기만 한다.
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager

from sqlalchemy import event, exc

_cond = threading.Condition()
_active: dict[int, int] = {}  # 연결을 빌린 스레드 → 빌린 수
_owner: int | None = None  # exclusive 를 잡은 스레드
_generation = 0  # exclusive 가 끝날 때마다 +1 — 그 전에 연 연결은 교체된 옛 파일을 가리킨다

CHECKOUT_WAIT_SECONDS = 120.0
DRAIN_WAIT_SECONDS = 20.0


class BarrierTimeout(RuntimeError):
    """장벽 대기가 제한 시간을 넘었다."""


def _on_connect(dbapi_connection, connection_record):
    connection_record.info["sr_barrier_gen"] = _generation


def _on_checkout(dbapi_connection, connection_record, connection_proxy):
    # checkout 은 DB 연결을 연 뒤에 불린다. 기다리는 동안 파일이 바뀌었으면(세대가 다르면) 이 연결은 옛 파일이므로
    # DisconnectionError 로 버리게 한다 — 풀이 새 연결로 다시 시도한다.
    me = threading.get_ident()
    with _cond:
        deadline = time.monotonic() + CHECKOUT_WAIT_SECONDS
        # 이미 연결을 가진 스레드(중첩 사용)나 장벽 소유자는 기다리지 않는다 — 기다리면 반납을 막아 교착된다.
        while _owner is not None and _owner != me and not _active.get(me):
            left = deadline - time.monotonic()
            if left <= 0:
                raise BarrierTimeout("DB 복원이 끝나지 않아 연결을 얻지 못했습니다. 잠시 뒤 다시 시도하세요.")
            _cond.wait(left)
        if connection_record.info.get("sr_barrier_gen", _generation) != _generation:
            raise exc.DisconnectionError("DB 파일이 교체되어 연결을 새로 엽니다.")
        _active[me] = _active.get(me, 0) + 1
        connection_record.info["sr_barrier_thread"] = me


def _on_checkin(dbapi_connection, connection_record):
    me = connection_record.info.pop("sr_barrier_thread", None)
    if me is None:
        return
    with _cond:
        n = _active.get(me, 0) - 1
        if n > 0:
            _active[me] = n
        else:
            _active.pop(me, None)
        _cond.notify_all()


def attach(engine) -> None:
    event.listen(engine, "connect", _on_connect)
    event.listen(engine, "checkout", _on_checkout)
    event.listen(engine, "checkin", _on_checkin)


def active_connections() -> int:
    with _cond:
        return sum(_active.values())


@contextmanager
def exclusive(drain_seconds: float | None = None):
    """다른 스레드의 운영 DB 연결이 모두 반납되길 기다린 뒤, 블록이 끝날 때까지 새 연결을 막는다.

    제한 시간 안에 반납되지 않으면 BarrierTimeout(장벽을 풀고 아무것도 바꾸지 않은 상태).
    """
    global _owner, _generation
    me = threading.get_ident()
    wait = DRAIN_WAIT_SECONDS if drain_seconds is None else drain_seconds
    with _cond:
        deadline = time.monotonic() + wait
        while _owner is not None:
            left = deadline - time.monotonic()
            if left <= 0:
                raise BarrierTimeout("다른 복원이 진행 중입니다.")
            _cond.wait(left)
        _owner = me
        try:
            while any(n for t, n in _active.items() if t != me):
                left = deadline - time.monotonic()
                if left <= 0:
                    raise BarrierTimeout("DB 를 쓰는 다른 작업이 끝나지 않아 복원하지 않았습니다. 잠시 뒤 다시 시도하세요.")
                _cond.wait(left)
        except BaseException:
            _owner = None
            _cond.notify_all()
            raise
    try:
        yield
    finally:
        with _cond:
            _generation += 1
            _owner = None
            _cond.notify_all()
