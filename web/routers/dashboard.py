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
async def dashboard(request: Request):
    try:
        stats = data_service.get_dashboard_stats(engine, mode=default_dedupe_mode())
    except Exception as e:
        print(f"Error loading dashboard data: {e}")
        stats = {
            "last_crawl_time": "오류", "total": 0, "acceptCount": 0, "partialCount": 0,
            "rejectCount": 0, "fineCount": 0, "penaltyCount": 0,
            "recent_answers": [], "watchlist": []
        }

    stats["sunwi"] = sunwi_service.get_dashboard_payload()

    return templates.TemplateResponse(request, "index.html", {
        "title": "대시보드",
        **stats
    })


@router.get("/sunwi/payload")
async def sunwi_payload():
    return sunwi_service.get_dashboard_payload()


@router.get("/sunwi/download/top5")
async def download_sunwi_top5_csv():
    csv_path = sunwi_service.get_top5_csv_path()
    if not os.path.exists(csv_path):
        raise HTTPException(status_code=404, detail="CSV file not found")

    return FileResponse(
        csv_path,
        media_type="text/csv",
        filename=os.path.basename(csv_path),
    )


@router.get("/sunwi/download/all")
async def download_sunwi_all_csv():
    csv_path = sunwi_service.get_all_csv_path()
    if not os.path.exists(csv_path):
        raise HTTPException(status_code=404, detail="CSV file not found")

    return FileResponse(
        csv_path,
        media_type="text/csv",
        filename=os.path.basename(csv_path),
    )
