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

from web.routers import dashboard, data, settings_route, crawl, stats, rating_route, watchlist_route

app = FastAPI(title="나만의 안전신문고")

app.mount("/static", StaticFiles(directory="web/static"), name="static")
templates = Jinja2Templates(directory="web/templates")

# Initialize required directories
os.makedirs("data", exist_ok=True)
os.makedirs("data/auth", exist_ok=True)
os.makedirs("logs", exist_ok=True)

# DB Init
from sqlalchemy import create_engine, text
import logger
logger.LoggerFactory.create_logger()

import settings.settings as settings
import database
import scheduler
engine = create_engine(f'sqlite:///{settings.db_path}', connect_args={"check_same_thread": False})
database.upgrade_schema(engine)

scheduler.init_scheduler()

def _checkpoint_wal():
    """SQLite WAL 파일을 체크포인트하여 정리합니다."""
    try:
        with engine.connect() as conn:
            conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE);"))
        logger.LoggerFactory.logbot.info("SQLite WAL 체크포인트 완료.")
    except Exception as e:
        logger.LoggerFactory.logbot.warning(f"WAL 체크포인트 실패: {e}")

@app.on_event("shutdown")
async def on_shutdown():
    _checkpoint_wal()

def _signal_handler(signum, frame):
    import sys
    logger.LoggerFactory.logbot.info(f"종료 신호({signum}) 수신 - WAL 정리 후 종료합니다.")
    _checkpoint_wal()
    sys.exit(0)

# SIGINT (Ctrl+C): Windows & Linux 공통
signal.signal(signal.SIGINT, _signal_handler)
# SIGTERM: Linux/Docker 전용 (Windows에서는 지원 안 됨)
if hasattr(signal, 'SIGTERM'):
    signal.signal(signal.SIGTERM, _signal_handler)

app.include_router(dashboard.router)
app.include_router(data.router)
app.include_router(settings_route.router)
app.include_router(crawl.router)
app.include_router(stats.router)
app.include_router(rating_route.router)
app.include_router(watchlist_route.router)

try:
    with open("VERSION", "r", encoding="utf-8") as f:
        APP_VERSION = f.read().strip()
except Exception:
    APP_VERSION = "Unknown"

@app.middleware("http")
async def inject_version_middleware(request: Request, call_next):
    request.state.app_version = APP_VERSION
    response = await call_next(request)
    return response

def start_server():
    uvicorn.run("main:app", host="0.0.0.0", port=6819, reload=True)

if __name__ == "__main__":
    def open_browser():
        time.sleep(2)
        webbrowser.open("http://127.0.0.1:6819")
    
    if not os.path.exists('/.dockerenv'):
        threading.Thread(target=open_browser, daemon=True).start()
    else:
        print("\n\n" + "="*60)
        print("🐳 도커 환경에서 실행 중입니다.")
        print("호스트 장비의 브라우저에서 'http://[서버-IP]:6819'에 접속하세요.")
        print("="*60 + "\n\n")

    start_server()
