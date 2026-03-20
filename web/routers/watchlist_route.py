from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from services import data_service
import settings.settings as settings
from sqlalchemy import create_engine

engine = create_engine(f'sqlite:///{settings.db_path}', connect_args={"check_same_thread": False})

router = APIRouter(prefix="/watchlist")
templates = Jinja2Templates(directory="web/templates")

class WatchlistReq(BaseModel):
    rnums: list[str]

@router.get("/", response_class=HTMLResponse)
def view_watchlist(request: Request):
    records = data_service.get_all_watchlist(engine)
    return templates.TemplateResponse("watchlist.html", {"request": request, "records": records, "title": "감시목록 관리"})

@router.post("/add")
def add_to_watchlist(req: WatchlistReq):
    rnums = data_service.resolve_to_report_numbers(engine, req.rnums)
    if not rnums:
        return {"status": "error", "message": "유효한 신고번호를 찾을 수 없습니다."}
    res = data_service.update_watchlist_status(engine, rnums, 'Y')
    return {"status": "success", "message": f"{res}건이 감시목록에 추가되었습니다."}

@router.post("/remove")
def remove_from_watchlist(req: WatchlistReq):
    res = data_service.update_watchlist_status(engine, req.rnums, 'n')
    return {"status": "success", "message": f"{res}건이 감시목록에서 제거되었습니다."}
