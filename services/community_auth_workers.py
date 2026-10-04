"""커뮤니티 연결 요청 poll 스레드 감독(EO R-09에서 서비스에서 분리).

요청마다 스레드 하나. 멈춘 스레드는 끝날 때까지 'retiring' 으로 소유하고, 종료 때는 남은 모든 스레드가 한 시간 예산을 나눠 쓴다.
종료가 시작되면 새 스레드를 만들지 않는다.
"""
from __future__ import annotations

import threading
import time


class AuthWorkerSupervisor:
    def __init__(self):
        self._workers: dict[str, tuple[threading.Thread, threading.Event]] = {}
        self._retiring: list[tuple[threading.Thread, threading.Event]] = []
        self._lock = threading.Lock()
        self._shutdown = threading.Event()

    @property
    def shutting_down(self) -> bool:
        return self._shutdown.is_set()

    def start(self, request_id: str, target, *, name: str = "community-auth-poll") -> None:
        """target(request_id, stop_event) 를 데몬 스레드로 시작한다. 같은 요청의 스레드가 살아 있으면 그대로 둔다."""
        if self._shutdown.is_set():
            return
        with self._lock:
            if self._shutdown.is_set():
                return
            existing = self._workers.get(request_id)
            if existing and existing[0].is_alive():
                return
            stop = threading.Event()
            thread = threading.Thread(target=target, args=(request_id, stop), name=name, daemon=True)
            self._workers[request_id] = (thread, stop)
            thread.start()

    def stop(self, request_id: str | None, join: bool = False) -> None:
        if not request_id:
            return
        with self._lock:
            entry = self._workers.pop(request_id, None)
            self._retiring = [item for item in self._retiring if item[0].is_alive()]
            if entry and entry[0].is_alive():
                self._retiring.append(entry)
        if entry:
            entry[1].set()
            if join and entry[0] is not threading.current_thread():
                entry[0].join(timeout=5)

    def finished(self, request_id: str) -> None:
        """스레드가 스스로 끝날 때: 지금 등록된 것이 자기 자신이면 지운다."""
        with self._lock:
            entry = self._workers.get(request_id)
            if entry and entry[0] is threading.current_thread():
                self._workers.pop(request_id, None)

    def active(self) -> int:
        with self._lock:
            return sum(1 for t, _ in self._workers.values() if t.is_alive())

    def shutdown(self, timeout: float = 5.0) -> bool:
        self._shutdown.set()
        deadline = time.monotonic() + max(0, timeout)
        with self._lock:
            entries = list(self._workers.values()) + list(self._retiring)
        for _, stop in entries:
            stop.set()
        for thread, _ in entries:
            if thread is not threading.current_thread():
                thread.join(timeout=max(0, deadline - time.monotonic()))
        with self._lock:
            self._workers = {key: entry for key, entry in self._workers.items() if entry[0].is_alive()}
            self._retiring = [entry for entry in self._retiring if entry[0].is_alive()]
        return not any(thread.is_alive() for thread, _ in entries)
