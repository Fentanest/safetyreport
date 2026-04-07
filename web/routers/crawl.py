from fastapi import APIRouter, Request, Form, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
import asyncio
import sys
import subprocess
import os
from services.crawl_manager import crawl_manager
from services.ws_manager import ws_manager
from core.utils.templating import templates
from sqlalchemy import create_engine
import settings.settings as settings

router = APIRouter(prefix="/crawl")


def _start_pending_crawl_from_queue(pending: list):
    """대기 큐의 신고번호로 새 크롤링을 자동 시작 (스레드 내 호출용)."""
    import datetime, shutil, threading
    if not pending:
        return
    is_frozen = getattr(sys, 'frozen', False)
    cmd = [sys.executable, "--mode", "crawl"] if is_frozen else [sys.executable, "-u", "start.py"]

    queue_file = os.path.join(settings.datapath, 'pending_queue.txt')
    with open(queue_file, 'w', encoding='utf-8') as f:
        f.write('\n'.join(str(r) for r in pending))
    cmd.extend(["--queue", queue_file])

    log_dir = os.path.join(settings.datapath, 'logs')
    log_file = os.path.join(log_dir, 'current_crawl.log')
    if os.path.exists(log_file):
        try:
            now_str = datetime.datetime.now().strftime("%Y-%m-%d %H_%M_%S")
            shutil.copy2(log_file, os.path.join(log_dir, f"crawl_{now_str}.log"))
        except Exception:
            pass
    with open(log_file, 'w', encoding='utf-8') as f:
        f.write(f"=== [대기 큐 자동 시작] 신고번호 {len(pending)}건 ===\n")
        f.write('\n'.join(f"  - {r}" for r in pending) + '\n')

    work_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    if crawl_manager.start_crawl(cmd, cwd=work_dir, log_file=log_file):
        import settings.settings as _ss
        ws_manager.broadcast_from_thread("crawl_started", {
            "source": "pending_queue",
            "count": len(pending),
            "crawl_mode": _ss.crawl_mode,
            "crawl_type": _ss.crawl_type,
        })
        proc = crawl_manager.get_process()

        def _wait(p, lpath):
            if p:
                p.wait()
            crawl_manager.clear_process()
            import time
            time.sleep(1)
            if os.path.exists(lpath):
                try:
                    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H_%M_%S")
                    dst = os.path.join(os.path.dirname(lpath), f"crawl_{now_str}.log")
                    with open(lpath, 'a', encoding='utf-8') as f:
                        f.write("\n[시스템] 크롤링 작업이 완료되었습니다.\n")
                    shutil.copy2(lpath, dst)
                except Exception:
                    pass
            try:
                from services.data_service import get_and_clear_crawl_done, peek_crawl_changes
                done = get_and_clear_crawl_done()
                changed_count = done["changed_count"] if done else 0
                ws_manager.broadcast_from_thread("crawl_finished", {"changed_count": changed_count})
                changes = peek_crawl_changes()
                if changes:
                    ws_manager.broadcast_from_thread("crawl_changes", {"changes": changes})
            except Exception:
                pass
            # 재귀적으로 대기 큐 처리
            next_pending = crawl_manager.pop_pending()
            if next_pending:
                _start_pending_crawl_from_queue(next_pending)

        if proc:
            threading.Thread(target=_wait, args=(proc, log_file), daemon=True).start()

@router.get("/")
async def crawl_dashboard(request: Request):
    import settings.settings as app_settings
    app_settings._instance.load()
    return templates.TemplateResponse(request, "crawl.html", {
        "title": "크롤링 제어 및 모니터링",
        "is_running": crawl_manager.is_crawling(),
        "crawl_type": app_settings.crawl_type,
        "max_empty_pages": app_settings.config.get('SETTINGS', 'max_empty_pages', fallback=3),
        "google_sheet_enabled": app_settings.google_sheet_enabled
    })

@router.post("/start")
async def start_crawl(
    login_mode: str = Form("member"),
    queue_list: str = Form(""),
    crawl_mode: str = Form("full"),
    crawl_type: str = Form("api"),
    max_empty_pages: int = Form(3)
):
    import settings.settings as app_settings
    app_settings._instance.update_config('SETTINGS', 'max_empty_pages', max_empty_pages)
    app_settings._instance.update_config('Crawler', 'crawl_type', crawl_type)
    save_mode = 'full' if crawl_mode == 'reset' else crawl_mode
    app_settings._instance.update_config('SETTINGS', 'crawl_mode', save_mode)
    app_settings._instance.save()

    log_dir = os.path.join(settings.datapath, 'logs')
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, 'current_crawl.log')

    with open(log_file, 'w', encoding='utf-8') as f:
        f.write("=== 크롤링 작업 시작 ===\n")

    is_frozen = getattr(sys, 'frozen', False)
    if is_frozen:
        cmd = [sys.executable, "--mode", "crawl"]
    else:
        cmd = [sys.executable, "-u", "start.py"]

    if login_mode == "nonmember":
        cmd.append("--nonmember")
    if crawl_mode == "min":
        cmd.append("--min")
    elif crawl_mode == "reset":
        cmd.append("--reset")

    if queue_list.strip():
        queue_file = os.path.join(settings.datapath, 'queue.txt')
        with open(queue_file, 'w', encoding='utf-8') as qf:
            qf.write(queue_list)
        cmd.append("--queue")
        cmd.append(queue_file)

    work_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    if not crawl_manager.start_crawl(cmd, cwd=work_dir, log_file=log_file):
        return JSONResponse({"status": "error", "message": "크롤링이 이미 실행 중입니다. (수동 또는 스케줄러)."})

    proc = crawl_manager.get_process()

    # WS 브로드캐스트: 크롤링 시작
    try:
        import asyncio as _asyncio
        loop = _asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(ws_manager.broadcast("crawl_started", {
                "login_mode": login_mode,
                "crawl_mode": crawl_mode,
                "crawl_type": crawl_type,
            }))
    except Exception:
        pass

    def wait_and_rotate_log(p, lpath):
        if p:
            p.wait()
        crawl_manager.clear_process()
        import time, shutil, datetime
        time.sleep(1)
        if os.path.exists(lpath):
            now_str = datetime.datetime.now().strftime("%Y-%m-%d %H_%M_%S")
            dst = os.path.join(os.path.dirname(lpath), f"crawl_{now_str}.log")
            try:
                with open(lpath, 'a', encoding='utf-8') as f:
                    f.write(f"\n[시스템] 크롤링 작업이 성공적으로 종료되었습니다.\n전체 상세 로그는 {os.path.basename(dst)} 파일로 백업 보관되었습니다.\n")
                shutil.copy2(lpath, dst)
            except Exception:
                pass
        # WS 브로드캐스트: 크롤링 완료 + 변경 상세
        try:
            from services.data_service import get_and_clear_crawl_done, peek_crawl_changes
            done = get_and_clear_crawl_done()
            changed_count = done["changed_count"] if done else 0
            ws_manager.broadcast_from_thread("crawl_finished", {"changed_count": changed_count})
            changes = peek_crawl_changes()
            if changes:
                ws_manager.broadcast_from_thread("crawl_changes", {"changes": changes})
        except Exception:
            pass
        # 대기 큐에 쌓인 신고번호가 있으면 자동으로 다음 크롤링 시작
        try:
            pending = crawl_manager.pop_pending()
            if pending:
                import threading as _thr
                _start_pending_crawl_from_queue(pending)
        except Exception:
            pass

    import threading
    if proc:
        threading.Thread(target=wait_and_rotate_log, args=(proc, log_file), daemon=True).start()

    return JSONResponse({"status": "success", "message": "크롤링이 \uc2dc\uc791\ub418\uc5c8\uc2b5\ub2c8\ub2e4."})

@router.post("/resume")
async def resume_crawl():
    # Signal start.py to resume if it is waiting for manual login
    signal_file = os.path.join(settings.datapath, 'resume.sig')
    with open(signal_file, 'w', encoding='utf-8') as f:
        f.write("RESUME")
    return JSONResponse({"status": "success", "message": "크롤링 재개 신호가 전송되었습니다."})

@router.post("/kill")
async def kill_crawl():
    if not crawl_manager.is_crawling():
        return JSONResponse({"status": "error", "message": "현재 실행 중인 크롤링 프로세스가 없습니다."})

    try:
        crawl_manager.stop_crawl()
        log_file = os.path.join(settings.datapath, 'logs', 'current_crawl.log')
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write("\n[시스템] 사용자 요청으로 크롤링 프로세스가 강제 종료되었습니다.\n")
        return JSONResponse({"status": "success", "message": "크롤링 프로세스가 강제로 종료되었습니다."})
    except Exception as e:
        return JSONResponse({"status": "error", "message": f"오류: {e}"})

@router.post("/export/excel")
async def export_excel():
    from core.database import database
    from core.utils import export
    from sqlalchemy import create_engine
    import settings.settings as app_settings
    engine = create_engine(f'sqlite:///{app_settings.db_path}', connect_args={"check_same_thread": False})
    df = database.load_results(engine=engine)
    if df.empty: return JSONResponse({"status": "error", "message": "저장할 데이터가 없습니다."})
    processed_df, _ = export._process_dataframe(df)
    export.save_to_excel(processed_df)
    return JSONResponse({"status": "success", "message": "DB 기반 엑셀 파일 생성이 완료되었습니다."})

@router.post("/export/sheet")
async def export_sheet():
    from core.database import database
    from core.utils import export
    from sqlalchemy import create_engine
    import settings.settings as app_settings
    if not app_settings.google_sheet_enabled:
        return JSONResponse({"status": "error", "message": "구글 시트 연동 기능이 비활성화되어 있습니다."})
    engine = create_engine(f'sqlite:///{app_settings.db_path}', connect_args={"check_same_thread": False})
    df = database.load_results(engine=engine)
    if df.empty: return JSONResponse({"status": "error", "message": "업로드할 데이터가 없습니다."})
    processed_df, photo_cols = export._process_dataframe(df)
    try:
        export.save_to_google_sheet(processed_df, photo_cols)
        return JSONResponse({"status": "success", "message": "구글 시트 업로드가 완료되었습니다."})
    except Exception as e:
        return JSONResponse({"status": "error", "message": f"구글 시트 업로드 중 오류 발생: {e}"})

@router.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket):
    await websocket.accept()
    log_file = os.path.join(settings.datapath, 'logs', 'current_crawl.log')
    
    try:
        if not os.path.exists(log_file):
            await websocket.send_text("로그 파일을 대기 중입니다...\n")
            while not os.path.exists(log_file):
                await asyncio.sleep(1)
                
        # Initial read
        if os.path.exists(log_file):
            with open(log_file, 'r', encoding='utf-8', errors='replace') as f:
                data = f.read()
                if data:
                    await websocket.send_text(data)
            
        last_size = os.path.getsize(log_file) if os.path.exists(log_file) else 0
        
        # Tail the file chunk by chunk asynchronously
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
                # File was truncated (new crawl started)
                last_size = 0
                
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"WS error: {e}")
