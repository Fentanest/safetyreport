from fastapi import APIRouter, Request
from sqlalchemy import create_engine
import settings.settings as app_settings
import pandas as pd
from services import data_service
from core.utils.templating import templates

router = APIRouter()
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
    agency: str = None,
    excludePolice: bool = False,
    onlyPolice: bool = False,
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
        'agency': agency,
        'excludePolice': excludePolice,
        'onlyPolice': onlyPolice,
    }
    records = data_service.get_agency_stats(engine, filters)
        
    return templates.TemplateResponse("stats.html", {
        "request": request,
        "title": "부서 통계",
        "records_traffic_person": records["traffic"]["by_person"],
        "records_traffic_agency": records["traffic"]["by_agency"],
        "records_other_person": records["other"]["by_person"],
        "records_other_agency": records["other"]["by_agency"],
        "f": filters
    })
