from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import create_engine

import settings.settings as app_settings
from core.utils.templating import templates
from core.database import database

router = APIRouter(tags=["auth"])


def _get_engine():
    return create_engine(
        f'sqlite:///{app_settings.db_path}',
        connect_args={"check_same_thread": False}
    )


# ── 최초 설정 (관리자 계정 생성) ─────────────────────────────────────────────

@router.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request):
    engine = _get_engine()
    if database.has_admin_user(engine):
        return RedirectResponse("/login", status_code=302)
    return templates.TemplateResponse(request, "setup.html", {"error": None})


@router.post("/setup")
async def do_setup(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...)
):
    engine = _get_engine()
    if database.has_admin_user(engine):
        return RedirectResponse("/login", status_code=302)

    if not username.strip():
        return templates.TemplateResponse(request, "setup.html", {"error": "아이디를 입력해주세요."})
    if len(password) < 4:
        return templates.TemplateResponse(request, "setup.html", {"error": "비밀번호는 4자 이상이어야 합니다."})
    if password != password_confirm:
        return templates.TemplateResponse(request, "setup.html", {"error": "비밀번호가 일치하지 않습니다."})

    database.create_admin_user(engine, username.strip(), password)
    return RedirectResponse("/login", status_code=303)


# ── 로그인 ────────────────────────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if request.session.get("admin_logged_in"):
        return RedirectResponse("/", status_code=302)
    engine = _get_engine()
    if not database.has_admin_user(engine):
        return RedirectResponse("/setup", status_code=302)
    error = request.query_params.get("error")
    next_path = request.query_params.get("next", "")
    return templates.TemplateResponse(request, "login.html", {"error": error, "next": next_path})


@router.post("/login")
async def do_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form(default="")
):
    engine = _get_engine()
    user = database.get_admin_user(engine, username.strip())

    if not user:
        return RedirectResponse(f"/login?error=아이디 또는 비밀번호가 올바르지 않습니다.&next={next}", status_code=303)

    from core.utils.security import verify_password
    if not verify_password(password, user["salt"], user["password_hash"]):
        return RedirectResponse(f"/login?error=아이디 또는 비밀번호가 올바르지 않습니다.&next={next}", status_code=303)

    request.session["admin_logged_in"] = True
    request.session["admin_username"] = username.strip()
    redirect_to = next if (next and next.startswith("/") and not next.startswith("//")) else "/"
    return RedirectResponse(redirect_to, status_code=303)


# ── 로그아웃 ──────────────────────────────────────────────────────────────────

@router.get("/logout")
async def do_logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)


# ── 관리자 계정 변경 ──────────────────────────────────────────────────────────

@router.post("/change-admin")
async def change_admin(
    request: Request,
    new_username: str = Form(...),
    current_password: str = Form(...),
    new_password: str = Form(...)
):
    old_username = request.session.get("admin_username", "")
    engine = _get_engine()
    user = database.get_admin_user(engine, old_username)

    if not user:
        return RedirectResponse("/settings?admin_error=세션이 만료되었습니다. 다시 로그인해주세요.", status_code=303)

    from core.utils.security import verify_password
    if not verify_password(current_password, user["salt"], user["password_hash"]):
        return RedirectResponse("/settings?admin_error=현재 비밀번호가 올바르지 않습니다.", status_code=303)

    if not new_username.strip():
        return RedirectResponse("/settings?admin_error=새 아이디를 입력해주세요.", status_code=303)
    if len(new_password) < 4:
        return RedirectResponse("/settings?admin_error=새 비밀번호는 4자 이상이어야 합니다.", status_code=303)

    database.update_admin_user(engine, old_username, new_username.strip(), new_password)
    request.session["admin_username"] = new_username.strip()
    return RedirectResponse("/settings?admin_success=관리자 계정이 변경되었습니다.", status_code=303)
