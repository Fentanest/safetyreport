from fastapi import APIRouter, Request, Query
from typing import Optional
from sqlalchemy import select, desc
import pandas as pd

from core.database import database
import settings.settings as settings
from sqlalchemy import create_engine
from services import data_service
from core.utils.templating import templates

engine = create_engine(f'sqlite:///{settings.db_path}', connect_args={"check_same_thread": False})
router = APIRouter(prefix="/data")


def _build_filters(status=None, fine=None, agency=None, person=None, agencyExact=False):
    f = {}
    if status: f['status'] = status
    if fine: f['fine'] = fine
    if agency: f['agency'] = agency
    if person: f['person'] = person
    if agencyExact: f['agencyExact'] = True
    return f or None


def _filter_title(base, status=None, fine=None, agency=None, person=None):
    parts = [base]
    if agency: parts.append(f'기관: {agency}')
    if person: parts.append(f'담당자: {person}')
    if status: parts.append(f'상태: {status}')
    if fine: parts.append(f'과태료: {fine}')
    return ' / '.join(parts)


@router.get("/traffic")
async def view_traffic(
    request: Request,
    status: Optional[str] = Query(None),
    fine: Optional[str] = Query(None),
    agency: Optional[str] = Query(None),
    person: Optional[str] = Query(None),
    agencyExact: bool = Query(False),
):
    filters = _build_filters(status, fine, agency, person, agencyExact)
    records = data_service.get_traffic_records(engine, filters)
    title = _filter_title("교통위반 전체 보기", status, fine, agency, person)
    return templates.TemplateResponse(request, "data_table.html", {
        "title": title,
        "records": records, "table_id": "trafficTable",
        "order_col": '[ 4, "desc" ]'
    })


@router.get("/other")
async def view_other(
    request: Request,
    status: Optional[str] = Query(None),
    fine: Optional[str] = Query(None),
    agency: Optional[str] = Query(None),
    person: Optional[str] = Query(None),
    agencyExact: bool = Query(False),
):
    filters = _build_filters(status, fine, agency, person, agencyExact)
    records = data_service.get_other_records(engine, filters)
    title = _filter_title("기타 위반 조회", status, fine, agency, person)
    return templates.TemplateResponse(request, "data_table.html", {
        "title": title,
        "records": records, "table_id": "otherTable",
        "order_col": '[ 4, "desc" ]'
    })


@router.get("/all")
async def view_all(
    request: Request,
    status: Optional[str] = Query(None),
    fine: Optional[str] = Query(None),
    agency: Optional[str] = Query(None),
    person: Optional[str] = Query(None),
):
    filters = _build_filters(status, fine, agency, person)
    records = data_service.get_all_records(engine, filters)
    title = _filter_title("전체 신고 조회", status, fine, agency, person)
    return templates.TemplateResponse(request, "data_table.html", {
        "title": title,
        "records": records, "table_id": "allTable",
        "order_col": '[ 4, "desc" ]'
    })


@router.get("/duplicates")
async def view_duplicates(request: Request):
    records = data_service.get_duplicate_records(engine)
    return templates.TemplateResponse(request, "data_table.html", {
        "title": "중복 차량 (2건 이상) 보기",
        "records": records, "table_id": "duplicateTable",
        "order_col": '[ 6, "asc" ]'
    })
