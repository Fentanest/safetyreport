"""WebSocket 연결 인증 + 커뮤니티 필수 게이트.

HTTP 미들웨어(관리자 세션·게이트)는 WebSocket 에 적용되지 않는다. 그래서 WS 엔드포인트는 연결 때 여기서
(1) 관리자 세션 쿠키 또는 API 키(쿼리 api_key / 헤더 X-API-Key)를 확인하고 (2) 게이트를 확인한다.
세션 쿠키는 SessionMiddleware 와 같은 방식(itsdangerous TimestampSigner + base64 JSON)으로 **읽기만** 한다 —
WS 에서 Set-Cookie 를 내보내지 않는다(_WebSocketSafeSessionMiddleware 의도 유지).
"""
from __future__ import annotations

import json
import time
from base64 import b64decode

from fastapi import WebSocket
from itsdangerous import BadSignature, TimestampSigner
from starlette.concurrency import run_in_threadpool

SESSION_COOKIE = "safetyreport_session"
CLOSE_UNAUTHORIZED = 4001
CLOSE_GATE = 4403
_session_secret: str | None = None


def configure(secret_key: str) -> None:
    """main.py 가 SessionMiddleware 와 같은 키를 알려 준다(한 곳에서 읽은 키를 함께 쓴다)."""
    global _session_secret
    _session_secret = str(secret_key)


def session_data(websocket: WebSocket) -> dict:
    raw = websocket.cookies.get(SESSION_COOKIE)
    if not raw:
        return {}
    import settings.settings as settings

    secret = _session_secret
    if secret is None:
        from core.utils.security import get_or_create_session_key

        secret = str(get_or_create_session_key(settings.datapath))
    signer = TimestampSigner(secret)
    try:
        data = signer.unsign(raw.encode("utf-8"), max_age=settings.session_max_age)
        value = json.loads(b64decode(data))
    except (BadSignature, ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def api_key_valid(websocket: WebSocket) -> bool:
    key = websocket.query_params.get("api_key") or websocket.headers.get("x-api-key") or ""
    if not key:
        return False
    from core.database import database
    from core.database.engine import get_engine

    return bool(database.validate_api_key(get_engine(), key))


def authenticated(websocket: WebSocket, *, allow_session: bool = True, allow_api_key: bool = True) -> bool:
    if allow_session and session_data(websocket).get("admin_logged_in"):
        return True
    return allow_api_key and api_key_valid(websocket)


async def gate_ok() -> bool:
    from services import community_gate

    return bool((await run_in_threadpool(community_gate.evaluate))["can_enter"])


async def reject_gate(websocket: WebSocket) -> None:
    """게이트 미충족: 클라이언트가 이유를 알 수 있게 accept 뒤 4403 으로 닫는다."""
    await websocket.accept()
    await websocket.close(code=CLOSE_GATE, reason="COMMUNITY_ONBOARDING_REQUIRED")


async def authorize(websocket: WebSocket, *, allow_session: bool = True, allow_api_key: bool = True) -> bool:
    """인증·게이트를 확인하고 실패하면 연결을 닫는다. 통과하면 True(아직 accept 하지 않음)."""
    ok = await run_in_threadpool(authenticated, websocket, allow_session=allow_session, allow_api_key=allow_api_key)
    if not ok:
        await websocket.close(code=CLOSE_UNAUTHORIZED, reason="Unauthorized")
        return False
    if not await gate_ok():
        await reject_gate(websocket)
        return False
    return True


class GateWatch:
    """긴 WS 루프 안에서 interval 초마다 게이트를 다시 본다. 잃으면 lost() 가 True."""

    def __init__(self, interval: float = 5.0):
        self.interval = interval
        self._next = time.monotonic() + interval

    async def lost(self) -> bool:
        now = time.monotonic()
        if now < self._next:
            return False
        self._next = now + self.interval
        return not await gate_ok()
