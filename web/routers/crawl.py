from fastapi import APIRouter, Request, Form, WebSocket, WebSocketDisconnect
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, JSONResponse
import asyncio
import sys
import subprocess
import os

router = APIRouter(prefix="/crawl")
templates = Jinja2Templates(directory="web/templates")

active_process = None

@router.get("/")
async def crawl_dashboard(request: Request):
    import settings.settings as app_settings
    app_settings._instance.load()
    return templates.TemplateResponse("crawl.html", {
        "request": request, 
        "title": "크롤링 제어 및 모니터링",
        "is_running": active_process is not None and active_process.poll() is None,
        "max_empty_pages": app_settings.config.get('SETTINGS', 'max_empty_pages', fallback=3)
    })

@router.post("/start")
async def start_crawl(
    login_mode: str = Form("member"),
    queue_list: str = Form(""),
    crawl_mode: str = Form("full"),
    max_empty_pages: int = Form(3)
):
    global active_process
    
    import settings.settings as app_settings
    app_settings._instance.update_config('SETTINGS', 'max_empty_pages', max_empty_pages)
    app_settings._instance.save()

    # Check if already running
    if active_process is not None and active_process.poll() is None:
        return JSONResponse({"status": "error", "message": "크롤링이 이미 실행 중입니다."})

    work_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    log_dir = os.path.join(work_dir, 'data', 'logs')
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, 'current_crawl.log')
    
    with open(log_file, 'w', encoding='utf-8') as f:
        f.write("=== 크롤링 작업 대기 ===\n")

    cmd = [sys.executable, "-u", "start.py"]
    if login_mode == "nonmember":
        cmd.append("--nonmember")
    if crawl_mode == "min":
        cmd.append("--min")
    elif crawl_mode == "reset":
        cmd.append("--reset")
        
    if queue_list.strip():
        # Store queue list to a temp file for start.py to read
        queue_file = os.path.join(work_dir, 'data', 'queue.txt')
        with open(queue_file, 'w', encoding='utf-8') as qf:
            qf.write(queue_list)
        cmd.append("--queue")
        cmd.append(queue_file)

    # Spawn process
    active_process = subprocess.Popen(
        cmd, 
        cwd=work_dir,
        stdout=open(log_file, 'a', encoding='utf-8'), 
        stderr=subprocess.STDOUT, 
        text=True
    )
    
    def wait_and_rotate_log(p, lpath):
        p.wait()
        import time, shutil, datetime
        time.sleep(1)
        if os.path.exists(lpath):
            now_str = datetime.datetime.now().strftime("%Y-%m-%d %H_%M_%S")
            dst = os.path.join(os.path.dirname(lpath), f"crawl_{now_str}.log")
            try:
                shutil.copy2(lpath, dst)
                with open(lpath, 'w', encoding='utf-8') as f:
                    f.write(f"\n[시스템] 크롤링 작업이 성공적으로 종료되었습니다.\n전체 상세 로그는 {os.path.basename(dst)} 파일로 백업 보관되었습니다.\n")
            except Exception:
                pass

    import threading
    threading.Thread(target=wait_and_rotate_log, args=(active_process, log_file), daemon=True).start()
    
    return JSONResponse({"status": "success", "message": "크롤링이 시작되었습니다."})

@router.post("/resume")
async def resume_crawl():
    # Signal start.py to resume if it is waiting for manual login
    work_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    signal_file = os.path.join(work_dir, 'data', 'resume.sig')
    with open(signal_file, 'w', encoding='utf-8') as f:
        f.write("RESUME")
    return JSONResponse({"status": "success", "message": "크롤링 재개 신호가 전송되었습니다."})

@router.post("/kill")
async def kill_crawl():
    global active_process
    if active_process is not None and active_process.poll() is None:
        active_process.terminate()
        active_process = None
        
    try:
        if active_process is not None and active_process.poll() is None:
            active_process.terminate()
            active_process = None
            
            work_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
            log_file = os.path.join(work_dir, 'data', 'logs', 'current_crawl.log')
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write("\n[시스템] 사용자 요청으로 크롤링 프로세스가 강제 종료되었습니다.\n")
                
            return JSONResponse({"status": "success", "message": "크롤링 프로세스가 강제로 종료되었습니다. (진행 중이던 데이터는 저장되지 않습니다)"})
        else:
            return JSONResponse({"status": "error", "message": "현재 실행 중인 크롤링 프로세스가 없습니다."})
    except Exception as e:
        logger.LoggerFactory.logbot.error(f"강제 종료 실패: {e}")
        return {"status": "error", "message": f"오류: {e}"}

@router.post("/export/excel")
async def export_excel():
    import database, export
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
    import database, export
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
    work_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    log_file = os.path.join(work_dir, 'data', 'logs', 'current_crawl.log')
    
    try:
        if not os.path.exists(log_file):
            await websocket.send_text("로그 파일을 대기 중입니다...\n")
            while not os.path.exists(log_file):
                await asyncio.sleep(1)
                
        # Initial read
        if os.path.exists(log_file):
            with open(log_file, 'r', encoding='utf-8') as f:
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
                with open(log_file, 'r', encoding='utf-8') as f:
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
