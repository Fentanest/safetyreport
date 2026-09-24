from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from services import data_service
from core.database.engine import get_engine
from core.utils.templating import templates

engine = get_engine()

router = APIRouter(prefix="/watchlist")

class WatchlistReq(BaseModel):
    rnums: list[str]

@router.get("/", response_class=HTMLResponse)
def view_watchlist(request: Request):
    records = data_service.get_all_watchlist(engine)
    return templates.TemplateResponse(request, "watchlist.html", {"records": records, "title": "감시목록 관리"})

@router.post("/add")
def add_to_watchlist(req: WatchlistReq):
    rnums = data_service.resolve_to_report_numbers(engine, req.rnums)
    if not rnums:
        return {"status": "error", "message": "유효한 신고번호를 찾을 수 없습니다."}
    res = data_service.update_watchlist_status(engine, rnums, 'Y')
    return {"status": "success", "message": f"{res}건이 감시목록에 추가되었습니다."}

@router.post("/remove")
def remove_from_watchlist(req: WatchlistReq):
    # ID 로 와도 지운다(S-34). 신고가 이미 사라진 감시 항목도 지울 수 있게 받은 값도 그대로 넣는다.
    rnums = sorted(set(data_service.resolve_to_report_numbers(engine, req.rnums)) | {r for r in req.rnums if r})
    res = data_service.update_watchlist_status(engine, rnums, 'N')
    return {"status": "success", "message": f"{res}건이 감시목록에서 제거되었습니다."}
