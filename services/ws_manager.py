"""
WebSocket 연결 관리자 (싱글톤)

FastAPI 서버 내에서 연결된 모든 모바일 클라이언트에게
크롤링 이벤트를 실시간으로 브로드캐스트합니다.
"""
import asyncio
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict
from fastapi import WebSocket

logger = logging.getLogger(__name__)

SEND_TIMEOUT = 5.0
CLOSE_TIMEOUT = 1.0
QUEUE_LIMIT = 32
QUEUE_BYTES = 4 * 1024 * 1024


@dataclass
class _Delivery:
    socket: WebSocket
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=QUEUE_LIMIT))
    bytes: int = 0
    worker: asyncio.Task | None = None


class WsManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._connections: Dict[str, WebSocket] = {}
            cls._instance._connection_meta: Dict[str, dict] = {}
            cls._instance._api_clients: Dict[str, dict] = {}   # HTTP API 최근 사용 추적
            cls._instance._metadata_lock = threading.RLock()
            cls._instance._api_seen = {}
            cls._instance._main_loop: asyncio.AbstractEventLoop | None = None
            cls._instance._deliveries = {}
            cls._instance._thread_slots = threading.BoundedSemaphore(QUEUE_LIMIT)
            cls._instance._publish_lock = threading.RLock()
            cls._instance._overflow_scheduled = False
            cls._instance._terminal_lock = asyncio.Lock()
        return cls._instance

    def set_main_loop(self, loop: asyncio.AbstractEventLoop):
        """FastAPI lifespan startup 시 메인 이벤트 루프를 저장합니다."""
        if self._main_loop is not loop and not self._connections:
            self._terminal_lock = asyncio.Lock()
        self._main_loop = loop

    async def connect(self, client_id: str, ws: WebSocket, api_key: str = "", ip: str = "", device_name: str = ""):
        await ws.accept()
        previous = self._connections.get(client_id)
        if previous is not None:
            await self._close(client_id, previous, 1013, 'Connection replaced')
        self._connections[client_id] = ws
        with self._metadata_lock:
            self._connection_meta[client_id] = {
            "device_name": device_name or "알 수 없는 기기",
            "api_key": api_key,
            "connected_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "ip": ip,
        }
        logger.info(f"[WS] 클라이언트 연결: {client_id} / {device_name} (총 {len(self._connections)}개)")

    def disconnect(self, client_id: str, ws=None):
        if ws is not None and self._connections.get(client_id) is not ws:
            return
        self._connections.pop(client_id, None)
        state = self._deliveries.pop(client_id, None)
        if state is not None:
            if state.worker is not None and state.worker is not asyncio.current_task():
                state.worker.cancel()
            while not state.queue.empty():
                _, size, completed = state.queue.get_nowait()
                state.bytes -= size
                if not completed.done():
                    completed.set_result(False)
        with self._metadata_lock:
            self._connection_meta.pop(client_id, None)
        logger.info(f"[WS] 클라이언트 종료: {client_id} (남은 {len(self._connections)}개)")

    async def send(self, client_id, ws, message):
        """ping/connected와 이벤트도 한 socket writer를 공유한다."""
        if self._connections.get(client_id) is not ws:
            return False
        payload = json.dumps(message, ensure_ascii=False)
        size = len(payload.encode('utf-8'))
        state = self._deliveries.get(client_id)
        if state is None:
            state = self._deliveries[client_id] = _Delivery(ws)
        if state.queue.full() or state.bytes + size > QUEUE_BYTES:
            await self._close(client_id, ws, 1013, 'Reconnect and replay')
            return False
        completed = asyncio.get_running_loop().create_future()
        state.bytes += size
        state.queue.put_nowait((payload, size, completed))
        if state.worker is None or state.worker.done():
            state.worker = asyncio.create_task(self._writer(client_id, state))
        return await asyncio.shield(completed)

    async def _writer(self, client_id, state):
        try:
            while not state.queue.empty():
                payload, size, completed = state.queue.get_nowait()
                try:
                    await asyncio.wait_for(state.socket.send_text(payload), SEND_TIMEOUT)
                    if not completed.done():
                        completed.set_result(True)
                except (Exception, asyncio.CancelledError):
                    if not completed.done():
                        completed.set_result(False)
                    raise
                finally:
                    state.bytes -= size
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._close(client_id, state.socket, 1013, 'Reconnect and replay')

    async def _close(self, client_id, ws, code, reason):
        self.disconnect(client_id, ws)
        try:
            await asyncio.wait_for(ws.close(code=code, reason=reason), CLOSE_TIMEOUT)
        except Exception:
            pass

    async def initialize(self, client_id, ws, after=None):
        """종료 이벤트만 cursor로 복구한다. 변경 자료는 기존 crawl/results로 조회한다."""
        from services import ws_event_store, crawl_run_state
        async with self._terminal_lock:
            info, events = await asyncio.to_thread(ws_event_store.replay, after)
            info.update(client_id=client_id, message='WebSocket 연결 성공',
                        last_attempt=await asyncio.to_thread(crawl_run_state.latest))
            if not await self.send(client_id, ws, {'type': 'connected', 'data': info}):
                return False
            for event in events:
                if not await self.send(client_id, ws, event):
                    return False
            return True

    async def broadcast(self, event_type: str, data: dict | None = None, *, _event=None):
        if event_type == 'crawl_finished':
            from services import ws_event_store
            async with self._terminal_lock:
                event = _event or await asyncio.to_thread(ws_event_store.append, self._event(event_type, data))
                await self._broadcast(event)
        else:
            await self._broadcast(_event or self._event(event_type, data))

    @staticmethod
    def _event(event_type, data):
        return {'type': event_type, 'timestamp': datetime.now().isoformat(), 'data': data or {}}

    async def _broadcast(self, event):
        """연결된 모든 클라이언트에게 이벤트를 병렬로 전송합니다.
        커뮤니티 게이트가 닫혀 있으면(캐시 만료로 확인 필요 포함) 보내지 않고 연결을 4403 으로 닫는다."""
        if not self._connections:
            return
        try:
            from services import community_gate
            # 세션 파일 해독이 있어 이벤트 루프를 막지 않게 스레드에서 평가한다
            gate_open = bool((await asyncio.to_thread(community_gate.evaluate))["can_enter"])
        except Exception:
            gate_open = False
        if not gate_open:
            await self.close_all(4403, "COMMUNITY_ONBOARDING_REQUIRED")
            return

        await asyncio.gather(*[self.send(cid, ws, event) for cid, ws in list(self._connections.items())], return_exceptions=True)

    def broadcast_from_thread(self, event_type: str, data: dict | None = None):
        """백그라운드 스레드에서 안전하게 브로드캐스트합니다 (fire-and-forget)."""
        with self._publish_lock:
            event = self._event(event_type, data)
            if event_type == 'crawl_finished':
                from services import ws_event_store
                event = ws_event_store.append(event)  # 연결/루프 부재에도 terminal을 보존
            if self._main_loop and self._main_loop.is_running():
                if not self._thread_slots.acquire(blocking=False):
                    # 예약 future 자체도 상한. terminal은 보존 후 재연결시 replay.
                    if not self._overflow_scheduled:
                        self._overflow_scheduled = True
                        self._main_loop.call_soon_threadsafe(self._schedule_overflow)
                    return
                try:
                    future = asyncio.run_coroutine_threadsafe(self.broadcast(event_type, data, _event=event), self._main_loop)
                except Exception:
                    self._thread_slots.release()
                    raise
                def completed(result):
                    self._thread_slots.release()
                    self._observe_delivery(result)
                future.add_done_callback(completed)

    def _schedule_overflow(self):
        async def close():
            try:
                await self.close_all(1013, 'Reconnect and replay')
            finally:
                with self._publish_lock:
                    self._overflow_scheduled = False
        asyncio.create_task(close())

    def track_api_request(self, api_key: str, device_name: str, ip: str = ""):
        """HTTP API 요청 시 최근 사용 기록 (in-memory)"""
        with self._metadata_lock:
            self._prune_api_clients()
            self._api_seen[api_key] = time.monotonic()
            self._api_clients[api_key] = {
            "device_name": device_name or "알 수 없는 기기",
            "api_key": api_key,
            "last_used": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "ip": ip,
            "connection_type": "HTTP API",
        }

    def _prune_api_clients(self):
        cutoff = time.monotonic() - 86400
        for key, seen in list(self._api_seen.items()):
            if seen < cutoff:
                self._api_seen.pop(key, None)
                self._api_clients.pop(key, None)
        while len(self._api_clients) >= 1024:
            key = min(self._api_seen, key=self._api_seen.get)
            self._api_seen.pop(key, None)
            self._api_clients.pop(key, None)

    @staticmethod
    def _observe_delivery(future):
        try:
            future.result()
        except Exception as exc:
            logger.warning('[WS] 백그라운드 전송 실패: %s', type(exc).__name__)

    def get_connected_clients(self) -> list:
        with self._metadata_lock:
            self._prune_api_clients()
            ws_clients = [
                {'client_id': meta.get('device_name', cid[:8] + '...'), 'connection_type': 'WebSocket', **meta}
                for cid, meta in self._connection_meta.items()
            ]
            api_clients = [dict(value) for value in self._api_clients.values()]
        return ws_clients + api_clients

    def connected_count(self) -> int:
        return len(self._connections)

    async def close_all(self, code: int, reason: str = "") -> None:
        """커뮤니티 게이트를 잃으면 모든 이벤트 연결을 닫는다(4403)."""
        await asyncio.gather(*[self._close(cid, ws, code, reason) for cid, ws in list(self._connections.items())], return_exceptions=True)

    def close_all_from_thread(self, code: int, reason: str = "") -> None:
        if self._main_loop and self._main_loop.is_running():
            future = asyncio.run_coroutine_threadsafe(self.close_all(code, reason), self._main_loop)
            future.add_done_callback(self._observe_delivery)


ws_manager = WsManager()
