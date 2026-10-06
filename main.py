import sys

# PyInstaller 단일 실행파일의 하위 모드(--mode crawl/bot/notify/save_excel)는 웹 서버 모듈을 불러오기 전에 나눈다
# (EO R-04: 크롤·알림 하위 프로세스가 라우터·서비스 전체를 불러오지 않게).
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
            sys.exit(start.main())
        elif mode == "notify":
            import core.utils.notifier as notifier
            import asyncio
            asyncio.run(notifier.main())
            sys.exit(0)
        elif mode == "save_excel":
            import scripts.debug.save as save_script
            save_script.main() # I should wrap save.py main logic in main()
            sys.exit(0)

import asyncio
from contextlib import asynccontextmanager
from starlette.concurrency import run_in_threadpool
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from core.utils.fallback import log_request_exception, note_fallback
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

from core.utils.path_utils import resource_path, is_frozen
from services import sunwi_service

bot_application = None

from core.utils.templating import templates, template_path

static_path = resource_path("web/static")

import settings.settings as settings
from core.utils import logger


def _init_runtime() -> None:
    """서버 프로세스 준비(EO R-04: import 가 아니라 앱을 만들 때 한 번): 코어 로거와 데이터 하위 폴더."""
    if logger.LoggerFactory.logbot is None:
        logger.LoggerFactory.create_logger()
    for sub in ("", "auth", "logs", "results"):
        os.makedirs(os.path.join(settings.datapath, sub), exist_ok=True)

# DB Init
from sqlalchemy import text
from core.database import database
from core.database.engine import get_engine
from core.utils import scheduler


def _checkpoint_wal():
    try:
        with get_engine().connect() as conn:
            conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
    except Exception as e:
        logger.LoggerFactory.logbot.error(f"WAL 체크포인트 실패: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    global bot_application
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
    from services.crawl_manager import crawl_manager as _managed_crawl
    _managed_crawl.resume_managed()
    from services import download_artifacts
    await run_in_threadpool(download_artifacts.recover)
    # 이전 버전 DB 는 옮기지 않는다(2026-09-26 초기화 크롤링 릴리스): data/backups/legacy_v*.db 로 통째로 백업한 뒤
    # 신고 자료를 비우고(관리자·API 키·감시목록·지오코딩 캐시만 남김) 초기화 크롤링 안내로 다시 채운다. 새 설치는 해당 없음.
    def _rotate_community_dataset():
        # 개인 DB 를 비우기 직전 community.db 데이터셋 선회전(복원과 같은 보수적 순서, S-20).
        from services.community_store import CommunityStore
        CommunityStore.open().rotate_dataset("legacy_reset")

    engine = get_engine()
    database.reset_legacy_database(engine, os.path.join(settings.datapath, "backups"), before_reset=_rotate_community_dataset)
    database.upgrade_schema(engine, backup_dir=os.path.join(settings.datapath, "backups"))
    database.repair_stale_merge(engine)
    from core.utils.runtime_mode import skip_in_fixture
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
        media_proxy_service.start()
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
            import bot
            bot_application = await bot.start_managed()
        except Exception as exc:
            logger.LoggerFactory.logbot.error(f"봇 시작 실패: {type(exc).__name__}")

    try:
        yield
    finally:
        # ── shutdown ─────────────────────────────────────────────────────────────
        shutdown_deadline = time.monotonic() + 60
        def allowance(cap=5):
            return min(cap, max(0, shutdown_deadline - time.monotonic()))
        async def stop_worker(name, callback):
            try:
                if not await run_in_threadpool(callback, timeout=allowance()):
                    logger.LoggerFactory.logbot.warning(f'[{name}] 종료 요청 후 작업자가 아직 실행 중입니다.')
            except Exception as exc:
                logger.LoggerFactory.logbot.warning(f'[{name}] 종료 실패: {type(exc).__name__}')
        try:
            await _ws_manager.close_all(1001, 'Server shutdown')
        except Exception as exc:
            logger.LoggerFactory.logbot.warning(f'[WS] 종료 실패: {type(exc).__name__}')
        try:
            if not await run_in_threadpool(_managed_crawl.shutdown, timeout=allowance(15)):
                logger.LoggerFactory.logbot.warning('[crawl] 종료 제한 시간 후 child 또는 준비 작업이 아직 실행 중입니다.')
        except Exception as exc:
            logger.LoggerFactory.logbot.warning(f'[crawl] 종료 실패: {type(exc).__name__}')
        try:
            from services import community_rebuild
            stopped = await run_in_threadpool(community_rebuild.stop_background, timeout=allowance())
            if not stopped:
                logger.LoggerFactory.logbot.warning('[rebuild] 종료 제한 시간 후 작업자가 아직 실행 중입니다.')
        except Exception as exc:
            logger.LoggerFactory.logbot.warning(f'[rebuild] 종료 실패: {type(exc).__name__}')
        try:
            from services import community_uploader
            await stop_worker('uploader', community_uploader.stop_background)
        except Exception:
            pass
        try:
            from services import community_auth_service
            await stop_worker('auth', community_auth_service.shutdown)
        except Exception:
            pass
        try:
            from core.crawler import direct_login
            await stop_worker('direct_login', direct_login.stop_keepalive)
        except Exception:
            pass
        try:
            await stop_worker('sunwi', sunwi_service.stop_background_refresh)
        except Exception as e:
            logger.LoggerFactory.logbot.error(f"sunwi background refresh 종료 중 오류: {e}")
        if bot_application:
            try:
                import bot
                await bot.stop_managed(bot_application, timeout=allowance(10))
            except Exception as exc:
                logger.LoggerFactory.logbot.warning(f'[bot] 종료 실패: {type(exc).__name__}')
            finally:
                bot_application = None
        try:
            if scheduler.scheduler.running:
                logger.LoggerFactory.logbot.info("스케줄러를 종료합니다.")
                scheduler.scheduler.shutdown(wait=False)
        except Exception as e:
            logger.LoggerFactory.logbot.error(f"스케줄러 종료 중 오류: {e}")
        try:
            from services import rating_service
            if not await run_in_threadpool(rating_service.stop, timeout=allowance()):
                logger.LoggerFactory.logbot.warning('[rating] 종료 제한 시간 후 작업자가 아직 실행 중입니다.')
        except Exception as exc:
            logger.LoggerFactory.logbot.warning(f'[rating] 종료 실패: {type(exc).__name__}')
        try:
            from services import media_proxy_service
            if not await run_in_threadpool(media_proxy_service.stop, timeout=allowance()):
                logger.LoggerFactory.logbot.warning('[media] 종료 제한 시간 후 다운로드 작업자가 아직 실행 중입니다.')
        except Exception as exc:
            logger.LoggerFactory.logbot.warning(f'[media] 종료 실패: {type(exc).__name__}')
        _checkpoint_wal()

try:
    with open(resource_path("VERSION"), "r", encoding="utf-8") as f:
        APP_VERSION = f.read().strip()
except Exception:
    APP_VERSION = "Unknown"


async def version_latest():
    from fastapi.responses import JSONResponse
    from core.utils.updater import get_latest_version_cached, _version_gt
    try:
        from starlette.concurrency import run_in_threadpool
        latest = await run_in_threadpool(get_latest_version_cached)
        if latest is None:
            return JSONResponse({"status": "unknown"})
        if _version_gt(latest, APP_VERSION):
            return JSONResponse({"status": "outdated", "latest": latest})
        return JSONResponse({"status": "up_to_date", "latest": latest})
    except Exception as exc:
        note_fallback("version_latest", exc)
        return JSONResponse({"status": "unknown"})


async def health_check():
    from fastapi.responses import JSONResponse
    return JSONResponse({"status": "ok"})

async def inject_version_middleware(request: Request, call_next):
    from fastapi.responses import JSONResponse
    request.state.app_version = APP_VERSION
    if not request.url.path.startswith(('/api/v1/', '/static/')) and request.url.path != '/health':
        from core.utils import csrf
        csrf.get_or_create_token(request)
    if request.session.get('admin_logged_in') and not request.url.path.startswith('/api/v1/') and request.url.path not in _PUBLIC_PATHS:
        if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            from core.utils import csrf
            supplied = request.headers.get(csrf.HEADER)
            if not supplied and request.headers.get('content-type', '').split(';')[0] == 'application/x-www-form-urlencoded':
                from urllib.parse import parse_qs
                raw = bytearray()
                async for chunk in request.stream():
                    if len(raw) + len(chunk) > 64 * 1024:
                        return JSONResponse({'detail': 'Form body is too large'}, status_code=413)
                    raw.extend(chunk)
                request._body = bytes(raw)
                supplied = parse_qs(raw.decode('utf-8', errors='replace')).get('_csrf_token', [None])[0]
            reason = csrf.verify_token(request, supplied)
            if reason:
                from fastapi.responses import JSONResponse
                return JSONResponse({'detail': 'csrf_failed', 'reason': reason}, status_code=403)
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
    ("GET", "/onboarding/cloud"), ("POST", "/settings/official-account/retry"),
    ("GET", "/onboarding/community"), ("GET", "/onboarding/rebuild"),
    ("GET", "/settings/community/status"), ("GET", "/settings/community/policy"), ("GET", "/settings/community/gate"),
    # disconnect 는 2026-09-27 에 없앴다(카카오 로그인 필수) — 대신 logout(신고 자료 삭제)·reset-session(세션 파일 손상 때만)·
    # db-owner/adopt(다른 계정의 자료를 지우고 시작)
    *(("POST", f"/settings/community/{a}") for a in ("start", "confirm", "cancel", "logout", "reset-session",
                                                      "db-owner/adopt", "settings", "consent",
                                                      "consent-revoke", "writer")),  # "contributions-delete": 미구현 기능이라 경로를 주석 처리(community_route.py)
    ("GET", "/settings/community/rebuild"),
    *(("POST", f"/settings/community/rebuild/{a}") for a in ("start", "resume", "pause")),
    ("GET", "/api/v1/app/config"), ("GET", "/api/v1/community-auth/status"),
    ("GET", "/api/v1/server/version"),
    *(("POST", f"/api/v1/community-auth/{a}") for a in ("start", "confirm", "cancel")),
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
    if hasattr(request.state, 'api_key_valid'):
        return request.state.api_key_valid
    key = request.headers.get("x-api-key") or request.query_params.get("api_key") or ""
    request.state.api_key_valid = bool(key) and bool(database.validate_api_key(get_engine(), key))
    return request.state.api_key_valid


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
        from services.official_account import BLOCKED
        if gate.get("state") in BLOCKED:
            return RedirectResponse("/settings/", status_code=302)
        if gate.get("state") in ("cloud_unavailable", "official_account_protocol_required"):
            return RedirectResponse("/onboarding/cloud", status_code=302)
        return RedirectResponse(f"/onboarding/community?next={quote(target, safe='/')}", status_code=302)
    return JSONResponse({"detail": "COMMUNITY_ONBOARDING_REQUIRED", "code": "COMMUNITY_ONBOARDING_REQUIRED",
                         "gate": {"state": gate.get("state"), "reasons": gate.get("reasons") or []}},
                        status_code=403, headers={"Cache-Control": "no-store"})


async def community_gate_middleware(request: Request, call_next):
    from starlette.concurrency import run_in_threadpool
    path = request.url.path
    # Every external API is guarded, including onboarding and query-key downloads.
    # API paths never inherit an administrator-cookie exception.
    if request.method != "OPTIONS" and (path.startswith("/api/v1/") or
            (path.startswith("/media/") and not request.session.get("admin_logged_in"))):
        valid = await run_in_threadpool(_request_api_key_valid, request)
        if not valid:
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": "유효하지 않은 API 키입니다."}, status_code=401)
        if not (request.method in ("GET", "HEAD") and path == "/api/v1/server/version"):
            from services.selfhost_compat import http_rejection
            problem = http_rejection(request)
            if problem:
                from fastapi.responses import JSONResponse
                return JSONResponse(problem, status_code=409, headers={"Cache-Control": "no-store"})
    if request.method == "OPTIONS":
        return await call_next(request)
    if (request.method, path) in {("GET", "/api/v1/server/version"), ("GET", "/onboarding/cloud"), ("POST", "/settings/official-account/retry"),
                                   ("GET", "/settings/community/gate")}:
        return await call_next(request)
    # Binding barriers also cover other onboarding pages; only recovery controls remain usable.
    recovery = {("GET", "/settings"), ("GET", "/settings/"), ("POST", "/settings/save"),
                ("GET", "/onboarding/cloud"), ("POST", "/settings/official-account/retry"),
                ("GET", "/settings/community/gate"), ("GET", "/settings/community/status")}
    public = path.startswith("/static/") or path in ("/login", "/logout", "/setup", "/health")
    binding_gate = None
    if not public:
        from services.official_account import BLOCKED
        binding_gate = await run_in_threadpool(_community_gate_state)
        if binding_gate.get("state") in BLOCKED | {"cloud_unavailable", "official_account_protocol_required"}:
            if (request.method, path) in recovery:
                # Cloud failures expose only retry UI/status, never an unverified settings mutation.
                if binding_gate.get("state") in ("cloud_unavailable", "official_account_protocol_required") and path in ("/settings", "/settings/", "/settings/save"):
                    return _gate_blocked_response(request, binding_gate)
                return await call_next(request)
            return _gate_blocked_response(request, binding_gate)
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
    gate_started = time.perf_counter()
    gate = binding_gate if binding_gate is not None else await run_in_threadpool(_community_gate_state)
    request.state.gate_ms = (time.perf_counter() - gate_started) * 1000
    if gate.get("can_enter"):
        return await call_next(request)
    return _gate_blocked_response(request, gate)


async def request_timings(request: Request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    response.headers['Server-Timing'] = 'app;dur=%.2f, gate;dur=%.2f' % (
        (time.perf_counter() - started) * 1000, getattr(request.state, 'gate_ms', 0.0))
    return response


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

    def _call(module: str, attr: str, *args, **kwargs):
        getattr(importlib.import_module(f"services.{module}"), attr)(*args, **kwargs)

    steps = [("uploader", lambda: _call("community_uploader", "start_background"))]
    if scheduler.scheduler.running:
        steps.append(("jobs", lambda: _call("community_schedule", "register_community_jobs", scheduler.scheduler)))
        steps.append(("midnight catch-up", lambda: _call("community_schedule", "catch_up_on_start")))
    steps.append(("rebuild supervisor", lambda: _call("community_rebuild", "start_background")))
    steps.append(("rebuild resume", lambda: _call("community_rebuild", "resume_on_startup", background=True)))
    for name, fn in steps:
        try:
            fn()
        except Exception as exc:  # 한 단계 실패가 서버 시작을 막지 않는다(각 단계는 자체적으로 fail-closed)
            log.warning(f"[community] 시작 단계 실패({name}): {type(exc).__name__}: {exc}")


# ── 인증 미들웨어 ──────────────────────────────────────────────────────────────

_PUBLIC_PATHS = {"/login", "/setup", "/logout", "/health"}
_PUBLIC_PREFIXES = ("/static/", "/api/v1/", "/ws/", "/media/")

async def auth_middleware(request: Request, call_next):
    path = request.url.path
    # 실제 WebSocket은 HTTP 미들웨어를 거치지 않는다. Upgrade 헤더는
    # 일반 HTTP 클라이언트도 임의로 보낼 수 있으므로 세션 예외가 아니다.
    if (path in _PUBLIC_PATHS
            or any(path.startswith(p) for p in _PUBLIC_PREFIXES)):
        try:
            return await call_next(request)
        except Exception as exc:
            log_request_exception(path, exc)
            from fastapi.responses import Response
            return Response(status_code=500)

    try:
        if not request.session.get("admin_logged_in"):
            return _login_redirect(request)
        return await call_next(request)
    except Exception as e:
        # 미들웨어 예외가 ASGI 소켓을 닫아 nginx 502로 이어지는 것을 방지
        log_request_exception(path, e)
        if not request.session.get("admin_logged_in", False):
            return _login_redirect(request)
        from fastapi.responses import Response
        return Response(status_code=500)

# SessionMiddleware는 마지막에 추가해야 가장 바깥에서(먼저) 실행됨(create_app 참고)
from starlette.middleware.sessions import SessionMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send
from core.utils.security import get_or_create_session_key
from core.utils import ws_auth as _ws_auth


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


def _signal_handler(signum, frame):
    logger.LoggerFactory.logbot.info(f"종료 신호({signum}) 수신 - WAL 정리 후 종료합니다.")
    _checkpoint_wal()
    # sys.exit()는 asyncio 루프 내에서 CancelledError를 일으키므로
    # 기본 핸들러로 복원한 뒤 다시 시그널을 보내 uvicorn이 안전하게 종료하게 한다.
    signal.signal(signum, signal.SIG_DFL)
    os.kill(os.getpid(), signum)


def _install_signal_handlers() -> None:
    if threading.current_thread() is not threading.main_thread():
        return  # signal.signal 은 주 스레드에서만 걸 수 있다(예전에도 import 하는 주 스레드에서 걸었다)
    # SIGINT (Ctrl+C): Windows & Linux 공통
    signal.signal(signal.SIGINT, _signal_handler)
    # SIGTERM: Linux/Docker 전용 (Windows에서는 지원 안 됨)
    if hasattr(signal, 'SIGTERM'):
        signal.signal(signal.SIGTERM, _signal_handler)


_ROUTERS = (
    auth_route.router, dashboard.router, data.router, settings_route.router, crawl.router, stats.router,
    rating_route.router, watchlist_route.router, duplicate_route.router, media_route.router, file_browser_route.router,
    db_editor_route.router, devices_route.router, backup_route.router, maintenance_route.router,
    community_route.router, community_route.api_router, community_route.gate_api_router,
    community_onboarding_route.router, community_upload_route.router, community_upload_route.api_router,
    community_rebuild_route.router, community_rebuild_route.api_router, api_route.router, ws_route.router,
)


def create_app() -> FastAPI:
    """웹 앱 조립(EO R-04). 프로세스 준비(로거·폴더) → 앱·정적 파일 → 프록시 → HTTP 미들웨어(아래 순서) → 세션 → 라우터.

    미들웨어는 나중에 넣은 것이 바깥이다: 세션 → 관리자 인증 → 시간 측정 → 커뮤니티 게이트 → 버전·CSRF → (프록시) → 라우트.
    설정·DB 경로는 프로세스 전역(settings)이라 한 프로세스에 데이터 루트가 다른 앱을 둘 만들 수는 없다.
    """
    _init_runtime()
    app = FastAPI(title="나만의 안전신문고", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=static_path), name="static")

    # Reverse proxy support: trust X-Forwarded-For / X-Forwarded-Proto from configured IPs
    if settings.trusted_proxies:
        from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
        trusted = [ip.strip() for ip in settings.trusted_proxies.split(',') if ip.strip()]
        if trusted:
            app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=trusted)

    _install_signal_handlers()
    for router in _ROUTERS:
        app.include_router(router)
    app.add_api_route("/version/latest", version_latest, methods=["GET"])
    app.add_api_route("/health", health_check, methods=["GET"])

    for middleware in (inject_version_middleware, community_gate_middleware, request_timings, auth_middleware):
        app.middleware("http")(middleware)
    session_key = get_or_create_session_key(settings.datapath)
    _ws_auth.configure(session_key)  # 로그 WS 가 관리자 세션 쿠키를 같은 키로 읽는다
    app.add_middleware(
        _WebSocketSafeSessionMiddleware,
        secret_key=session_key,
        session_cookie="safetyreport_session",
        max_age=settings.session_max_age,
    )
    app.state.engine = get_engine()
    return app


_app: FastAPI | None = None
_app_lock = threading.Lock()


def get_app() -> FastAPI:
    """이 프로세스의 웹 앱(처음 부를 때 만든다). `main:app`(uvicorn·Docker·fixture)·`main.app` 은 이것을 돌려준다."""
    global _app
    with _app_lock:
        if _app is None:
            _app = create_app()
        return _app


def __getattr__(name):
    # PEP 562: `import main` 만으로는 앱·로그·폴더·시그널을 만들지 않는다(EO R-04). `main.app` 을 처음 읽을 때 만든다.
    if name == "app":
        return get_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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
        from core.utils.runtime_mode import is_fixture_mode
        fixture = is_fixture_mode()
        port = int(os.environ.get('SAFETYREPORT_FIXTURE_PORT', '18773')) if fixture else 6819
        if not 1024 <= port <= 65535 or (fixture and port in (6819, 9222)):
            raise ValueError('invalid fixture port')
        host = '127.0.0.1' if fixture else '0.0.0.0'
        if is_frozen:
            uvicorn.run(get_app(), host=host, port=port, log_config=_UVICORN_LOG_CONFIG,
                        ws_ping_interval=None, ws_ping_timeout=None)
        else:
            uvicorn.run("main:app", host=host, port=port, reload=not fixture, log_config=_UVICORN_LOG_CONFIG,
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
        from core.utils.runtime_mode import is_fixture_mode
        if not is_fixture_mode() and not os.path.exists('/.dockerenv'):
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
