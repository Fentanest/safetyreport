"""Self-host wire protocol, independent of product and database versions."""
from __future__ import annotations

import re

PROTOCOL_VERSION = 3
SUPPORTED_CLIENT_PROTOCOLS = [3]
MINIMUM_SERVER_MAJOR = 3
_VERSION = re.compile(r"^v?(\d+)\.\d+\.\d+(?:\.\d+)?(?:-(?:dev|alpha|beta|rc)[.\w-]*)?(?:\+[\w.-]+)?$")


def product_major(value: str | None) -> int | None:
    match = _VERSION.fullmatch(value or "")
    return int(match[1]) if match else None


def metadata() -> dict:
    return {"protocol_version": PROTOCOL_VERSION,
            "supported_client_protocols": list(SUPPORTED_CLIENT_PROTOCOLS),
            "minimum_server_major": MINIMUM_SERVER_MAJOR}


def rejection(client_type, client_version, client_protocol, server_version=None) -> dict | None:
    if server_version is None:
        from core.utils.updater import get_current_version
        server_version = get_current_version()
    major = product_major(server_version)
    code = None
    if major is None or major < MINIMUM_SERVER_MAJOR:
        code = "SERVER_UPGRADE_REQUIRED"
    elif client_protocol is None or client_protocol == "" or client_protocol in ("1", "2"):
        code = "CLIENT_UPGRADE_REQUIRED"
    elif client_protocol != "3":
        code = "CLIENT_PROTOCOL_UNSUPPORTED"
    elif client_type not in ("mobile", "chromeextension") or product_major(client_version) is None:
        code = "CLIENT_UPGRADE_REQUIRED"
    if code is None:
        return None
    messages = {"SERVER_UPGRADE_REQUIRED": "서버를 v3 이상으로 업데이트하세요.",
                "CLIENT_UPGRADE_REQUIRED": "self-host 통신 계약 v3을 지원하는 클라이언트로 업데이트하세요.",
                "CLIENT_PROTOCOL_UNSUPPORTED": "지원하지 않는 self-host 통신 계약입니다."}
    return {"status": "error", "code": code, "detail": messages[code],
            "message": messages[code], "compatibility": metadata()}


def update_lock_rejection() -> dict | None:
    """필수 버전 잠금(2-10) 중인 이 서버: Client API 는 기존 SERVER_UPGRADE_REQUIRED 409 형식 그대로 거절한다.
    휴대폰(Client)은 이 코드로 "서버 프로그램 업데이트가 필요합니다" 안내를 보인다(휴대폰 잠금 아님)."""
    try:
        from services import community_update_lock
        if not community_update_lock.locked():
            return None
    except Exception as exc:
        from core.utils.fallback import note_fallback
        note_fallback("selfhost_compat.update_lock", exc)
        return None  # 읽지 못하면 막지 않는다(먹통 방지)
    from services.community_update_lock import SERVER_UPGRADE_MESSAGE
    return {"status": "error", "code": "SERVER_UPGRADE_REQUIRED", "detail": SERVER_UPGRADE_MESSAGE,
            "message": SERVER_UPGRADE_MESSAGE, "compatibility": metadata()}


def http_rejection(request):
    return rejection(request.headers.get("x-safetyreport-client"),
                     request.headers.get("x-safetyreport-version"),
                     request.headers.get("x-safetyreport-protocol")) or update_lock_rejection()


def ws_rejection(websocket):
    q = websocket.query_params
    return rejection(q.get("client_type"), q.get("client_version"), q.get("client_protocol")) or update_lock_rejection()
