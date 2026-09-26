import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
import uvicorn
import webbrowser
import threading
import time
import os
import signal

from web.routers import dashboard, data, settings_route, crawl, stats, rating_route, watchlist_route, file_browser_route, devices_route
from web.routers import auth_route, api_route, ws_route, db_editor_route, backup_route, maintenance_route
from web.routers import duplicate_route, media_route, community_route, community_onboarding_route
from web.routers import community_upload_route, community_rebuild_route
import subprocess
import sys

from core.utils.path_utils import resource_path, is_frozen
from services import sunwi_service

# Handle different execution modes for PyInstaller single-binary bundle
if __name__ == "__main__":
    if "--mode" in sys.argv:
        mode_idx = sys.argv.index("--mode")
        mode = sys.argv[mode_idx + 1]
        
        # Remove --mode and the value from sys.argv so they don't interfere with the target scripts
        # But for 'crawl', we want to keep other arguments
        target_argv = [sys.argv[0]] + sys.argv[mode_idx + 2:] + sys.argv[1:mode_idx]
        sys.argv = target_argv

        if mode == "bot":
            import bot
            bot.main()
            sys.exit(0)
        elif mode == "crawl":
            import start
            start.main()
            sys.exit(0)
        elif mode == "notify":
            import core.utils.notifier as notifier
            import asyncio
            asyncio.run(notifier.main())
            sys.exit(0)
        elif mode == "save_excel":
            import scripts.debug.save as save_script
            save_script.main() # I should wrap save.py main logic in main()
            sys.exit(0)

bot_process = None

from core.utils.templating import templates, template_path

static_path = resource_path("web/static")

import settings.settings as settings
from core.utils import logger
logger.LoggerFactory.create_logger()

# Initialize required directories using settings' datapath
os.makedirs(settings.datapath, exist_ok=True)
os.makedirs(os.path.join(settings.datapath, 'auth'), exist_ok=True)
os.makedirs(os.path.join(settings.datapath, 'logs'), exist_ok=True)
os.makedirs(os.path.join(settings.datapath, 'results'), exist_ok=True)

# DB Init
from sqlalchemy import text
from core.database import database
from core.database.engine import get_engine
from core.utils import scheduler
engine = get_engine()

def _checkpoint_wal():
    try:
        with engine.connect() as conn:
            conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
    except Exception as e:
        logger.LoggerFactory.logbot.error(f"WAL 체크포인트 실패: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    global bot_process
    # ── startup ──────────────────────────────────────────────────────────────
    # uvicorn 접근 로그에 타임스탬프 추가 (log_config 적용 여부와 무관하게 보장)
    try:
        import logging as _logging
        from uvicorn.logging import AccessFormatter as _AF, DefaultFormatter as _DF
        _ts = "%Y-%m-%d %H:%M:%S"
        _fmts = [
            ("uvicorn.access", _AF, '[%(asctime)s] %(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s'),
            ("uvicorn", _DF, "[%(asctime)s] %(levelprefix)s %(message)s"),
            ("uvicorn.error", _DF, "[%(asctime)s] %(levelprefix)s %(message)s"),
        ]
        for _name, _cls, _fmt in _fmts:
            _log = _logging.getLogger(_name)
            for _h in _log.handlers:
                _h.setFormatter(_cls(fmt=_fmt, datefmt=_ts, use_colors=False))
    except Exception:
        pass

    # websockets 라이브러리 내부 ping timeout 로그 노이즈 억제
    import logging as _logging
    _logging.getLogger("websockets").setLevel(_logging.ERROR)

    from services.ws_manager import ws_manager as _ws_manager
    _ws_manager.set_main_loop(asyncio.get_event_loop())
    # 업데이트 직후 첫 기동이면 스키마를 올리기 전에 data/backups/ 에 DB 사본을 남긴다.
    database.upgrade_schema(engine, backup_dir=os.path.join(settings.datapath, "backups"))
    from core.utils.runtime_mode import skip_in_fixture
    if not skip_in_fixture("startup geocode backfill"):
        try:
            from services import geocode_service
            geocode_service.ensure_map_backfill_started(engine, batch_size=120)
        except Exception as exc:
            logger.LoggerFactory.logbot.warning(f"[geocode] 서버 시작 시 자동 백필 시작 실패: {exc}")
    if not skip_in_fixture("startup photo capture backfill"):
        try:
            # 업데이트 뒤 한 번 훑기: 아직 못 읽은 주정차 사진 촬영 시각(추정 과태료용). 진행은 화면 하단 표시줄에 보인다.
            from services import maintenance_service
            maintenance_service.repair_car_numbers(engine)  # 네트워크 없음, 금방 끝남
            maintenance_service.start_photo_backfill(engine)
        except Exception as exc:
            logger.LoggerFactory.logbot.warning(f"[maintenance] 사진 촬영 시각 한 번 훑기 시작 실패: {exc}")
    if not skip_in_fixture("startup scheduler"):
        scheduler.init_scheduler()
    if not skip_in_fixture("startup crawl pending retry"):
        try:
            # 지난 실행에서 남은 대기 큐 번호가 있으면 재시도 타이머를 건다(곧바로 크롤하지는 않음, 감사 R6-03).
            from services.crawl_manager import crawl_manager
            crawl_manager.schedule_retry_if_pending()
        except Exception as exc:
            logger.LoggerFactory.logbot.warning(f"[crawl] 대기 큐 재시도 예약 실패: {type(exc).__name__}")
    sunwi_service.start_background_refresh()
    # 커뮤니티 계정: 부팅 때는 만료 전 대기 연결 요청의 poll 만 다시 시작한다(fixture 는 loopback 스택만).
    try:
        from services import community_auth_service
        community_auth_service.resume_on_startup()
    except Exception as exc:
        logger.LoggerFactory.logbot.warning(f"[community] 대기 연결 요청 재개 실패: {type(exc).__name__}")
    _start_community_services()

    try:
        from services import media_proxy_service
        removed = media_proxy_service.cleanup_cache()
        if removed:
            logger.LoggerFactory.logbot.info(f"media cache cleanup: {removed} stale file(s) removed")
    except Exception as exc:
        logger.LoggerFactory.logbot.warning(f"media cache cleanup failed: {exc}")

    # 직접 로그인 토큰 keep-alive (55분 주기). 자격증명 없으면 자동 스킵.
    try:
        from core.crawler import direct_login
        direct_login.start_keepalive(interval_seconds=55 * 60)
    except Exception as e:
        logger.LoggerFactory.logbot.warning(f"direct_login keep-alive 시작 실패: {e}")

    if settings.telegram_enabled:
        try:
            logger.LoggerFactory.logbot.info("텔레그램 봇 프로세스를 시작합니다.")
            if is_frozen:
                bot_process = subprocess.Popen([sys.executable, "--mode", "bot"])
            else:
                bot_process = subprocess.Popen([sys.executable, "bot.py"])
        except Exception as e:
            logger.LoggerFactory.logbot.error(f"봇 프로세스 시작 실패: {e}")

    yield

    # ── shutdown ─────────────────────────────────────────────────────────────
    try:
        from services import community_uploader
        community_uploader.stop_background()
    except Exception:
        pass
    try:
        from services import community_auth_service
        community_auth_service.shutdown()
    except Exception:
        pass
    try:
        from core.crawler import direct_login
        direct_login.stop_keepalive()
    except Exception:
        pass
    try:
        sunwi_service.stop_background_refresh()
    except Exception as e:
        logger.LoggerFactory.logbot.error(f"sunwi background refresh 종료 중 오류: {e}")
    if bot_process:
        logger.LoggerFactory.logbot.info("텔레그램 봇 프로세스를 종료합니다.")
        bot_process.terminate()
        try:
            bot_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            bot_process.kill()
    try:
        if scheduler.scheduler.running:
            logger.LoggerFactory.logbot.info("스케줄러를 종료합니다.")
            scheduler.scheduler.shutdown(wait=False)
    except Exception as e:
        logger.LoggerFactory.logbot.error(f"스케줄러 종료 중 오류: {e}")
    _checkpoint_wal()

app = FastAPI(title="나만의 안전신문고", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=static_path), name="static")

# Reverse proxy support: trust X-Forwarded-For / X-Forwarded-Proto from configured IPs
if settings.trusted_proxies:
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
    _trusted_list = [ip.strip() for ip in settings.trusted_proxies.split(',') if ip.strip()]
    if _trusted_list:
        app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=_trusted_list)

def _signal_handler(signum, frame):
    logger.LoggerFactory.logbot.info(f"종료 신호({signum}) 수신 - WAL 정리 후 종료합니다.")
    _checkpoint_wal()
    # sys.exit()는 asyncio 루프 내에서 CancelledError를 일으키므로
    # 기본 핸들러로 복원한 뒤 다시 시그널을 보내 uvicorn이 안전하게 종료하게 한다.
    signal.signal(signum, signal.SIG_DFL)
    os.kill(os.getpid(), signum)

# SIGINT (Ctrl+C): Windows & Linux 공통
signal.signal(signal.SIGINT, _signal_handler)
# SIGTERM: Linux/Docker 전용 (Windows에서는 지원 안 됨)
if hasattr(signal, 'SIGTERM'):
    signal.signal(signal.SIGTERM, _signal_handler)

app.include_router(auth_route.router)
app.include_router(dashboard.router)
app.include_router(data.router)
app.include_router(settings_route.router)
app.include_router(crawl.router)
app.include_router(stats.router)
app.include_router(rating_route.router)
app.include_router(watchlist_route.router)
app.include_router(duplicate_route.router)
app.include_router(media_route.router)
app.include_router(file_browser_route.router)
app.include_router(db_editor_route.router)
app.include_router(devices_route.router)
app.include_router(backup_route.router)
app.include_router(maintenance_route.router)
app.include_router(community_route.router)
app.include_router(community_route.api_router)
app.include_router(community_route.gate_api_router)
app.include_router(community_onboarding_route.router)
app.include_router(community_upload_route.router)
app.include_router(community_upload_route.api_router)
app.include_router(community_rebuild_route.router)
app.include_router(community_rebuild_route.api_router)
app.include_router(api_route.router)
app.include_router(ws_route.router)

try:
    with open(resource_path("VERSION"), "r", encoding="utf-8") as f:
        APP_VERSION = f.read().strip()
except Exception:
    APP_VERSION = "Unknown"


@app.get("/version/latest")
async def version_latest():
    from fastapi.responses import JSONResponse
    from core.utils.updater import get_latest_version_cached, _version_gt
    try:
        latest = get_latest_version_cached()
        if latest is None:
            return JSONResponse({"status": "unknown"})
        if _version_gt(latest, APP_VERSION):
            return JSONResponse({"status": "outdated", "latest": latest})
        return JSONResponse({"status": "up_to_date", "latest": latest})
    except Exception:
        return JSONResponse({"status": "unknown"})


@app.get("/health")
async def health_check():
    from fastapi.responses import JSONResponse
    return JSONResponse({"status": "ok"})

@app.middleware("http")
async def inject_version_middleware(request: Request, call_next):
    request.state.app_version = APP_VERSION
    response = await call_next(request)
    return response

# ── 세션 만료 / 미인증 응답 ────────────────────────────────────────────────────

def _is_ajax(request: Request) -> bool:
    """fetch/XHR 요청 여부 판단 (Accept 헤더 또는 X-Requested-With)."""
    accept = request.headers.get("accept", "")
    return (
        "application/json" in accept
        or request.headers.get("x-requested-with", "").lower() == "xmlhttprequest"
    )

def _login_redirect(request: Request):
    from fastapi.responses import RedirectResponse, JSONResponse
    if _is_ajax(request):
        return JSONResponse({"detail": "session_expired"}, status_code=401)
    next_path = request.url.path
    if next_path and next_path not in ("/login", "/logout", "/setup"):
        return RedirectResponse(f"/login?next={next_path}", status_code=302)
    return RedirectResponse("/login", status_code=302)

# ── 커뮤니티 필수 게이트 미들웨어 ──────────────────────────────────────────────────
# auth_middleware 보다 **먼저** 선언해야 안쪽에서(관리자 인증 뒤에) 실행된다: Session → auth → community gate → 라우트.
# 정확한 method+path allowlist 만 게이트 없이 통과한다(각자 기존 인증·CSRF·manager 권한은 그대로).

_GATE_ALLOW = frozenset({
    ("GET", "/login"), ("POST", "/login"), ("GET", "/setup"), ("POST", "/setup"), ("GET", "/logout"), ("GET", "/health"),
    ("GET", "/onboarding/community"), ("GET", "/onboarding/rebuild"),
    ("GET", "/settings/community/status"), ("GET", "/settings/community/policy"), ("GET", "/settings/community/gate"),
    *(("POST", f"/settings/community/{a}") for a in ("start", "confirm", "cancel", "disconnect", "settings", "consent",
                                                      "consent-revoke", "writer", "contributions-delete")),
    ("GET", "/settings/community/rebuild"),
    *(("POST", f"/settings/community/rebuild/{a}") for a in ("start", "resume", "pause")),
    ("GET", "/api/v1/app/config"), ("GET", "/api/v1/community-auth/status"),
    *(("POST", f"/api/v1/community-auth/{a}") for a in ("start", "confirm", "cancel", "disconnect")),
    ("GET", "/api/v1/community/gate"), ("GET", "/api/v1/community/rebuild"),
    *(("POST", f"/api/v1/community/rebuild/{a}") for a in ("start", "resume")),
})


def _gate_exempt(method: str, path: str) -> bool:
    if path.startswith("/static/"):
        return True
    if method == "HEAD":
        method = "GET"
    return (method, path) in _GATE_ALLOW


def _request_api_key_valid(request: Request) -> bool:
    key = request.headers.get("x-api-key") or request.query_params.get("api_key") or ""
    return bool(key) and bool(database.validate_api_key(engine, key))


def _community_gate_state() -> dict:
    from services import community_gate
    return community_gate.check_for_request()


def _gate_blocked_response(request: Request, gate: dict):
    from fastapi.responses import RedirectResponse, JSONResponse
    path = request.url.path
    if (request.method == "GET" and not _is_ajax(request)
            and not path.startswith(("/api/", "/media/", "/community/", "/settings/community/"))):
        from urllib.parse import quote
        target = path + (f"?{request.url.query}" if request.url.query else "")
        return RedirectResponse(f"/onboarding/community?next={quote(target, safe='/')}", status_code=302)
    return JSONResponse({"detail": "COMMUNITY_ONBOARDING_REQUIRED", "code": "COMMUNITY_ONBOARDING_REQUIRED",
                         "gate": {"state": gate.get("state"), "reasons": gate.get("reasons") or []}},
                        status_code=403, headers={"Cache-Control": "no-store"})


@app.middleware("http")
async def community_gate_middleware(request: Request, call_next):
    from starlette.concurrency import run_in_threadpool
    path = request.url.path
    if _gate_exempt(request.method, path):
        return await call_next(request)
    if path.startswith("/api/v1/"):
        # 키가 없거나 틀리면 라우트가 401 을 준다(게이트 상태를 인증 전에 드러내지 않음).
        if not await run_in_threadpool(_request_api_key_valid, request):
            return await call_next(request)
    elif path.startswith("/media/"):
        # 예전에는 인증 없이 열려 있었다 → 관리자 세션 또는 API 키 + 게이트.
        if not (request.session.get("admin_logged_in") or await run_in_threadpool(_request_api_key_valid, request)):
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
    gate = await run_in_threadpool(_community_gate_state)
    if gate.get("can_enter"):
        return await call_next(request)
    return _gate_blocked_response(request, gate)


def _on_community_gate_change(result: dict) -> None:
    """게이트를 잃으면(확인 필요 포함 — Sol M-01) 이벤트 WS 를 4403 으로 닫는다(로그 WS 는 자체 GateWatch)."""
    if not result.get("can_enter"):
        from services.ws_manager import ws_manager
        ws_manager.close_all_from_thread(4403, "COMMUNITY_ONBOARDING_REQUIRED")


def _start_community_services() -> None:
    """interfaces.md 순서: CommunityStore.open → 게이트 확인(비동기) → 업로더 → 스케줄 job → 자정 따라잡기 → 초기화 재개."""
    log = logger.LoggerFactory.logbot
    try:
        from services.community_store import CommunityStore
        from services import community_gate
        CommunityStore.open()
        community_gate.on_change(_on_community_gate_change)
        threading.Thread(target=community_gate.refresh_now, name="community-gate-startup", daemon=True).start()
    except Exception as exc:
        log.warning(f"[community] 커뮤니티 저장소·게이트 시작 실패: {type(exc).__name__}: {exc}")
        return
    import importlib

    def _call(module: str, attr: str, *args):
        getattr(importlib.import_module(f"services.{module}"), attr)(*args)

    steps = [("uploader", lambda: _call("community_uploader", "start_background"))]
    if scheduler.scheduler.running:
        steps.append(("jobs", lambda: _call("community_schedule", "register_community_jobs", scheduler.scheduler)))
        steps.append(("midnight catch-up", lambda: _call("community_schedule", "catch_up_on_start")))
    steps.append(("rebuild resume", lambda: _call("community_rebuild", "resume_on_startup")))
    for name, fn in steps:
        try:
            fn()
        except Exception as exc:  # 한 단계 실패가 서버 시작을 막지 않는다(각 단계는 자체적으로 fail-closed)
            log.warning(f"[community] 시작 단계 실패({name}): {type(exc).__name__}: {exc}")


# ── 인증 미들웨어 ──────────────────────────────────────────────────────────────

_PUBLIC_PATHS = {"/login", "/setup", "/logout", "/health"}
_PUBLIC_PREFIXES = ("/static/", "/api/v1/", "/ws/", "/media/")

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    # WebSocket 및 공개 경로는 인증 없이 통과
    if (path in _PUBLIC_PATHS
            or any(path.startswith(p) for p in _PUBLIC_PREFIXES)
            or request.headers.get("upgrade", "").lower() == "websocket"):
        try:
            return await call_next(request)
        except Exception:
            from fastapi.responses import Response
            return Response(status_code=500)

    try:
        if not request.session.get("admin_logged_in"):
            return _login_redirect(request)
        return await call_next(request)
    except Exception as e:
        # 미들웨어 예외가 ASGI 소켓을 닫아 nginx 502로 이어지는 것을 방지
        import traceback
        logger.LoggerFactory.logbot.error(f"[middleware] {request.url.path} 처리 중 예외: {e}\n{traceback.format_exc()}")
        if not request.session.get("admin_logged_in", False):
            return _login_redirect(request)
        from fastapi.responses import Response
        return Response(status_code=500)

# SessionMiddleware는 마지막에 추가해야 가장 바깥에서(먼저) 실행됨
from starlette.middleware.sessions import SessionMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send
from core.utils.security import get_or_create_session_key
_session_key = get_or_create_session_key(settings.datapath)
from core.utils import ws_auth as _ws_auth
_ws_auth.configure(_session_key)  # 로그 WS 가 관리자 세션 쿠키를 같은 키로 읽는다

class _WebSocketSafeSessionMiddleware:
    """WebSocket 연결에서 SessionMiddleware가 세션 쿠키를 덮어쓰는 것을 방지합니다.
    WS 비정상 종료(1011 등) 시 빈 Set-Cookie가 발급되어 기존 세션이 소멸하는 버그 수정."""
    def __init__(self, app: ASGIApp, **kwargs):
        self._session_mw = SessionMiddleware(app, **kwargs)
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] == "websocket":
            await self._app(scope, receive, send)
        else:
            await self._session_mw(scope, receive, send)

app.add_middleware(
    _WebSocketSafeSessionMiddleware,
    secret_key=_session_key,
    session_cookie="safetyreport_session",
    max_age=settings.session_max_age,
)


_UVICORN_LOG_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "access": {
            "()": "uvicorn.logging.AccessFormatter",
            "fmt": '[%(asctime)s] %(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s',
            "datefmt": "%Y-%m-%d %H:%M:%S",
            "use_colors": False,
        },
        "default": {
            "()": "uvicorn.logging.DefaultFormatter",
            "fmt": "[%(asctime)s] %(levelprefix)s %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
            "use_colors": False,
        },
    },
    "handlers": {
        "access": {"class": "logging.StreamHandler", "formatter": "access", "stream": "ext://sys.stdout"},
        "default": {"class": "logging.StreamHandler", "formatter": "default", "stream": "ext://sys.stderr"},
    },
    "loggers": {
        "uvicorn": {"handlers": ["default"], "level": "INFO", "propagate": False},
        "uvicorn.error": {"handlers": ["default"], "level": "INFO", "propagate": False},
        "uvicorn.access": {"handlers": ["access"], "level": "INFO", "propagate": False},
    },
}

def start_server():
    # Use app object for frozen binary (no reload), but use "main:app" string for dev mode (with reload)
    try:
        if is_frozen:
            uvicorn.run(app, host="0.0.0.0", port=6819, log_config=_UVICORN_LOG_CONFIG,
                        ws_ping_interval=None, ws_ping_timeout=None)
        else:
            uvicorn.run("main:app", host="0.0.0.0", port=6819, reload=True, log_config=_UVICORN_LOG_CONFIG,
                        ws_ping_interval=None, ws_ping_timeout=None)
    except Exception as e:
        logger.LoggerFactory.get_logger().error(f"서버 시작 오류: {e}")
        if not is_frozen:
            raise e

if __name__ == "__main__":
    def open_browser():
        time.sleep(2)
        webbrowser.open("http://127.0.0.1:6819")

    try:
        from core.utils.updater import check_and_prompt_update
        check_and_prompt_update()
    except Exception as _ue:
        print(f"업데이트 확인 중 오류: {_ue}")

    try:
        if not os.path.exists('/.dockerenv'):
            threading.Thread(target=open_browser, daemon=True).start()
        else:
            print("\n\n" + "="*60)
            print("🐳 도커 환경에서 실행 중입니다.")
            print("호스트 장비의 브라우저에서 'http://[서버-IP]:6819'에 접속하세요.")
            print("="*60 + "\n\n")

        start_server()
    except Exception as e:
        # If logger is not initialized yet, try to initialize it or print to console
        try:
            logger.LoggerFactory.get_logger().critical(f"애플리케이션 실행 중 치명적 오류 발생: {e}", exc_info=True)
        except Exception:
            print(f"CRITICAL ERROR: {e}")
            with open("crash_report.log", "a", encoding="utf-8") as f:
                import datetime
                f.write(f"[{datetime.datetime.now()}] CRITICAL ERROR: {e}\n")
        sys.exit(1)
