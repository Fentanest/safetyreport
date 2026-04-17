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
from web.routers import auth_route, api_route, ws_route
import subprocess
import sys

from core.utils.path_utils import resource_path, is_frozen

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
from sqlalchemy import create_engine, text
from core.database import database
from core.utils import scheduler
engine = create_engine(f'sqlite:///{settings.db_path}', connect_args={"check_same_thread": False})

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
    database.upgrade_schema(engine)
    scheduler.init_scheduler()
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
app.include_router(file_browser_route.router)
app.include_router(devices_route.router)
app.include_router(api_route.router)
app.include_router(ws_route.router)

try:
    with open(resource_path("VERSION"), "r", encoding="utf-8") as f:
        APP_VERSION = f.read().strip()
except Exception:
    APP_VERSION = "Unknown"


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

# ── 인증 미들웨어 ──────────────────────────────────────────────────────────────

_PUBLIC_PATHS = {"/login", "/setup", "/logout", "/health"}
_PUBLIC_PREFIXES = ("/static/", "/api/v1/", "/ws/")

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
    except Exception:
        # 미들웨어 예외가 ASGI 소켓을 닫아 nginx 502로 이어지는 것을 방지
        if not request.session.get("admin_logged_in", False):
            return _login_redirect(request)
        from fastapi.responses import Response
        return Response(status_code=500)

# SessionMiddleware는 마지막에 추가해야 가장 바깥에서(먼저) 실행됨
from starlette.middleware.sessions import SessionMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send
from core.utils.security import get_or_create_session_key
_session_key = get_or_create_session_key(settings.datapath)

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
