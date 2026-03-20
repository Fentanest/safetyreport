from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import create_engine
import settings.settings as app_settings
import pandas as pd
import database
from services import data_service

router = APIRouter()
templates = Jinja2Templates(directory="web/templates")
engine = create_engine(f'sqlite:///{app_settings.db_path}', connect_args={"check_same_thread": False})

@router.get("/stats")
async def view_stats(
    request: Request,
    reportName: str = None,
    law: str = None,
    location: str = None,
    reportDateStart: str = None,
    reportDateEnd: str = None,
    occurDateStart: str = None,
    occurDateEnd: str = None,
    responseDateStart: str = None,
    responseDateEnd: str = None,
    occurTimeStart: str = None,
    occurTimeEnd: str = None,
):
    filters = {
        'reportName': reportName,
        'law': law,
        'location': location,
        'reportDateStart': reportDateStart,
        'reportDateEnd': reportDateEnd,
        'occurDateStart': occurDateStart,
        'occurDateEnd': occurDateEnd,
        'responseDateStart': responseDateStart,
        'responseDateEnd': responseDateEnd,
        'occurTimeStart': occurTimeStart,
        'occurTimeEnd': occurTimeEnd,
    }
    records = data_service.get_agency_stats(engine, filters)
        
    return templates.TemplateResponse("stats.html", {
        "request": request,
        "title": "부서 통계",
        "records_traffic": records["traffic"],
        "records_other": records["other"],
        "f": filters
    })
