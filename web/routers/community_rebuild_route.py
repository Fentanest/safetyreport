"""1회 초기화 크롤링 로컬 API (T3b).

- 관리자 웹: /settings/community/rebuild/* (세션 인증 미들웨어 + CSRF·JSON·Origin 확인)
- 모바일 Client: /api/v1/community/rebuild/* (X-API-Key, 관리 허용 키만 +
  X-Community-User-Token 을 T3a community_gate.verify_client_user_token 으로 확인)

main.py 등록은 T3a 가 한다. 모듈 변수 이름 router·api_router 고정.
응답은 no-store, 토큰·비밀 없음.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from core.utils import csrf
from services import community_rebuild as rebuild
from web.routers.api_route import _require_api_key

router = APIRouter(prefix="/settings/community/rebuild")
api_router = APIRouter(prefix="/api/v1/community/rebuild")

_NO_STORE = {"Cache-Control": "no-store"}
_USER_TOKEN_HEADER = "x-community-user-token"


def _ok(payload: dict, status_code: int = 200) -> JSONResponse:
    return JSONResponse(payload, status_code=status_code, headers=_NO_STORE)


def _forbidden(reason: str) -> JSONResponse:
    return JSONResponse({"detail": "요청을 확인할 수 없습니다. 설정 화면을 새로고침한 뒤 다시 시도해 주세요.",
                         "code": "csrf_failed", "reason": reason},
                        status_code=403, headers=_NO_STORE)


async def _json_body(request: Request) -> dict:
    raw = await request.body()
    if not raw.strip():
        return {}
    if len(raw) > 8192:
        return {"__too_large__": True}
    try:
        body = json.loads(raw)
    except ValueError:
        return {"__invalid__": True}
    if not isinstance(body, dict):
        return {"__invalid__": True}
    return body


def _only(body: dict, allowed: set[str]) -> JSONResponse | None:
    if "__too_large__" in body:
        return _ok({"detail": "요청 본문이 너무 큽니다.", "code": "invalid_request"}, 400)
    if "__invalid__" in body:
        return _ok({"detail": "JSON 본문이 올바르지 않습니다.", "code": "invalid_request"}, 400)
    if set(body) - allowed:
        return _ok({"detail": "알 수 없는 입력 항목이 있습니다.", "code": "invalid_request"}, 400)
    return None


def _state_response(payload: dict) -> JSONResponse:
    if payload.get("state") == "prerequisites_required":
        return _ok({"data": payload, "code": "prerequisites_required"}, 409)
    return _ok({"data": payload})


# ── 관리자 웹 ─────────────────────────────────────────────────────────────────

@router.get("")
async def web_status():
    payload = await run_in_threadpool(rebuild.status)
    return _ok({"data": payload})


async def _web_action(request: Request, allowed: set[str], action):
    reason = csrf.verify_json_post(request)
    if reason:
        return _forbidden(reason)
    body = await _json_body(request)
    rejected = _only(body, allowed)
    if rejected is not None:
        return rejected
    payload = await run_in_threadpool(action, body)
    return _state_response(payload)


@router.post("/start")
async def web_start(request: Request):
    return await _web_action(request, {"confirmed_by"},
                             lambda body: rebuild.start(str(body.get("confirmed_by") or "web")))


@router.post("/resume")
async def web_resume(request: Request):
    return await _web_action(request, set(), lambda _body: rebuild.resume())


@router.post("/pause")
async def web_pause(request: Request):
    return await _web_action(request, {"reason"},
                             lambda body: rebuild.pause(str(body.get("reason") or "user_paused")))


@router.post("/accept-gaps")
async def web_accept_gaps(request: Request):
    return await _web_action(request, set(), lambda _body: rebuild.accept_gaps())


# ── 모바일 Client (API 키 + 사용자 토큰) ───────────────────────────────────────

def _can_manage(api_key: str) -> bool:
    try:
        from services import community_auth_service as cas
        return cas.hash_api_key(api_key) in cas.get_service().config().api_key_managers
    except Exception:
        return False


def _verify_client_user_token(request: Request) -> bool:
    """폰 사용자 access token 이 서버 연결 사용자와 같을 때만 True. 토큰이 없거나 확인 실패면 False."""
    from services import community_gate as gate
    token = request.headers.get(_USER_TOKEN_HEADER) or ""
    if not token:
        return False
    try:
        return bool(gate.verify_client_user_token(token))
    except Exception:
        return False


async def _api_action(request: Request, api_key: str, allowed: set[str], action):
    if not _can_manage(api_key):
        return _ok({"detail": "커뮤니티 관리 권한이 없는 키입니다.",
                     "code": "permission_required"}, 403)
    if not _verify_client_user_token(request):
        return _ok({"detail": "사용자 확인이 필요합니다.", "code": "user_token_required"}, 403)
    body = await _json_body(request)
    rejected = _only(body, allowed)
    if rejected is not None:
        return rejected
    payload = await run_in_threadpool(action, body)
    return _state_response(payload)


@api_router.get("")
async def api_status(api_key: str = Depends(_require_api_key)):
    if not _can_manage(api_key):
        return _ok({"detail": "커뮤니티 관리 권한이 없는 키입니다.",
                     "code": "permission_required"}, 403)
    payload = await run_in_threadpool(rebuild.status)
    return _ok({"data": payload})


@api_router.post("/start")
async def api_start(request: Request, api_key: str = Depends(_require_api_key)):
    return await _api_action(request, api_key, {"confirmed_by"},
                             lambda body: rebuild.start(str(body.get("confirmed_by") or "api")))


@api_router.post("/resume")
async def api_resume(request: Request, api_key: str = Depends(_require_api_key)):
    return await _api_action(request, api_key, set(), lambda _body: rebuild.resume())
