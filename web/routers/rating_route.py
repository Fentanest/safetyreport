from fastapi import APIRouter, Request, Form, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
import pandas as pd
from sqlalchemy import select, desc, create_engine
from core.database import database
import threading
from core.utils import logger
import settings.settings as app_settings
import os
import time
import asyncio
from services import data_service
from core.utils.templating import templates

engine = create_engine(f'sqlite:///{app_settings.db_path}', connect_args={"check_same_thread": False})
router = APIRouter(prefix="/rating")

@router.get("/")
async def view_rating_page(request: Request):
    records = data_service.get_unrated_records(engine)
            
    return templates.TemplateResponse(request, "rating.html", {
        "title": "자동 별점 주기",
        "records": records
    })

@router.post("/start")
async def start_batch_rating(request: Request, ids: str = Form(""), score: int = Form(5)):
    id_list = [i.strip() for i in ids.replace(',', '\n').split('\n') if i.strip()]
    if not id_list:
        return JSONResponse({"status": "error", "message": "별점을 부여할 신고 번호 또는 ID를 입력해주세요."})
    
    # Extract actual internal IDs using DataService
    final_ids = data_service.resolve_ids_for_rating(engine, id_list)
    if not final_ids:
        return JSONResponse({"status": "error", "message": "유효한 신고 건을 찾을 수 없습니다."})

    log_dir = os.path.join(app_settings.datapath, 'logs')
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, 'current_rating.log')
    
    # 기존 로그가 있으면 백업 (기존 로그의 최종 수정 시각 기준)
    if os.path.exists(log_file):
        try:
            mtime = os.path.getmtime(log_file)
            ts = time.strftime('%Y%m%d_%H%M%S', time.localtime(mtime))
            backup_name = os.path.join(log_dir, f"{ts}_rating.log")
            import shutil
            shutil.move(log_file, backup_name)
        except Exception as e:
            logger.LoggerFactory.get_logger().error(f"별점 로그 백업 중 오류: {e}")

    with open(log_file, 'w', encoding='utf-8') as f:
        f.write("=== 별점 작업 로그 시작 ===\n")

    def _do_rating():
        from services import star_rating_service as star_rating
        star_rating.run_batch_rating(final_ids, score=score, log_file=log_file)

    # ==============================================================================
    # 추후 API 통신 불가 시 (가령 로그인 세션 검증 추가) 복구용 셀레니움 기반 로직 백업
    # ==============================================================================
    # def _do_rating_selenium():
    #     import driv, login, time, star_rating, settings.settings as settings
    #     driver = driv.create_driver()
    #     if not driver: return
    #     driver.get(settings.mysafereporturl)
    #     time.sleep(2)
    #     if not login.login_mysafety(driver):
    #         driver.quit()
    #         return
    #     
    #     star_rating.run_batch_rating_selenium(driver, final_ids, score=score)
    #     driver.quit()
    # ==============================================================================

    from services.crawl_manager import crawl_manager
    if crawl_manager.is_crawling():
        return JSONResponse({"status": "error", "message": "현재 크롤링 프로세스가 진행 중입니다. 충돌 방지를 위해 크롤링 종료 후 실행해주세요."})

    threading.Thread(target=_do_rating, daemon=True).start()
    return JSONResponse({"status": "success", "message": f"총 {len(final_ids)}건에 대해 {score}점 별점 부여를 백그라운드에서 시작합니다. (실시간 로그를 확인하세요)"})

@router.websocket("/ws/rating_logs")
async def websocket_rating_logs(websocket: WebSocket):
    await websocket.accept()
    log_file = os.path.join(app_settings.datapath, 'logs', 'current_rating.log')
    
    try:
        if not os.path.exists(log_file):
            await websocket.send_text("별점 로그 파일을 대기 중입니다...\n")
            while not os.path.exists(log_file):
                await asyncio.sleep(1)
                
        # Initial read
        if os.path.exists(log_file):
            with open(log_file, 'r', encoding='utf-8', errors='replace') as f:
                data = f.read()
                if data:
                    await websocket.send_text(data)
            
        last_size = os.path.getsize(log_file) if os.path.exists(log_file) else 0
        
        while True:
            await asyncio.sleep(0.5)
            if not os.path.exists(log_file):
                continue
                
            current_size = os.path.getsize(log_file)
            if current_size > last_size:
                with open(log_file, 'r', encoding='utf-8', errors='replace') as f:
                    f.seek(last_size)
                    new_data = f.read()
                    if new_data:
                        await websocket.send_text(new_data)
                last_size = current_size
            elif current_size < last_size:
                last_size = 0
                
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"Rating WS error: {e}")
