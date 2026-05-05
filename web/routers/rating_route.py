from fastapi import APIRouter, Request, Form, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
import settings.settings as app_settings
import os
import asyncio
from core.database.engine import get_engine
from services import data_service, rating_service
from core.utils.templating import templates

engine = get_engine()
router = APIRouter(prefix="/rating")

@router.get("/")
async def view_rating_page(request: Request):
    records = data_service.get_unrated_records(engine)
            
    return templates.TemplateResponse(request, "rating.html", {
        "title": "자동 별점 주기",
        "records": records,
        "phone_number": app_settings.phone_number or ""
    })

@router.post("/start")
async def start_batch_rating(request: Request, ids: str = Form(""), score: int = Form(5)):
    id_list = [i.strip() for i in ids.replace(',', '\n').split('\n') if i.strip()]
    if not id_list:
        return JSONResponse({"status": "error", "message": "별점을 부여할 신고 번호 또는 ID를 입력해주세요."})

    try:
        final_ids = rating_service.start_batch_rating(engine, id_list, score)
    except ValueError as exc:
        return JSONResponse({"status": "error", "message": str(exc)})
    except RuntimeError as exc:
        return JSONResponse({"status": "error", "message": str(exc)})

    return JSONResponse({"status": "success", "message": f"총 {len(final_ids)}건에 대해 {score}점 별점 부여를 백그라운드에서 시작합니다. (실시간 로그를 확인하세요)"})

@router.websocket("/ws/rating_logs")
async def websocket_rating_logs(websocket: WebSocket):
    await websocket.accept()
    log_file = os.path.join(app_settings.datapath, 'logs', 'current_rating.log')
    
    try:
        if not os.path.exists(log_file):
            await websocket.send_text("별점 로그 파일을 대기 중입니다...\n")
            while not os.path.exists(log_file):
                await asyncio.sleep(1)
                
        # Initial read
        if os.path.exists(log_file):
            with open(log_file, 'r', encoding='utf-8', errors='replace') as f:
                data = f.read()
                if data:
                    await websocket.send_text(data)
            
        last_size = os.path.getsize(log_file) if os.path.exists(log_file) else 0
        
        while True:
            await asyncio.sleep(0.5)
            if not os.path.exists(log_file):
                continue
                
            current_size = os.path.getsize(log_file)
            if current_size > last_size:
                with open(log_file, 'r', encoding='utf-8', errors='replace') as f:
                    f.seek(last_size)
                    new_data = f.read()
                    if new_data:
                        await websocket.send_text(new_data)
                last_size = current_size
            elif current_size < last_size:
                last_size = 0
                
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"Rating WS error: {e}")
