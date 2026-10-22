"""관리자 웹 상단 안내(2-8)·앱 공지(2-11) 연결. 실제 목록·저장은 E 의 services/user_action_notice.py·services/app_announcements.py.

- 관리자 계정 키(admin_key): `request.session['admin_username']` 그대로(E 와 합의). 아이디를 바꾸면 새 키가 된다('보지 않기'는 처음부터).
- 공지는 PC 서버가 Edge 에서 받아(app_announcements) 이 화면에 넘긴다. 브라우저는 Supabase 를 직접 부르지 않는다.
- 경로(관리자 세션 필수. POST 는 CSRF 헤더 — session-requests.js 가 붙인다. /api/v1 아래가 아니다):
  `GET /api/announcements/pending` → `{"data": [...]}`(스레드풀, 캐시가 오래되면 여기서 한 번 받는다)
  `POST /api/announcements/{id}/snooze` → 공지별 '보지 않기'(관리자 계정별, PC 데이터 폴더)
  `POST /api/announcements/{id}/dismiss` → '닫기'(이번 로그인 동안만. E 의 메모리 + 이 로그인 세션에도 적는다)
- 로그인 직후 `on_login(admin_key)`(auth_route). 기본 템플릿(web/templates/base.html)은 `sr_notices(request)`·`sr_announcements(request)` 로
  partials/notice_banner.html(`notices`)·partials/announcement_popup.html(`announcements`)을 그린다. 화면 그리기에서는 네트워크를 기다리지 않는다:
  공지 캐시가 비었거나 오래됐으면 이번 화면은 빈 목록으로 그리고 백그라운드로 받는다(다음 화면에 뜬다). 실패하면 빈 목록(화면은 그대로).
"""
from __future__ import annotations

import re
import threading
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from core.utils.fallback import note_fallback

router = APIRouter(prefix="/api/announcements")

ANNOUNCEMENT_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")
PATH = re.compile(r"^/api/announcements/[A-Za-z0-9_%.-]{1,240}/(snooze|dismiss)$")  # 게이트 예외(id 검사는 라우트가 한다)
_CLOSED_KEY = "announcements_closed"
_refreshing = threading.Lock()


def admin_key(request: Request) -> str | None:
    try:
        if not request.session.get("admin_logged_in"):
            return None
        name = request.session.get("admin_username")
    except (AssertionError, AttributeError):  # 세션 미들웨어 없는 요청
        return None
    return name if isinstance(name, str) and name else None


def on_login(username: str | None) -> None:
    if not username:
        return
    try:
        from services import app_announcements
        app_announcements.on_login(username)
    except Exception as exc:
        note_fallback("announcements.on_login", exc)


def _cache_fresh(module) -> bool:
    """E 구현의 캐시가 6시간 안이면 True(네트워크 없이 바로 읽을 수 있다). 캐시 정보를 모르면 True(그대로 부른다)."""
    cache = getattr(module, "_cache", None)
    limit = getattr(module, "CACHE_SECONDS", None)
    if not isinstance(cache, dict) or not isinstance(limit, (int, float)):
        return True
    now = getattr(module, "_now", time.time)()
    age = now - float(cache.get("fetched_at") or 0)
    return 0 <= age < limit


def _refresh_in_background(module) -> None:
    if not _refreshing.acquire(blocking=False):
        return

    def run() -> None:
        try:
            module.refresh()
        except Exception as exc:
            note_fallback("announcements.refresh", exc)
        finally:
            _refreshing.release()

    threading.Thread(target=run, name="app-announcements-refresh", daemon=True).start()


def _closed(request: Request) -> set:
    try:
        return {x for x in (request.session.get(_CLOSED_KEY) or []) if isinstance(x, str)}
    except (AssertionError, AttributeError):
        return set()


def sr_announcements(request: Request) -> list[dict]:
    key = admin_key(request)
    if not key:
        return []
    try:
        from services import app_announcements
        if not _cache_fresh(app_announcements):
            _refresh_in_background(app_announcements)
            return []
        items = app_announcements.pending_for_admin(key) or []
        closed = _closed(request)
        return [item for item in items if isinstance(item, dict) and item.get("id") not in closed]
    except Exception as exc:
        note_fallback("announcements.pending", exc)
        return []


def sr_notices(request: Request) -> list[dict]:
    if not admin_key(request):
        return []
    try:
        from services import community_progress_trigger, user_action_notice
        inputs = community_progress_trigger.notice_inputs()
        inputs["update_required"] = False  # 잠금 중에는 관리자 웹 전체가 잠금 화면(update_required.html)이다
        return list(user_action_notice.evaluate(**inputs) or [])
    except Exception as exc:
        note_fallback("announcements.notices", exc)
        return []


def register_template_globals(env) -> None:
    env.globals["sr_notices"] = sr_notices
    env.globals["sr_announcements"] = sr_announcements


def _bad(code: str, status: int) -> JSONResponse:
    return JSONResponse({"status": "error", "code": code}, status_code=status, headers={"Cache-Control": "no-store"})


@router.get("/pending")
def pending(request: Request):
    key = admin_key(request)
    if not key:
        return _bad("unauthorized", 401)
    try:
        from services import app_announcements
        items = app_announcements.pending_for_admin(key) or []
    except Exception as exc:
        note_fallback("announcements.pending_api", exc)
        items = []
    closed = _closed(request)
    return JSONResponse({"data": [i for i in items if isinstance(i, dict) and i.get("id") not in closed]},
                        headers={"Cache-Control": "no-store"})


@router.post("/{announcement_id}/snooze")
def snooze(request: Request, announcement_id: str):
    key = admin_key(request)
    if not key:
        return _bad("unauthorized", 401)
    if not ANNOUNCEMENT_ID.fullmatch(announcement_id):
        return _bad("invalid_announcement", 400)
    try:
        from services import app_announcements
        app_announcements.snooze(key, announcement_id)
    except Exception as exc:
        note_fallback("announcements.snooze", exc)
        return _bad("snooze_failed", 500)
    return {"status": "ok"}


@router.post("/{announcement_id}/dismiss")
def dismiss(request: Request, announcement_id: str):
    key = admin_key(request)
    if not key:
        return _bad("unauthorized", 401)
    if not ANNOUNCEMENT_ID.fullmatch(announcement_id):
        return _bad("invalid_announcement", 400)
    closed = sorted(_closed(request))
    if announcement_id not in closed:
        request.session[_CLOSED_KEY] = (closed + [announcement_id])[-50:]
    try:
        from services import app_announcements
        app_announcements.dismiss(key, announcement_id)
    except Exception as exc:
        note_fallback("announcements.dismiss", exc)
    return {"status": "ok"}
