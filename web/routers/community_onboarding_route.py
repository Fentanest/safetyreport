"""필수 설정 화면(PC·Docker): /onboarding/community(카카오 인증 + 공유 동의), /onboarding/rebuild(1회 초기화 안내·확인).

관리자 로그인 뒤 게이트 미들웨어가 이리로 보낸다. 두 화면은 게이트 allowlist 에 있고, 그 밖의 화면은 게이트를 통과해야 한다.
`next` 는 같은 서버의 상대경로만 받는다(열린 리다이렉트 방지).
"""
from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from core.utils import csrf
from core.utils.path_utils import resource_path
from services import community_gate

router = APIRouter()
templates = Jinja2Templates(directory=resource_path("web/templates"))

_NO_STORE = {"Cache-Control": "no-store"}
_BLOCKED_NEXT = ("/onboarding/", "/login", "/logout", "/setup")


def safe_next(value: str | None) -> str:
    """같은 서버 안의 상대경로만. 그 밖(절대 URL·// ·백슬래시·제어문자·온보딩 자신)은 '/'."""
    if not isinstance(value, str) or not value or len(value) > 512:
        return "/"
    if not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        return "/"
    parts = urlsplit(value)
    if parts.scheme or parts.netloc:
        return "/"
    if any(parts.path == p.rstrip("/") or parts.path.startswith(p) for p in _BLOCKED_NEXT):
        return "/"
    return value


def _rebuild_status() -> dict | None:
    try:
        from services import community_rebuild
    except ImportError:
        return None
    try:
        return {"required": bool(community_rebuild.required()), **community_rebuild.status()}
    except Exception:
        return None


@router.get("/onboarding/community")
async def onboarding_community(request: Request, next: str | None = None):
    target = safe_next(next)
    gate = await run_in_threadpool(community_gate.evaluate)
    if gate["state"] == "verification_required":
        gate = await run_in_threadpool(community_gate.refresh_now)
    return templates.TemplateResponse(request, "onboarding_community.html", {
        "csrf_token": csrf.get_or_create_token(request), "next": target, "gate": gate, "cm_mode": "onboarding",
    }, headers=_NO_STORE)


@router.get("/onboarding/rebuild")
async def onboarding_rebuild(request: Request, next: str | None = None):
    gate = await run_in_threadpool(community_gate.evaluate)
    if not gate["can_enter"]:
        return RedirectResponse("/onboarding/community?next=/onboarding/rebuild", status_code=302)
    return templates.TemplateResponse(request, "onboarding_rebuild.html", {
        "csrf_token": csrf.get_or_create_token(request), "next": safe_next(next),
        "rebuild": await run_in_threadpool(_rebuild_status),
    }, headers=_NO_STORE)
