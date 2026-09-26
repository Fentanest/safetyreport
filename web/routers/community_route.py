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
import math

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from core.utils import csrf
from services import community_auth_service as cas
from services import community_gate
from services.community_account_client import AccountApiError, CommunityAccountClient
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
        gate = await run_in_threadpool(_regate, "login")
        return _ok({"data": dto, "gate": gate})
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
        community_gate.invalidate("logout")  # 로그아웃 전에 context 를 먼저 끈다(업로드 즉시 중단)
        result = await run_in_threadpool(service.disconnect)
        dto = await run_in_threadpool(service.status, can_manage=True)
        return _ok({"data": dto, "result": result, "gate": await run_in_threadpool(community_gate.status_view)})
    return await _web_action(request, run)


@router.post("/settings")
async def web_settings(request: Request):
    async def run(body):
        known = {hash_api_key(row["key"]) for row in await run_in_threadpool(_api_key_rows)}
        cfg = await run_in_threadpool(cas.update_settings, body, known)
        service = cas.get_service()
        dto = await run_in_threadpool(service.status, can_manage=True)
        gate = await run_in_threadpool(_regate, "settings_saved")
        return _ok({"data": dto, "config": await run_in_threadpool(_config_view, cfg), "gate": gate})
    return await _web_action(request, run)


# ── 필수 게이트: 동의·철회·업로드 연결·삭제 요청 ──────────────────────────────────

_ACCOUNT_ERRORS = {
    "kakao_required": (403, "카카오 계정 연결이 먼저 필요합니다."),
    "policy_mismatch": (409, "동의 문서가 바뀌었습니다. 새로고침한 뒤 새 문서를 확인해 주세요."),
    "contributor_suspended": (403, "이 계정의 공유가 중지되어 있습니다."),
    "stale_grant": (409, "동의 상태가 바뀌었습니다. 새로고침한 뒤 다시 시도해 주세요."),
    "not_found": (404, "대상을 찾을 수 없습니다."),
    "writer_conflict": (409, "다른 기기가 이 공식 계정의 업로드를 맡고 있습니다."),
    "rate_limited": (429, "요청이 너무 잦습니다. 잠시 뒤 다시 시도해 주세요."),
    "auth_required": (401, "커뮤니티 로그인이 만료되었습니다. 다시 로그인해 주세요."),
}


def _regate(reason: str) -> dict:
    community_gate.invalidate(reason)
    community_gate.refresh_now()
    return community_gate.status_view()


def _account_call(fn):
    """커뮤니티 세션 토큰으로 community-account 를 부른다. 실패는 CommunityAuthError 로 바꾼다."""
    service = cas.get_service()
    cfg = service.config()
    token = service.get_access_token()  # CommunityAuthError(not_connected/reauth_required/...)
    try:
        return fn(CommunityAccountClient(cfg.supabase_url, cfg.publishable_key), token)
    except AccountApiError as exc:
        status, message = _ACCOUNT_ERRORS.get(exc.code, (503 if exc.transient else 502, None))
        err = CommunityAuthError("account_" + exc.code if exc.code not in _ACCOUNT_ERRORS else exc.code,
                                 message or "커뮤니티 서버에 연결하지 못했습니다. 잠시 뒤 다시 시도해 주세요.",
                                 retry_after=exc.retry_after)
        err.status = status
        err.extra = exc.extra
        raise err from None


def _fail_account(exc: CommunityAuthError) -> JSONResponse:
    resp = _fail(exc)
    extra = getattr(exc, "extra", None) or {}
    if extra.get("active_writer"):
        body = json.loads(resp.body)
        body["active_writer"] = {k: extra["active_writer"].get(k) for k in ("device_label", "platform", "source_app", "created_at")}
        resp = JSONResponse(body, status_code=resp.status_code, headers=dict(resp.headers))
    return resp


def _policy_view() -> dict:
    return {"policy_version": community_gate.REQUIRED_POLICY_VERSION,
            "consent_text_sha256": community_gate.CONSENT_TEXT_SHA256, "text": community_gate.consent_text()}


def _consent() -> dict:
    res = _account_call(lambda c, t: c.consent(t, community_gate.REQUIRED_POLICY_VERSION, community_gate.CONSENT_TEXT_SHA256))
    return {"result": {k: res.get(k) for k in ("policy_version", "granted_at", "created")}, "gate": _regate("consent_saved")}


def _consent_revoke() -> dict:
    grant_id = community_gate.current_grant_id()
    if not grant_id:
        community_gate.refresh_now()
        grant_id = community_gate.current_grant_id()
    if not grant_id:
        raise CommunityAuthError("invalid_state", "철회할 동의가 없습니다.")
    community_gate.invalidate("consent_revoked")  # 응답 전에 업로드부터 멈춘다
    res = _account_call(lambda c, t: c.consent_revoke(t, grant_id))
    return {"result": {"revoked": bool(res.get("revoked")), "already_revoked": bool(res.get("already_revoked"))},
            "gate": _regate("consent_revoked")}


def _takeover() -> dict:
    community_gate.request_takeover()
    return {"gate": community_gate.status_view()}


def _contributions_delete() -> dict:
    from services import community_capture

    # 1) 로컬 삭제 대기 표시를 먼저(community.db, 트랜잭션). 못 쓰면 중앙 삭제를 요청하지 않는다(Sol 2차 H-03a).
    try:
        local_id = community_capture.begin_deletion()
    except Exception:
        raise CommunityAuthError("invalid_state", "이 서버의 공유 저장소에 기록할 수 없어 삭제를 요청하지 않았습니다. 잠시 뒤 다시 시도해 주세요.") from None
    community_gate.invalidate("deletion_requested")
    try:
        res = _account_call(lambda c, t: c.delete_contributions(t))
    except CommunityAuthError:
        try:  # 중앙 삭제가 확실히 실패했으면 이 표시만 지운다(지우지 못하면 업로드가 막힌 채로 남는다 — fail-closed)
            community_capture.cancel_deletion(local_id)
        except Exception:
            pass
        raise
    cas.get_service().store.save_writer(None)  # 중앙이 연결을 모두 폐기했다 → 다음 확인 때 새로 등록
    local_ok = True
    try:  # 2) 표시를 한 트랜잭션에서 적용(그 시점까지의 journal 전부 차단). 실패해도 표시가 남아 업로드·reshare 를 막는다.
        community_capture.apply_pending_deletion()
    except Exception:
        local_ok = False
    return {"result": {k: res.get(k) for k in ("deletion_id", "deleted_facts", "revoked_connections", "deleted_at")},
            "local_cleanup_pending": not local_ok, "gate": _regate("deletion_completed")}


@router.get("/policy")
async def web_policy(request: Request):
    return _ok({"data": await run_in_threadpool(_policy_view)})


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
    return await _gate_action(request, {"accepted", "policy_version", "consent_text_sha256"}, _consent,
                              {"accepted": True, "policy_version": community_gate.REQUIRED_POLICY_VERSION,
                               "consent_text_sha256": community_gate.CONSENT_TEXT_SHA256})


@router.post("/consent-revoke")
async def web_consent_revoke(request: Request):
    return await _gate_action(request, {"confirm"}, _consent_revoke, {"confirm": True})


@router.post("/writer")
async def web_writer(request: Request):
    return await _gate_action(request, {"takeover"}, _takeover, {"takeover": True})


@router.post("/contributions-delete")
async def web_contributions_delete(request: Request):
    return await _gate_action(request, {"confirm"}, _contributions_delete, {"confirm": "DELETE_MY_SHARED_REPORTS"})


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
        await run_in_threadpool(_regate, "login")
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
        community_gate.invalidate("logout")
        result = await run_in_threadpool(service.disconnect)
        dto = await run_in_threadpool(service.status, can_manage=True)
        return _ok({"data": dto, "result": result})
    return await _api_action(request, api_key, run)


# ── 모바일: 서버 게이트 상태 (/api/v1/community/gate) ─────────────────────────────

gate_api_router = APIRouter(prefix="/api/v1/community")


@gate_api_router.get("/gate")
async def api_gate(api_key: str = Depends(_require_api_key)):
    """서버(이 PC/Docker) 커뮤니티 계정의 게이트 상태. 토큰·사용자 UUID 없음. Client 는 account.fingerprint 로 자기 계정과 비교한다."""
    return _ok({"data": await run_in_threadpool(community_gate.status_view)})
