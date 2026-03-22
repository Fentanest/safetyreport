from fastapi import APIRouter, Request
from sqlalchemy import select, desc
import pandas as pd
from datetime import datetime, timedelta

from core.database import database
import settings.settings as settings
from sqlalchemy import create_engine
import os
from services import data_service
from core.utils.templating import templates

engine = create_engine(f'sqlite:///{settings.db_path}', connect_args={"check_same_thread": False})
router = APIRouter()

@router.get("/")
async def dashboard(request: Request):
    try:
        stats = data_service.get_dashboard_stats(engine)
    except Exception as e:
        print(f"Error loading dashboard data: {e}")
        stats = {
            "last_crawl_time": "오류", "total": 0, "acceptCount": 0, "partialCount": 0,
            "rejectCount": 0, "fineCount": 0, "penaltyCount": 0,
            "recent_answers": [], "watchlist": []
        }

    return templates.TemplateResponse("index.html", {
        "request": request,
        "title": "대시보드",
        **stats
    })
