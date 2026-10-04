"""커뮤니티 계정 연결 로컬 API.

- 관리자 웹: /settings/community/* (세션 인증 미들웨어 + CSRF·JSON·Origin 확인)
- 모바일 Client: /api/v1/community-auth/* (X-API-Key). 관리 동작은 [COMMUNITY] api_key_managers 에
  키의 SHA-256 이 있어야 한다(기존 API 키는 범위 구분이 없어 기본 거부).
- 필수 게이트(services/community_gate.py): 동의 저장·철회, 업로드 연결 전환, 공유 자료 삭제 요청도 여기서 받는다.
  로그인 확정·로그아웃·동의 변경 뒤에는 게이트를 무효화하고 중앙 status 를 다시 받는다.

비밀값·연결 링크를 URL(쿼리)로 받거나 주지 않는다 — 접속 로그에 요청 줄이 남기 때문. 모든 입력은 JSON 본문.
"""
from __future__ import annotations

import json
import logging
import math
import re

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from core.utils import csrf
from services import community_auth_service as cas
from services import community_account_ops as ops
from services import community_gate
from services.community_auth_service import CommunityAuthError, hash_api_key
from web.routers.api_route import _require_api_key

logger = logging.getLogger(__name__)

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
        "disabled_ignored": cfg.disabled_ignored,
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
        gate = await run_in_threadpool(ops.regate, "login")
        return _ok({"data": dto, "gate": gate})
    return await _web_action(request, run)


@router.post("/cancel")
async def web_cancel(request: Request):
    async def run(body):
        _only(body, {"request_id"})
        dto = await run_in_threadpool(cas.get_service().cancel, _request_id(body, False))
        return _ok({"data": dto})
    return await _web_action(request, run)


# 2026-09-27: 카카오 로그인은 필수(사용자 결정) — "연결 해제"(데이터를 남긴 채 로그인만 푸는 기능)는 지금은 쓰지 않는다.
# 대신 카카오 로그아웃(/logout)이 신고 자료를 지운다. 필요해지면 되살린다(주석 처리).
# @router.post("/disconnect")
# async def web_disconnect(request: Request):
#     async def run(body):
#         _only(body, set())
#         service = cas.get_service()
#         community_gate.invalidate("logout")  # 로그아웃 전에 context 를 먼저 끈다(업로드 즉시 중단)
#         result = await run_in_threadpool(service.disconnect)
#         dto = await run_in_threadpool(service.status, can_manage=True)
#         return _ok({"data": dto, "result": result, "gate": await run_in_threadpool(community_gate.status_view)})
#     return await _web_action(request, run)


def _refused(exc: Exception) -> JSONResponse:
    return JSONResponse({"detail": str(exc), "code": "busy"}, status_code=409, headers=_NO_STORE)


@router.post("/logout")
async def web_logout(request: Request):
    """카카오 로그아웃(순서는 services/community_account_ops.logout). 화면은 먼저 "신고 내역이 모두 지워집니다"를 확인받는다."""
    async def run(body):
        _only(body, {"confirm"})
        if body.get("confirm") != ops.LOGOUT_CONFIRM:
            raise CommunityAuthError("invalid_settings", "확인 항목이 필요합니다.")
        try:
            return _ok(await run_in_threadpool(ops.logout))
        except ops.OperationRefused as exc:
            return _refused(exc)
    return await _web_action(request, run)


@router.post("/reset-session")
async def web_reset_session(request: Request):
    """세션 파일을 읽을 수 없을 때만 세션을 옆으로 옮긴다(services/community_account_ops.reset_session)."""
    async def run(body):
        _only(body, set())
        return _ok(await run_in_threadpool(ops.reset_session))
    return await _web_action(request, run)


@router.post("/db-owner/adopt")
async def web_db_owner_adopt(request: Request):
    """다른 카카오 계정의 신고 자료를 지우고 지금 계정으로 시작한다(services/community_account_ops.adopt_db_owner)."""
    async def run(body):
        _only(body, {"confirm"})
        if body.get("confirm") != ops.ADOPT_CONFIRM:
            raise CommunityAuthError("invalid_settings", "확인 항목이 필요합니다.")
        try:
            return _ok(await run_in_threadpool(ops.adopt_db_owner))
        except ops.OperationRefused as exc:
            return _refused(exc)
    return await _web_action(request, run)


@router.post("/settings")
async def web_settings(request: Request):
    async def run(body):
        known = {hash_api_key(row["key"]) for row in await run_in_threadpool(_api_key_rows)}
        cfg = await run_in_threadpool(cas.update_settings, body, known)
        service = cas.get_service()
        dto = await run_in_threadpool(service.status, can_manage=True)
        gate = await run_in_threadpool(ops.regate, "settings_saved")
        return _ok({"data": dto, "config": await run_in_threadpool(_config_view, cfg), "gate": gate})
    return await _web_action(request, run)


# ── 필수 게이트: 동의·철회·업로드 연결·삭제 요청 ──────────────────────────────────

def _fail_account(exc: CommunityAuthError) -> JSONResponse:
    resp = _fail(exc)
    extra = getattr(exc, "extra", None) or {}
    if extra.get("active_writer"):
        body = json.loads(resp.body)
        body["active_writer"] = {k: extra["active_writer"].get(k) for k in ("device_label", "platform", "source_app", "created_at")}
        resp = JSONResponse(body, status_code=resp.status_code, headers=dict(resp.headers))
    return resp


_POLICY_VERSION_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}\.[0-9]{1,3}$")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


@router.get("/policy")
async def web_policy(request: Request):
    try:
        return _ok({"data": await run_in_threadpool(ops.policy_view)})
    except CommunityAuthError as exc:  # 카카오 연결 전·만료·중앙 오류 — 화면은 code 를 보고 안내한다
        return _fail_account(exc)


@router.get("/gate")
async def web_gate(request: Request):
    return _ok({"data": await run_in_threadpool(community_gate.status_view)})


async def _gate_action(request: Request, allowed: set[str], fn, required: dict | None = None):
    async def run(body):
        _only(body, allowed)
        for key, value in (required or {}).items():
            if body.get(key) != value:
                raise CommunityAuthError("invalid_settings", "확인 항목이 필요합니다.")
        try:
            return _ok({"data": await run_in_threadpool(fn)})
        except CommunityAuthError as exc:
            return _fail_account(exc)
    return await _web_action(request, run)


@router.post("/consent")
async def web_consent(request: Request):
    # 기본 해제 체크박스 + 계속 버튼으로만 온다(accepted: true 필수). 성공 응답 뒤에만 화면이 완료로 바뀐다.
    async def run(body):
        _only(body, {"accepted", "policy_version", "consent_text_sha256"})
        version, digest = body.get("policy_version"), body.get("consent_text_sha256")
        if (body.get("accepted") is not True or not isinstance(version, str) or not _POLICY_VERSION_RE.match(version)
                or not isinstance(digest, str) or not _HEX64_RE.match(digest)):
            raise CommunityAuthError("invalid_settings", "확인 항목이 필요합니다.")
        try:
            return _ok({"data": await run_in_threadpool(ops.consent, version, digest)})
        except CommunityAuthError as exc:
            return _fail_account(exc)
    return await _web_action(request, run)


@router.post("/consent-revoke")
async def web_consent_revoke(request: Request):
    return await _gate_action(request, {"confirm"}, ops.consent_revoke, {"confirm": True})


@router.post("/writer")
async def web_writer(request: Request):
    return await _gate_action(request, {"takeover"}, ops.takeover, {"takeover": True})


# 공유한 자료 전체 삭제(contributions-delete)는 아직 구현하지 않는 기능이다(2026-09-27 결정). 되살릴 때 이 주석을 해제한다.
# services/community_account_ops.contributions_delete 는 로컬 삭제 표시(업로드 차단) 규칙 테스트가 쓰므로 남겨 두지만, 이 경로가 없으면 호출되지 않는다.
# @router.post("/contributions-delete")
# async def web_contributions_delete(request: Request):
#     return await _gate_action(request, {"confirm"}, ops.contributions_delete, {"confirm": "DELETE_MY_SHARED_REPORTS"})


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
        await run_in_threadpool(ops.regate, "login")
        return _ok({"data": dto})
    return await _api_action(request, api_key, run)


@api_router.post("/cancel")
async def api_cancel(request: Request, api_key: str = Depends(_require_api_key)):
    async def run(body):
        _only(body, {"request_id"})
        dto = await run_in_threadpool(cas.get_service().cancel, _request_id(body, False))
        return _ok({"data": dto})
    return await _api_action(request, api_key, run)


# 2026-09-27: 카카오 로그인 필수 — 모바일 Client 에서 서버의 카카오 로그인을 푸는 API 는 없앤다(사용자 결정: 경로 삭제). 필요해지면 되살린다.
# @api_router.post("/disconnect")
# async def api_disconnect(request: Request, api_key: str = Depends(_require_api_key)):
#     async def run(body):
#         _only(body, set())
#         service = cas.get_service()
#         community_gate.invalidate("logout")
#         result = await run_in_threadpool(service.disconnect)
#         dto = await run_in_threadpool(service.status, can_manage=True)
#         return _ok({"data": dto, "result": result})
#     return await _api_action(request, api_key, run)


# ── 모바일: 서버 게이트 상태 (/api/v1/community/gate) ─────────────────────────────

gate_api_router = APIRouter(prefix="/api/v1/community")


@gate_api_router.get("/gate")
async def api_gate(api_key: str = Depends(_require_api_key)):
    """서버(이 PC/Docker) 커뮤니티 계정의 게이트 상태. 토큰·사용자 UUID 없음. Client 는 account.fingerprint 로 자기 계정과 비교한다."""
    return _ok({"data": await run_in_threadpool(community_gate.status_view)})
