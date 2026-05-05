from fastapi import APIRouter, Request, Form, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
import asyncio
import os

import settings.settings as settings
from core.database.engine import get_engine
from core.utils.templating import templates
from services import crawl_control, export_service
from services.crawl_manager import crawl_manager

router = APIRouter(prefix="/crawl")


@router.get("/")
async def crawl_dashboard(request: Request):
    import settings.settings as app_settings

    app_settings._instance.load()
    return templates.TemplateResponse(request, "crawl.html", {
        "title": "크롤링 제어 및 모니터링",
        "is_running": crawl_manager.is_crawling(),
        "crawl_type": app_settings.crawl_type,
        "max_empty_pages": app_settings.config.get("SETTINGS", "max_empty_pages", fallback=3),
        "google_sheet_enabled": app_settings.google_sheet_enabled,
    })


@router.post("/start")
async def start_crawl(
    login_mode: str = Form("member"),
    queue_list: str = Form(""),
    crawl_mode: str = Form("full"),
    crawl_type: str = Form("api"),
    max_empty_pages: int = Form(3),
):
    try:
        crawl_control.start_crawl(
            login_mode=login_mode,
            crawl_mode=crawl_mode,
            crawl_type="api" if crawl_type == "api" else "legacy",
            max_empty_pages=max_empty_pages,
            queue_list=queue_list,
            queue_filename="queue.txt",
            header="=== 크롤링 작업 시작 ===",
            broadcast_source="web_start",
        )
    except RuntimeError:
        return JSONResponse({"status": "error", "message": "크롤링이 이미 실행 중입니다. (수동 또는 스케줄러)."})
    except Exception as exc:
        return JSONResponse({"status": "error", "message": f"오류: {exc}"})

    return JSONResponse({"status": "success", "message": "크롤링이 시작되었습니다."})


@router.post("/resume")
async def resume_crawl():
    crawl_control.resume_crawl()
    return JSONResponse({"status": "success", "message": "크롤링 재개 신호가 전송되었습니다."})


@router.post("/kill")
async def kill_crawl():
    if not crawl_control.stop_crawl():
        return JSONResponse({"status": "error", "message": "현재 실행 중인 크롤링 프로세스가 없습니다."})
    return JSONResponse({"status": "success", "message": "크롤링 프로세스가 강제로 종료되었습니다."})


@router.post("/export/excel")
async def export_excel():
    if not export_service.export_results(get_engine(), save_excel=True, save_sheet=False):
        return JSONResponse({"status": "error", "message": "저장할 데이터가 없습니다."})
    return JSONResponse({"status": "success", "message": "DB 기반 엑셀 파일 생성이 완료되었습니다."})


@router.post("/export/sheet")
async def export_sheet():
    import settings.settings as app_settings

    if not app_settings.google_sheet_enabled:
        return JSONResponse({"status": "error", "message": "구글 시트 연동 기능이 비활성화되어 있습니다."})
    if not export_service.export_results(get_engine(), save_excel=False, save_sheet=True):
        return JSONResponse({"status": "error", "message": "업로드할 데이터가 없습니다."})
    return JSONResponse({"status": "success", "message": "구글 시트 업로드가 완료되었습니다."})


@router.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket):
    await websocket.accept()
    log_file = os.path.join(settings.datapath, "logs", "current_crawl.log")

    try:
        if not os.path.exists(log_file):
            await websocket.send_text("로그 파일을 대기 중입니다...\n")
            while not os.path.exists(log_file):
                await asyncio.sleep(1)

        if os.path.exists(log_file):
            with open(log_file, "r", encoding="utf-8", errors="replace") as file_obj:
                data = file_obj.read()
                if data:
                    await websocket.send_text(data)

        last_size = os.path.getsize(log_file) if os.path.exists(log_file) else 0

        while True:
            await asyncio.sleep(0.5)
            if not os.path.exists(log_file):
                continue

            current_size = os.path.getsize(log_file)
            if current_size > last_size:
                with open(log_file, "r", encoding="utf-8", errors="replace") as file_obj:
                    file_obj.seek(last_size)
                    new_data = file_obj.read()
                    if new_data:
                        await websocket.send_text(new_data)
                last_size = current_size
            elif current_size < last_size:
                last_size = 0
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        print(f"WS error: {exc}")
