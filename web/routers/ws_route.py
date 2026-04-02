"""
WebSocket 이벤트 엔드포인트

모바일 앱이 ws://<host>/ws/events?api_key=<key> 로 연결합니다.
- API Key 쿼리 파라미터로 인증 (세션 불필요)
- 30초마다 ping 전송으로 연결 유지
- 클라이언트에서 "pong" 수신 시 타임아웃 리셋
"""
import asyncio
import uuid
import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from core.database import database
from sqlalchemy import create_engine
import settings.settings as settings
from services.ws_manager import ws_manager

logger = logging.getLogger(__name__)
router = APIRouter()

_engine = None

def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(
            f'sqlite:///{settings.db_path}',
            connect_args={"check_same_thread": False}
        )
    return _engine


@router.websocket("/ws/events")
async def ws_events(
    websocket: WebSocket,
    api_key: str = Query(default=""),
):
    # API Key 인증
    if not api_key or not database.validate_api_key(_get_engine(), api_key):
        await websocket.close(code=4001, reason="Unauthorized")
        return

    client_id = str(uuid.uuid4())
    device_name = database.get_api_key_name(_get_engine(), api_key)
    forwarded = websocket.headers.get("x-forwarded-for", "")
    ip = forwarded.split(",")[0].strip() if forwarded else (websocket.client.host if websocket.client else "")
    await ws_manager.connect(client_id, websocket, api_key=api_key, ip=ip, device_name=device_name)

    # 연결 확인 메시지
    await websocket.send_json({
        "type": "connected",
        "data": {"client_id": client_id, "message": "WebSocket 연결 성공"}
    })

    ping_interval = 30  # seconds

    async def _pinger():
        """주기적으로 ping을 전송하여 연결을 유지합니다."""
        while True:
            await asyncio.sleep(ping_interval)
            try:
                await websocket.send_json({"type": "ping"})
            except Exception:
                break

    pinger_task = asyncio.create_task(_pinger())

    try:
        while True:
            # 클라이언트로부터 메시지 수신 (pong 등)
            _ = await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug(f"[WS] 클라이언트 오류 ({client_id}): {e}")
    finally:
        pinger_task.cancel()
        ws_manager.disconnect(client_id)
