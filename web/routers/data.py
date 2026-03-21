from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, desc
import pandas as pd

import database
import settings.settings as settings
from sqlalchemy import create_engine
from services import data_service

engine = create_engine(f'sqlite:///{settings.db_path}', connect_args={"check_same_thread": False})
router = APIRouter(prefix="/data")
templates = Jinja2Templates(directory="web/templates")

@router.get("/traffic")
async def view_traffic(request: Request):
    records = data_service.get_traffic_records(engine)
    return templates.TemplateResponse("data_table.html", {
        "request": request, "title": "교통위반 전체 보기", 
        "records": records, "table_id": "trafficTable",
        "order_col": '[ 4, "desc" ]'
    })

@router.get("/other")
async def view_other(request: Request):
    records = data_service.get_other_records(engine)
    return templates.TemplateResponse("data_table.html", {
        "request": request, "title": "기타 위반 조회", 
        "records": records, "table_id": "otherTable",
        "order_col": '[ 4, "desc" ]'
    })

@router.get("/duplicates")
async def view_duplicates(request: Request):
    records = data_service.get_duplicate_records(engine)
    return templates.TemplateResponse("data_table.html", {
        "request": request, "title": "중복 차량 (2건 이상) 보기", 
        "records": records, "table_id": "duplicateTable",
        "order_col": '[ 6, "asc" ]'
    })
