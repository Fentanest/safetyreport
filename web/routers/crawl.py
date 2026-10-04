from fastapi import APIRouter, Request, Form, WebSocket, WebSocketDisconnect
from core.utils import ws_auth
from services import community_gate
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import asyncio
import codecs
import os

import settings.settings as settings
from core.database.engine import get_engine
from core.utils.templating import templates
from services import crawl_control, export_service
from services.crawl_manager import crawl_manager
from services.community_crawl_upload import PendingUploadError

router = APIRouter(prefix="/crawl")


class QueueCrawlReq(BaseModel):
    report_numbers: list[str]


@router.get("/")
def crawl_dashboard(request: Request):
    import settings.settings as app_settings

    app_settings._instance.load()
    return templates.TemplateResponse(request, "crawl.html", {
        "title": "크롤링 제어 및 모니터링",
        "is_running": crawl_manager.is_crawling(),
        "google_sheet_enabled": app_settings.google_sheet_enabled,
    })


@router.get("/status")
def crawl_status(request: Request):
    """크롤 화면이 실행 종료를 알아채는 데 쓰는 관리자 세션용 상태(기술일지 C03). 모바일 API·WS 계약과 무관하다."""
    return {"running": crawl_manager.is_crawling()}


def _community_blocked(status: int, code: str) -> JSONResponse:
    return JSONResponse({"status": "error", "code": code, "message": community_gate.BLOCK_MESSAGES[code]}, status_code=status)


@router.post("/start")
def start_crawl(
    queue_list: str = Form(""),
    crawl_mode: str = Form("full"),
):
    blocked = community_gate.crawl_block()
    if blocked:
        return _community_blocked(*blocked)
    try:
        crawl_control.start_crawl(
            crawl_mode=crawl_mode,
            queue_list=queue_list,
            queue_filename="queue.txt",
            header="=== 크롤링 작업 시작 ===",
            broadcast_source="web_start",
        )
    except RuntimeError as exc:
        if isinstance(exc, PendingUploadError):
            return JSONResponse({"status": "error", "message": str(exc)}, status_code=503)
        if community_gate.block_code(exc):
            return _community_blocked(409 if community_gate.block_code(exc) == community_gate.REBUILD_REQUIRED else 403,
                                      community_gate.block_code(exc))
        return JSONResponse({"status": "error", "message": "크롤링이 이미 실행 중입니다. (수동 또는 스케줄러)."})
    except Exception as exc:
        return JSONResponse({"status": "error", "message": f"오류: {exc}"})

    return JSONResponse({"status": "success", "message": "크롤링이 시작되었습니다."})


@router.post("/kill")
def kill_crawl():
    if not crawl_control.stop_crawl():
        return JSONResponse({"status": "error", "message": "현재 실행 중인 크롤링 프로세스가 없습니다."})
    return JSONResponse({"status": "success", "message": "크롤링 프로세스가 강제로 종료되었습니다."})


@router.post("/enqueue-selected")
def enqueue_selected_crawl(req: QueueCrawlReq):
    if not req.report_numbers:
        return JSONResponse({"status": "error", "message": "신고번호를 하나 이상 선택해주세요."})
    blocked = community_gate.crawl_block()
    if blocked:
        return _community_blocked(*blocked)
    try:
        result = crawl_control.enqueue_reports(req.report_numbers, source="web_selected")
    except ValueError as exc:
        return JSONResponse({"status": "error", "message": str(exc)})
    except RuntimeError as exc:
        return JSONResponse({"status": "error", "message": str(exc)})

    if result["status"] == "queued":
        return JSONResponse({
            "status": "queued",
            "message": f"선택한 {result['requested_count']}건이 현재 크롤링 종료 후 대기열에 추가되었습니다. (대기 중: {result['queue_size']}건)",
        })

    return JSONResponse({
        "status": "success",
        "message": f"선택한 {result['requested_count']}건 크롤링이 시작되었습니다.",
    })


@router.post("/export/excel")
def export_excel():
    if not export_service.export_results(get_engine(), save_excel=True, save_sheet=False):
        return JSONResponse({"status": "error", "message": "저장할 데이터가 없습니다."})
    return JSONResponse({"status": "success", "message": "DB 기반 엑셀 파일 생성이 완료되었습니다."})


@router.post("/export/sheet")
def export_sheet():
    import settings.settings as app_settings

    if not app_settings.google_sheet_enabled:
        return JSONResponse({"status": "error", "message": "구글 시트 연동 기능이 비활성화되어 있습니다."})
    if not export_service.export_results(get_engine(), save_excel=False, save_sheet=True):
        return JSONResponse({"status": "error", "message": "업로드할 데이터가 없습니다."})
    return JSONResponse({"status": "success", "message": "구글 시트 업로드가 완료되었습니다."})


@router.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket):
    if not await ws_auth.authorize(websocket):  # 관리자 세션 또는 API 키 + 커뮤니티 게이트
        return
    await websocket.accept()
    watch = ws_auth.GateWatch(websocket=websocket)
    log_file = os.path.join(settings.datapath, "logs", "current_crawl.log")
    from web.log_stream import stream_log
    await stream_log(websocket, log_file, watch, "로그 파일을 대기 중입니다...\n")
