"""커뮤니티 계정 연결 로컬 API.

- 관리자 웹: /settings/community/* (세션 인증 미들웨어 + CSRF·JSON·Origin 확인)
- 모바일 Client: /api/v1/community-auth/* (X-API-Key). 관리 동작은 [COMMUNITY] api_key_managers 에
  키의 SHA-256 이 있어야 한다(기존 API 키는 범위 구분이 없어 기본 거부).

비밀값·연결 링크를 URL(쿼리)로 받거나 주지 않는다 — 접속 로그에 요청 줄이 남기 때문. 모든 입력은 JSON 본문.
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

router = APIRouter(prefix="/settings/community")
api_router = APIRouter(prefix="/api/v1/community-auth")

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


def _only(body: dict, allowed: set[str]) -> None:
    if set(body) - allowed:
        raise CommunityAuthError("invalid_settings", "알 수 없는 입력 항목이 있습니다.")


def _api_key_rows() -> list[dict]:
    from core.database import database
    from core.database.engine import get_engine

    return database.get_all_api_keys(get_engine())


def _config_view(cfg: cas.CommunityConfig) -> dict:
    """관리자 화면용 설정 요약. supabase_url·publishable_key 는 공개값이다. API 키 원문은 넣지 않는다."""
    service = cas.get_service()
    keys = []
    for row in _api_key_rows():
        digest = hash_api_key(row["key"])
        keys.append({"id": digest, "name": row.get("name") or "이름 없음",
                     "created_at": row.get("created_at"), "allowed": digest in cfg.api_key_managers})
    return {
        "enabled": cfg.enabled, "supabase_url": cfg.supabase_url, "publishable_key": cfg.publishable_key,
        "site_url": cfg.site_url, "device_label": cfg.device_label,
        "default_device_label": service.default_device_label(cfg),
        "env_locked": sorted(cfg.env_locked), "problems": list(cfg.problems), "api_keys": keys,
    }


def _label(body: dict) -> str | None:
    value = body.get("device_label")
    if value is None:
        return None
    if not isinstance(value, str):
        raise CommunityAuthError("invalid_label")
    return value


def _request_id(body: dict, required: bool) -> str | None:
    value = body.get("request_id")
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value:
        raise CommunityAuthError("request_mismatch")
    return value


# ── 관리자 웹 ─────────────────────────────────────────────────────────────────

@router.get("/status")
async def web_status(request: Request):
    service = cas.get_service()
    dto = await run_in_threadpool(service.status, can_manage=True)
    return _ok({"data": dto, "config": await run_in_threadpool(_config_view, service.config())})


async def _web_action(request: Request, action):
    reason = csrf.verify_json_post(request)
    if reason:
        return _forbidden(reason)
    try:
        body = await _json_body(request)
        return await action(body)
    except CommunityAuthError as exc:
        return _fail(exc)


@router.post("/start")
async def web_start(request: Request):
    async def run(body):
        _only(body, {"device_label"})
        dto = await run_in_threadpool(cas.get_service().start, device_label=_label(body))
        return _ok({"data": dto})
    return await _web_action(request, run)


@router.post("/confirm")
async def web_confirm(request: Request):
    async def run(body):
        _only(body, {"request_id"})
        dto = await run_in_threadpool(cas.get_service().confirm, _request_id(body, True))
        return _ok({"data": dto})
    return await _web_action(request, run)


@router.post("/cancel")
async def web_cancel(request: Request):
    async def run(body):
        _only(body, {"request_id"})
        dto = await run_in_threadpool(cas.get_service().cancel, _request_id(body, False))
        return _ok({"data": dto})
    return await _web_action(request, run)


@router.post("/disconnect")
async def web_disconnect(request: Request):
    async def run(body):
        _only(body, set())
        service = cas.get_service()
        result = await run_in_threadpool(service.disconnect)
        dto = await run_in_threadpool(service.status, can_manage=True)
        return _ok({"data": dto, "result": result})
    return await _web_action(request, run)


@router.post("/settings")
async def web_settings(request: Request):
    async def run(body):
        known = {hash_api_key(row["key"]) for row in await run_in_threadpool(_api_key_rows)}
        cfg = await run_in_threadpool(cas.update_settings, body, known)
        service = cas.get_service()
        dto = await run_in_threadpool(service.status, can_manage=True)
        return _ok({"data": dto, "config": await run_in_threadpool(_config_view, cfg)})
    return await _web_action(request, run)


# ── 모바일 Client (API 키) ─────────────────────────────────────────────────────

def _can_manage(api_key: str) -> bool:
    return hash_api_key(api_key) in cas.get_service().config().api_key_managers


async def _api_action(request: Request, api_key: str, action):
    try:
        if not _can_manage(api_key):
            raise CommunityAuthError("permission_required")
        body = await _json_body(request)
        return await action(body)
    except CommunityAuthError as exc:
        return _fail(exc)


@api_router.get("/status")
async def api_status(api_key: str = Depends(_require_api_key)):
    dto = await run_in_threadpool(cas.get_service().status, can_manage=_can_manage(api_key))
    return _ok({"data": dto})


@api_router.post("/start")
async def api_start(request: Request, api_key: str = Depends(_require_api_key)):
    async def run(body):
        _only(body, {"device_label"})
        dto = await run_in_threadpool(cas.get_service().start, client_kind="mobile_client_server",
                                      device_label=_label(body))
        return _ok({"data": dto})
    return await _api_action(request, api_key, run)


@api_router.post("/confirm")
async def api_confirm(request: Request, api_key: str = Depends(_require_api_key)):
    async def run(body):
        _only(body, {"request_id"})
        dto = await run_in_threadpool(cas.get_service().confirm, _request_id(body, True))
        return _ok({"data": dto})
    return await _api_action(request, api_key, run)


@api_router.post("/cancel")
async def api_cancel(request: Request, api_key: str = Depends(_require_api_key)):
    async def run(body):
        _only(body, {"request_id"})
        dto = await run_in_threadpool(cas.get_service().cancel, _request_id(body, False))
        return _ok({"data": dto})
    return await _api_action(request, api_key, run)


@api_router.post("/disconnect")
async def api_disconnect(request: Request, api_key: str = Depends(_require_api_key)):
    async def run(body):
        _only(body, set())
        service = cas.get_service()
        result = await run_in_threadpool(service.disconnect)
        dto = await run_in_threadpool(service.status, can_manage=True)
        return _ok({"data": dto, "result": result})
    return await _api_action(request, api_key, run)
