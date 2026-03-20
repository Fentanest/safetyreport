from fastapi import APIRouter, Request, Form
from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse
import pandas as pd
from sqlalchemy import select, desc
from sqlalchemy import select, desc, create_engine
import database
import threading
import logger
import settings.settings as app_settings
from services import data_service

engine = create_engine(f'sqlite:///{app_settings.db_path}', connect_args={"check_same_thread": False})
router = APIRouter(prefix="/rating")
templates = Jinja2Templates(directory="web/templates")

@router.get("/")
async def view_rating_page(request: Request):
    records = data_service.get_unrated_records(engine)
            
    return templates.TemplateResponse("rating.html", {
        "request": request,
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

    def _do_rating():
        import star_rating
        star_rating.run_batch_rating(final_ids, score=score)

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

    import web.routers.crawl as crawl_router
    if crawl_router.active_process and crawl_router.active_process.poll() is None:
        return JSONResponse({"status": "error", "message": "현재 크롤링 프로세스가 진행 중입니다. 충돌 방지를 위해 크롤링 종료 후 실행해주세요."})

    threading.Thread(target=_do_rating, daemon=True).start()
    return JSONResponse({"status": "success", "message": f"총 {len(final_ids)}건에 대해 {score}점 별점 부여를 백그라운드에서 시작합니다. (초고속 API 모드)"})
