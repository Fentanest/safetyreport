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

from web.routers import dashboard, data, settings_route, crawl, stats, rating_route, watchlist_route, file_browser_route
import subprocess
import sys

bot_process = None
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

@app.on_event("startup")
async def on_startup():
    global bot_process
    # 스케줄러 시작
    scheduler.init_scheduler()
    
    if settings.telegram_enabled:
        try:
            logger.LoggerFactory.logbot.info("텔레그램 봇 프로세스를 시작합니다.")
            bot_process = subprocess.Popen([sys.executable, "bot.py"])
        except Exception as e:
            logger.LoggerFactory.logbot.error(f"봇 프로세스 시작 실패: {e}")

@app.on_event("shutdown")
async def on_shutdown():
    global bot_process
    if bot_process:
        logger.LoggerFactory.logbot.info("텔레그램 봇 프로세스를 종료합니다.")
        bot_process.terminate()
        try:
            bot_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            bot_process.kill()
    _checkpoint_wal()

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

app.include_router(dashboard.router)
app.include_router(data.router)
app.include_router(settings_route.router)
app.include_router(crawl.router)
app.include_router(stats.router)
app.include_router(rating_route.router)
app.include_router(watchlist_route.router)
app.include_router(file_browser_route.router)

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
