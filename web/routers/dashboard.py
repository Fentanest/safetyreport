import os

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import FileResponse
from core.database.engine import get_engine
from services import data_service
from services import sunwi_service
from core.utils.templating import templates
from web.routers.filters import default_dedupe_mode

engine = get_engine()
router = APIRouter()

@router.get("/")
def dashboard(request: Request):
    try:
        stats = data_service.get_dashboard_stats(engine, mode=default_dedupe_mode())
        stats["load_error"] = False
    except Exception as e:
        # 집계 실패를 0건으로 보여 주면 신고가 없는 것처럼 보인다 — 실패를 화면에 밝힌다(기술일지 O-02)
        from core.utils import logger
        if logger.LoggerFactory.logbot:
            logger.LoggerFactory.logbot.error(f"[dashboard] 대시보드 집계 실패: {type(e).__name__}: {e}")
        stats = {"last_crawl_time": "확인 실패", "load_error": True, "recent_answers": [], "watchlist": []}

    # 전국 안전신고 현황(Sunwi)은 2026-09-28 통계 화면으로 옮겼다(/stats 하단). /sunwi/* 경로는 그대로 쓴다.
    return templates.TemplateResponse(request, "index.html", {
        "title": "대시보드",
        **stats
    })


@router.get("/sunwi/payload")
def sunwi_payload():
    return sunwi_service.get_dashboard_payload()


@router.get("/sunwi/download/top5")
def download_sunwi_top5_csv():
    csv_path = sunwi_service.get_top5_csv_path()
    if not os.path.exists(csv_path):
        raise HTTPException(status_code=404, detail="CSV file not found")

    return FileResponse(
        csv_path,
        media_type="text/csv",
        filename=os.path.basename(csv_path),
    )


@router.get("/sunwi/download/all")
def download_sunwi_all_csv():
    csv_path = sunwi_service.get_all_csv_path()
    if not os.path.exists(csv_path):
        raise HTTPException(status_code=404, detail="CSV file not found")

    return FileResponse(
        csv_path,
        media_type="text/csv",
        filename=os.path.basename(csv_path),
    )
