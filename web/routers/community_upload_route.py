"""커뮤니티 공유 업로드 로컬 API.

- 관리자 웹 router (prefix /community, 세션 인증은 미들웨어 담당):
  GET /community/upload/status, POST /community/upload/run, POST /community/upload/reshare
  (CSRF: core/utils/csrf.py verify_json_post, JSON 본문)
- API 키 api_router (prefix /api/v1/community):
  GET /upload/status, POST /upload/run (manager 권한 키 +
  헤더 X-Community-User-Token 을 community_gate.verify_client_user_token(token)으로 검증,
  서버 연결 사용자와 다르면 403 account_mismatch).

라우터 등록(main.py)은 T3 소유 — 모듈 변수 이름 router / api_router 고정.
응답 Cache-Control: no-store, 토큰·UUID 없음.
"""
from __future__ import annotations

import json
import math

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from core.utils import csrf
from services import community_auth_service as cas
from services.community_auth_service import CommunityAuthError, hash_api_key
from web.routers.api_route import _require_api_key

router = APIRouter(prefix="/community")
api_router = APIRouter(prefix="/api/v1/community")

_NO_STORE = {"Cache-Control": "no-store"}


def _ok(payload: dict) -> JSONResponse:
    return JSONResponse(payload, headers=_NO_STORE)


def _fail(exc: CommunityAuthError) -> JSONResponse:
    headers = dict(_NO_STORE)
    if exc.retry_after:
        headers["Retry-After"] = str(max(1, math.ceil(exc.retry_after)))
    return JSONResponse({"detail": exc.message, "code": exc.code}, status_code=exc.status, headers=headers)


def _forbidden(reason: str) -> JSONResponse:
    return JSONResponse({"detail": "요청을 확인할 수 없습니다. 설정 화면을 새로고침한 뒤 다시 시도해 주세요.",
                         "code": "csrf_failed", "reason": reason}, status_code=403, headers=_NO_STORE)


async def _json_body(request: Request) -> dict:
    raw = await request.body()
    if not raw.strip():
        return {}
    if len(raw) > 8192:
        raise CommunityAuthError("invalid_settings", "요청 본문이 너무 큽니다.")
    try:
        body = json.loads(raw)
    except ValueError:
        raise CommunityAuthError("invalid_settings", "JSON 본문이 올바르지 않습니다.") from None
    if not isinstance(body, dict):
        raise CommunityAuthError("invalid_settings", "JSON 본문이 올바르지 않습니다.")
    return body


def _can_manage(api_key: str) -> bool:
    return hash_api_key(api_key) in cas.get_service().config().api_key_managers


def _check_client_user(request: Request, required: bool = True) -> str | None:
    """폰 사용자 토큰(X-Community-User-Token)을 서버 연결 사용자와 비교. 민감 제어(업로드 실행)는 토큰 필수.
    상태 조회(required=False)는 토큰이 없으면 통과, 있으면 같은 사용자여야 한다."""
    from services import community_gate as _gate

    token = request.headers.get("x-community-user-token")
    if not token:
        return "user_token_required" if required else None
    try:
        ok = bool(_gate.verify_client_user_token(token))
    except Exception:
        ok = False
    return None if ok else "account_mismatch"


@router.get("/upload/status")
async def web_upload_status(request: Request):
    from services import community_uploader as _uploader
    dto = await run_in_threadpool(_uploader.upload_status)
    return _ok({"data": dto})


async def _web_action(request: Request, action):
    reason = csrf.verify_json_post(request)
    if reason:
        return _forbidden(reason)
    try:
        body = await _json_body(request)
        if set(body):
            raise CommunityAuthError("invalid_settings", "알 수 없는 입력 항목이 있습니다.")
        return await action()
    except CommunityAuthError as exc:
        return _fail(exc)


@router.post("/upload/run")
async def web_upload_run(request: Request):
    async def run():
        from services import community_uploader as _uploader
        dto = await run_in_threadpool(_uploader.request_upload, "manual")
        return _ok({"data": _public_run(dto)})
    return await _web_action(request, run)


@router.post("/upload/reshare")
async def web_upload_reshare(request: Request):
    async def run():
        from services import community_uploader as _uploader
        dto = await run_in_threadpool(_uploader.request_reshare)
        return _ok({"data": _public_run(dto)})
    return await _web_action(request, run)


def _mismatch(code: str = "account_mismatch") -> JSONResponse:
    detail = ("사용자 확인이 필요합니다. 앱에서 커뮤니티 계정으로 로그인해 주세요." if code == "user_token_required"
              else "서버에 연결된 커뮤니티 계정과 다릅니다.")
    return JSONResponse({"detail": detail, "code": code}, status_code=403, headers=_NO_STORE)


def _public_run(raw: dict) -> dict:
    """내부 run_id(UUID)·request_id 전체는 빼고 결과·건수·추적ID(앞 8자)만. 토큰·UUID 없음."""
    ids = raw.get("request_ids") or []
    tracking = (ids[-1][:8] if ids else raw.get("last_request") or "") or None
    if isinstance(tracking, str):
        tracking = tracking[:8]
    return {"result": raw.get("result"), "counts": raw.get("counts", {}),
            "error_code": raw.get("error_code"), "tracking": tracking,
            **({"reshared": raw["reshared"]} if "reshared" in raw else {}),
            **({"count": raw["count"]} if "count" in raw else {})}


@api_router.get("/upload/status")
async def api_upload_status(request: Request, api_key: str = Depends(_require_api_key)):
    mismatch = _check_client_user(request, required=False)
    if mismatch:
        return _mismatch(mismatch)
    try:
        if not _can_manage(api_key):
            raise CommunityAuthError("permission_required")
        from services import community_uploader as _uploader
        dto = await run_in_threadpool(_uploader.upload_status)
        return _ok({"data": dto})
    except CommunityAuthError as exc:
        return _fail(exc)


@api_router.post("/upload/run")
async def api_upload_run(request: Request, api_key: str = Depends(_require_api_key)):
    mismatch = _check_client_user(request)
    if mismatch:
        return _mismatch(mismatch)
    try:
        if not _can_manage(api_key):
            raise CommunityAuthError("permission_required")
        body = await _json_body(request)
        if set(body):
            raise CommunityAuthError("invalid_settings", "알 수 없는 입력 항목이 있습니다.")
        from services import community_uploader as _uploader
        dto = await run_in_threadpool(_uploader.request_upload, "manual")
        return _ok({"data": _public_run(dto)})
    except CommunityAuthError as exc:
        return _fail(exc)
