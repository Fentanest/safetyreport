"""
WebSocket 연결 관리자 (싱글톤)

FastAPI 서버 내에서 연결된 모든 모바일 클라이언트에게
크롤링 이벤트를 실시간으로 브로드캐스트합니다.
"""
import asyncio
import json
import logging
from datetime import datetime
from typing import Dict
from fastapi import WebSocket

logger = logging.getLogger(__name__)


class WsManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._connections: Dict[str, WebSocket] = {}
            cls._instance._connection_meta: Dict[str, dict] = {}
            cls._instance._api_clients: Dict[str, dict] = {}   # HTTP API 최근 사용 추적
            cls._instance._main_loop: asyncio.AbstractEventLoop | None = None
        return cls._instance

    def set_main_loop(self, loop: asyncio.AbstractEventLoop):
        """FastAPI lifespan startup 시 메인 이벤트 루프를 저장합니다."""
        self._main_loop = loop

    async def connect(self, client_id: str, ws: WebSocket, api_key: str = "", ip: str = "", device_name: str = ""):
        await ws.accept()
        self._connections[client_id] = ws
        self._connection_meta[client_id] = {
            "device_name": device_name or "알 수 없는 기기",
            "api_key": api_key,
            "connected_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "ip": ip,
        }
        logger.info(f"[WS] 클라이언트 연결: {client_id} / {device_name} (총 {len(self._connections)}개)")

    def disconnect(self, client_id: str):
        self._connections.pop(client_id, None)
        self._connection_meta.pop(client_id, None)
        logger.info(f"[WS] 클라이언트 종료: {client_id} (남은 {len(self._connections)}개)")

    async def broadcast(self, event_type: str, data: dict | None = None):
        """연결된 모든 클라이언트에게 이벤트를 병렬로 전송합니다."""
        if not self._connections:
            return

        payload = json.dumps({
            "type": event_type,
            "timestamp": datetime.now().isoformat(),
            "data": data or {},
        }, ensure_ascii=False)

        client_ids = list(self._connections.keys())
        sockets = [self._connections[cid] for cid in client_ids]

        results = await asyncio.gather(
            *[ws.send_text(payload) for ws in sockets],
            return_exceptions=True,
        )

        for cid, result in zip(client_ids, results):
            if isinstance(result, Exception):
                self.disconnect(cid)

    def broadcast_from_thread(self, event_type: str, data: dict | None = None):
        """백그라운드 스레드에서 안전하게 브로드캐스트합니다 (fire-and-forget)."""
        if self._main_loop and self._main_loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self.broadcast(event_type, data),
                self._main_loop,
            )
        else:
            logger.debug(f"[WS] 메인 루프 없음, 브로드캐스트 스킵: {event_type}")

    def track_api_request(self, api_key: str, device_name: str, ip: str = ""):
        """HTTP API 요청 시 최근 사용 기록 (in-memory)"""
        self._api_clients[api_key] = {
            "device_name": device_name or "알 수 없는 기기",
            "api_key": api_key,
            "last_used": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "ip": ip,
            "connection_type": "HTTP API",
        }

    def get_connected_clients(self) -> list:
        ws_clients = [
            {"client_id": meta.get("device_name", cid[:8] + "..."), "connection_type": "WebSocket", **meta}
            for cid, meta in self._connection_meta.items()
        ]
        api_clients = list(self._api_clients.values())
        return ws_clients + api_clients

    def connected_count(self) -> int:
        return len(self._connections)


ws_manager = WsManager()
