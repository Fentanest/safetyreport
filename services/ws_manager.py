"""
WebSocket 연결 관리자 (싱글톤)

FastAPI 서버 내에서 연결된 모든 모바일 클라이언트에게
크롤링 이벤트를 실시간으로 브로드캐스트합니다.
"""
import asyncio
import json
import logging
from datetime import datetime
from typing import Dict, Set
from fastapi import WebSocket

logger = logging.getLogger(__name__)


class WsManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            # client_id -> WebSocket
            cls._instance._connections: Dict[str, WebSocket] = {}
        return cls._instance

    def _all(self) -> list:
        return list(self._connections.values())

    async def connect(self, client_id: str, ws: WebSocket):
        await ws.accept()
        self._connections[client_id] = ws
        logger.info(f"[WS] 클라이언트 연결: {client_id} (총 {len(self._connections)}개)")

    def disconnect(self, client_id: str):
        self._connections.pop(client_id, None)
        logger.info(f"[WS] 클라이언트 종료: {client_id} (남은 {len(self._connections)}개)")

    async def broadcast(self, event_type: str, data: dict | None = None):
        """연결된 모든 클라이언트에게 이벤트를 전송합니다."""
        if not self._connections:
            return

        payload = json.dumps({
            "type": event_type,
            "timestamp": datetime.now().isoformat(),
            "data": data or {},
        }, ensure_ascii=False)

        dead: list[str] = []
        for client_id, ws in list(self._connections.items()):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(client_id)

        for cid in dead:
            self.disconnect(cid)

    def connected_count(self) -> int:
        return len(self._connections)


ws_manager = WsManager()
