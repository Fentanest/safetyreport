from fastapi import APIRouter, Request, Form, WebSocket, WebSocketDisconnect
from core.utils import ws_auth
from fastapi.responses import JSONResponse
import settings.settings as app_settings
import os
import asyncio
from core.database.engine import get_engine
from services import data_service, rating_eligibility, rating_service
from core.utils.templating import templates

engine = get_engine()
router = APIRouter(prefix="/rating")

@router.get("/")
def view_rating_page(request: Request):
    records = data_service.get_unrated_records(engine)
            
    return templates.TemplateResponse(request, "rating.html", {
        "title": "자동 별점 주기",
        "records": records,
        "phone_number": app_settings.phone_number or "",
        "rating_cause_max": rating_eligibility.RATING_CAUSE_MAX,
    })

@router.post("/start")
def start_batch_rating(request: Request, ids: str = Form(""), score: int = Form(5), cause: str = Form("")):
    id_list = [i.strip() for i in ids.replace(',', '\n').split('\n') if i.strip()]
    if not id_list:
        return JSONResponse({"status": "error", "message": "별점을 부여할 신고 번호 또는 ID를 입력해주세요."})

    try:
        final_ids = rating_service.start_batch_rating(engine, id_list, score, cause)
    except ValueError as exc:
        return JSONResponse({"status": "error", "message": str(exc)})
    except RuntimeError as exc:
        return JSONResponse({"status": "error", "message": str(exc)})

    return JSONResponse({"status": "success", "message": f"총 {len(final_ids)}건에 대해 {score}점 별점 부여를 백그라운드에서 시작합니다. (실시간 로그를 확인하세요)"})

@router.websocket("/ws/rating_logs")
async def websocket_rating_logs(websocket: WebSocket):
    if not await ws_auth.authorize(websocket):  # 관리자 세션 또는 API 키 + 커뮤니티 게이트
        return
    await websocket.accept()
    watch = ws_auth.GateWatch()
    log_file = os.path.join(app_settings.datapath, 'logs', 'current_rating.log')
    
    from web.log_stream import stream_log
    await stream_log(websocket, log_file, watch, "별점 로그 파일을 대기 중입니다...\n")
