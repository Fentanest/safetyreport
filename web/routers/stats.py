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
    agencyExact: bool = False,
    excludePolice: bool = False,
    onlyPolice: bool = False,
    year: str = None,
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
        'agencyExact': agencyExact,
        'excludePolice': excludePolice,
        'onlyPolice': onlyPolice,
        'year': year,
    }
    records = data_service.get_agency_stats(engine, filters)

    return templates.TemplateResponse(request, "stats.html", {
        "title": "부서 통계",
        "available_years": records.get("available_years", []),
        "current_year": year or "all",
        "records_traffic_agency":         records["traffic"]["by_agency"],
        "records_traffic_person":         records["traffic"]["by_person"],
        "records_traffic_police_agency":  records["traffic"]["police_by_agency"],
        "records_traffic_police_person":  records["traffic"]["police_by_person"],
        "records_traffic_other_agency":   records["traffic"]["other_by_agency"],
        "records_traffic_other_person":   records["traffic"]["other_by_person"],
        "records_parking_agency":         records["parking"]["by_agency"],
        "records_parking_person":         records["parking"]["by_person"],
        "records_parking_police_agency":  records["parking"]["police_by_agency"],
        "records_parking_police_person":  records["parking"]["police_by_person"],
        "records_parking_other_agency":   records["parking"]["other_by_agency"],
        "records_parking_other_person":   records["parking"]["other_by_person"],
        "records_other_agency":           records["other"]["by_agency"],
        "records_other_person":           records["other"]["by_person"],
        "records_other_police_agency":    records["other"]["police_by_agency"],
        "records_other_police_person":    records["other"]["police_by_person"],
        "records_other_other_agency":     records["other"]["other_by_agency"],
        "records_other_other_person":     records["other"]["other_by_person"],
        "f": filters
    })
