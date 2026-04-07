from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.security import APIKeyHeader
from sqlalchemy import create_engine
import settings.settings as settings
from services import data_service
from services.ws_manager import ws_manager
from core.database import database
import pandas as pd

router = APIRouter(prefix="/api/v1")
engine = create_engine(f'sqlite:///{settings.db_path}', connect_args={"check_same_thread": False})

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

def _require_api_key(api_key: str = Depends(_api_key_header)):
    if not api_key or not database.validate_api_key(engine, api_key):
        raise HTTPException(status_code=401, detail="유효하지 않은 API 키입니다.")
    return api_key

@router.get("/summary")
async def get_summary(request: Request, _: str = Depends(_require_api_key)):
    """모바일용 대시보드 요약 정보"""
    try:
        stats = data_service.get_dashboard_stats(engine)
        return {"status": "success", "data": stats}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/reports/{category}")
async def get_reports(category: str, _: str = Depends(_require_api_key)):
    """카테고리별 신고 리스트 (traffic, other, duplicates)"""
    try:
        if category == "traffic":
            records = data_service.get_traffic_records(engine)
        elif category == "other":
            records = data_service.get_other_records(engine)
        elif category == "duplicates":
            records = data_service.get_duplicate_records(engine)
        else:
            raise HTTPException(status_code=400, detail="Invalid category")
        return {"status": "success", "category": category, "count": len(records), "data": records}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/stats")
async def get_stats(_: str = Depends(_require_api_key)):
    """기관별/담당자별 처리 통계"""
    try:
        stats = data_service.get_agency_stats(engine)
        return {"status": "success", "data": stats}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/watchlist")
async def get_watchlist(_: str = Depends(_require_api_key)):
    """감시 목록 조회"""
    try:
        items = data_service.get_all_watchlist(engine)
        return {"status": "success", "count": len(items), "data": items}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/watchlist")
async def update_watchlist(request: Request, _: str = Depends(_require_api_key)):
    """감시 목록 추가/제거. body: {report_numbers: [...], action: 'add'|'remove'}"""
    body = await request.json()
    report_numbers = body.get("report_numbers", [])
    action = body.get("action", "remove")
    if not report_numbers:
        raise HTTPException(status_code=400, detail="report_numbers is required")
    try:
        count = data_service.update_watchlist_status(
            engine, report_numbers, 'Y' if action == 'add' else 'N'
        )
        return {"status": "success", "updated": count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

import sys as _sys
import os as _os
import datetime as _dt
import shutil as _sh
import threading as _threading

def _get_work_dir():
    return _os.path.abspath(_os.path.join(_os.path.dirname(__file__), '..', '..'))

def _rotate_log(log_file: str):
    """현재 로그를 타임스탬프 파일로 백업."""
    if _os.path.exists(log_file):
        try:
            now_str = _dt.datetime.now().strftime("%Y-%m-%d %H_%M_%S")
            dst = _os.path.join(_os.path.dirname(log_file), f"crawl_{now_str}.log")
            _sh.copy2(log_file, dst)
        except Exception:
            pass

def _run_after_crawl(proc, log_file: str):
    """크롤링 프로세스 완료 후 공통 처리: 로그 회전 → WS 브로드캐스트 → 대기 큐 자동 실행."""
    from services.crawl_manager import crawl_manager
    import time
    if proc:
        proc.wait()
    crawl_manager.clear_process()
    time.sleep(1)

    # 로그 완료 메시지 + 백업
    if _os.path.exists(log_file):
        try:
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write("\n[시스템] 크롤링 작업이 완료되었습니다.\n")
            _rotate_log(log_file)
        except Exception:
            pass

    # WS: crawl_finished + crawl_changes
    try:
        done = data_service.get_and_clear_crawl_done()
        changed_count = done["changed_count"] if done else 0
        ws_manager.broadcast_from_thread("crawl_finished", {"changed_count": changed_count})
    except Exception:
        pass
    try:
        changes = data_service.peek_crawl_changes()
        if changes:
            ws_manager.broadcast_from_thread("crawl_changes", {"changes": changes})
    except Exception:
        pass

    # 대기 큐에 쌓인 신고번호가 있으면 자동으로 다음 크롤링 시작
    pending = crawl_manager.pop_pending()
    if pending:
        _launch_pending_crawl(pending)


def _launch_pending_crawl(pending: list):
    """대기 큐의 신고번호로 새 크롤링을 즉시 시작."""
    from services.crawl_manager import crawl_manager
    if not pending:
        return

    is_frozen = getattr(_sys, 'frozen', False)
    cmd = [_sys.executable, "--mode", "crawl"] if is_frozen else [_sys.executable, "-u", "start.py"]

    queue_file = _os.path.join(settings.datapath, 'pending_queue.txt')
    with open(queue_file, 'w', encoding='utf-8') as f:
        f.write('\n'.join(str(r) for r in pending))
    cmd.extend(["--queue", queue_file])

    log_dir = _os.path.join(settings.datapath, 'logs')
    log_file = _os.path.join(log_dir, 'current_crawl.log')
    _rotate_log(log_file)

    with open(log_file, 'w', encoding='utf-8') as f:
        f.write(f"=== [대기 큐 자동 시작] 신고번호 {len(pending)}건 ===\n")
        f.write('\n'.join(f"  - {r}" for r in pending) + '\n')

    if crawl_manager.start_crawl(cmd, cwd=_get_work_dir(), log_file=log_file):
        import settings.settings as _s2
        ws_manager.broadcast_from_thread("crawl_started", {
            "source": "pending_queue",
            "count": len(pending),
            "crawl_mode": _s2.crawl_mode,
            "crawl_type": _s2.crawl_type,
        })
        proc = crawl_manager.get_process()
        if proc:
            _threading.Thread(
                target=_run_after_crawl, args=(proc, log_file), daemon=True
            ).start()


@router.post("/crawl/enqueue")
async def enqueue_crawl(request: Request, _: str = Depends(_require_api_key)):
    """신고번호를 받아 크롤링 큐에 추가 (안드로이드 알림 연동용).
    크롤링 중이면 대기 큐에 쌓아두고 완료 후 자동 실행."""
    from services.crawl_manager import crawl_manager

    body = await request.json()
    report_number = body.get("report_number")
    if not report_number:
        raise HTTPException(status_code=400, detail="report_number is required")

    # 크롤링 중이면 대기 큐에 추가
    if crawl_manager.is_crawling():
        queue_size = crawl_manager.append_to_pending(str(report_number))
        return {
            "status": "queued",
            "message": f"크롤링 완료 후 자동 실행됩니다. (대기 중: {queue_size}건)",
        }

    is_frozen = getattr(_sys, 'frozen', False)
    cmd = [_sys.executable, "--mode", "crawl"] if is_frozen else [_sys.executable, "-u", "start.py"]

    queue_file = _os.path.join(settings.datapath, 'mobile_queue.txt')
    with open(queue_file, 'w', encoding='utf-8') as qf:
        qf.write(str(report_number))
    cmd.extend(["--queue", queue_file])

    log_dir = _os.path.join(settings.datapath, 'logs')
    _os.makedirs(log_dir, exist_ok=True)
    log_file = _os.path.join(log_dir, 'current_crawl.log')
    _rotate_log(log_file)

    with open(log_file, 'w', encoding='utf-8') as f:
        f.write(f"=== [모바일에서 시작된 크롤링] - 신고번호: {report_number} ===\n")

    if crawl_manager.start_crawl(cmd, cwd=_get_work_dir(), log_file=log_file):
        proc = crawl_manager.get_process()
        try:
            import asyncio as _aio
            import settings.settings as _s
            loop = _aio.get_event_loop()
            if loop.is_running():
                loop.create_task(ws_manager.broadcast("crawl_started", {
                    "source": "mobile_enqueue",
                    "report_number": report_number,
                    "crawl_mode": _s.crawl_mode,
                    "crawl_type": _s.crawl_type,
                }))
        except Exception:
            pass
        if proc:
            _threading.Thread(target=_run_after_crawl, args=(proc, log_file), daemon=True).start()
        return {"status": "success", "message": f"신고번호 {report_number} 크롤링이 시작되었습니다."}
    else:
        return {"status": "error", "message": "크롤링 프로세스를 시작하지 못했습니다."}

@router.get("/crawl/results")
async def get_crawl_results(_: str = Depends(_require_api_key)):
    """크롤링으로 변경된 신고 목록 조회 및 초기화 (모바일 개별 알림용)"""
    try:
        changes = data_service.get_and_clear_crawl_changes()
        return {"status": "success", "count": len(changes), "data": changes}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/crawl/status")
async def get_crawl_status(_: str = Depends(_require_api_key)):
    """크롤링 실행 중 여부 확인"""
    from services.crawl_manager import crawl_manager
    return {"status": "success", "running": crawl_manager.is_crawling()}


@router.get("/crawl/done")
async def get_crawl_done(_: str = Depends(_require_api_key)):
    """크롤링 완료 여부 및 변경 건수 조회 (확인 후 자동 삭제)"""
    try:
        done = data_service.get_and_clear_crawl_done()
        if done is None:
            return {"status": "success", "done": False}
        return {"status": "success", "done": True, "timestamp": done["timestamp"], "changed_count": done["changed_count"]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/crawl/config")
async def get_crawl_config(_: str = Depends(_require_api_key)):
    """크롤링 설정 조회 (저장된 crawl_type, max_empty_pages)"""
    import settings.settings as s
    s._instance.load()
    return {
        "status": "success",
        "data": {
            "crawl_type": s.crawl_type,
            "crawl_mode": s.crawl_mode,
            "max_empty_pages": s.max_empty_pages,
        }
    }


@router.post("/crawl/start")
async def mobile_start_crawl(request: Request, _: str = Depends(_require_api_key)):
    """모바일에서 크롤링 시작. queue_list가 있고 이미 실행 중이면 대기 큐에 추가."""
    from services.crawl_manager import crawl_manager

    body = await request.json()
    login_mode = body.get("login_mode", "member")
    crawl_type = body.get("crawl_type", "api")
    crawl_mode = body.get("crawl_mode", "full")
    max_empty_pages = int(body.get("max_empty_pages", 3))
    queue_list = body.get("queue_list", "").strip()

    # 이미 크롤링 중인 경우
    if crawl_manager.is_crawling():
        # queue_list(다중 선택 신고번호)가 있으면 대기 큐에 추가
        if queue_list:
            for rnum in queue_list.splitlines():
                rnum = rnum.strip()
                if rnum:
                    crawl_manager.append_to_pending(rnum)
            return {
                "status": "queued",
                "message": f"크롤링 완료 후 자동 실행됩니다. (대기 중: {crawl_manager.pending_count()}건)",
            }
        raise HTTPException(status_code=409, detail="크롤링이 이미 실행 중입니다.")

    import settings.settings as app_settings
    app_settings._instance.update_config('SETTINGS', 'max_empty_pages', max_empty_pages)
    app_settings._instance.update_config('Crawler', 'crawl_type', crawl_type)
    save_mode = 'full' if crawl_mode == 'reset' else crawl_mode
    app_settings._instance.update_config('SETTINGS', 'crawl_mode', save_mode)
    app_settings._instance.save()

    is_frozen = getattr(_sys, 'frozen', False)
    cmd = [_sys.executable, "--mode", "crawl"] if is_frozen else [_sys.executable, "-u", "start.py"]

    if login_mode == "nonmember":
        cmd.append("--nonmember")
    if crawl_mode == "min":
        cmd.append("--min")
    elif crawl_mode == "reset":
        cmd.append("--reset")

    if queue_list:
        queue_file = _os.path.join(settings.datapath, 'mobile_queue.txt')
        with open(queue_file, 'w', encoding='utf-8') as qf:
            qf.write(queue_list)
        cmd.extend(["--queue", queue_file])

    log_dir = _os.path.join(settings.datapath, 'logs')
    _os.makedirs(log_dir, exist_ok=True)
    log_file = _os.path.join(log_dir, 'current_crawl.log')
    _rotate_log(log_file)

    with open(log_file, 'w', encoding='utf-8') as f:
        f.write("=== [모바일에서 시작된 크롤링] ===\n")

    if not crawl_manager.start_crawl(cmd, cwd=_get_work_dir(), log_file=log_file):
        raise HTTPException(status_code=500, detail="크롤링 프로세스를 시작하지 못했습니다.")

    proc = crawl_manager.get_process()
    try:
        import asyncio as _aio2
        loop3 = _aio2.get_event_loop()
        if loop3.is_running():
            loop3.create_task(ws_manager.broadcast("crawl_started", {
                "source": "mobile_start",
                "login_mode": login_mode,
                "crawl_mode": crawl_mode,
                "crawl_type": crawl_type,
            }))
    except Exception:
        pass

    if proc:
        _threading.Thread(target=_run_after_crawl, args=(proc, log_file), daemon=True).start()

    return {"status": "success", "message": "크롤링이 시작되었습니다."}


@router.post("/crawl/kill")
async def mobile_kill_crawl(_: str = Depends(_require_api_key)):
    """모바일에서 크롤링 강제 중지"""
    import os
    from services.crawl_manager import crawl_manager
    import settings.settings as app_settings
    if not crawl_manager.is_crawling():
        raise HTTPException(status_code=409, detail="실행 중인 크롤링이 없습니다.")
    try:
        crawl_manager.stop_crawl()
        log_file = os.path.join(app_settings.datapath, 'logs', 'current_crawl.log')
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write("\n[시스템] 사용자 요청으로 크롤링 프로세스가 강제 종료되었습니다.\n")
        return {"status": "success", "message": "크롤링이 강제 중지되었습니다."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/crawl/resume")
async def mobile_resume_crawl(_: str = Depends(_require_api_key)):
    """모바일에서 크롤링 재개 (비회원 수동 로그인 완료 신호)"""
    import os
    import settings.settings as app_settings
    signal_file = os.path.join(app_settings.datapath, 'resume.sig')
    with open(signal_file, 'w', encoding='utf-8') as f:
        f.write("RESUME")
    return {"status": "success", "message": "크롤링 재개 신호가 전송되었습니다."}


@router.get("/files")
async def list_files(path: str = "", _: str = Depends(_require_api_key)):
    """서버 파일 브라우저 — logs / results 폴더만 허용"""
    import os
    from datetime import datetime as dt

    ALLOWED_ROOTS = {'logs', 'results'}
    base = os.path.abspath(settings.datapath)

    # 루트 요청: 허용된 폴더만 반환
    if not path:
        items = []
        for name in sorted(ALLOWED_ROOTS):
            full = os.path.join(base, name)
            if os.path.exists(full):
                items.append({
                    "name": name, "path": name, "is_dir": True, "size": None,
                    "modified": dt.fromtimestamp(os.path.getmtime(full)).strftime("%Y-%m-%d %H:%M"),
                })
        return {"status": "success", "current_path": "/", "data": items}

    # 첫 번째 경로 컴포넌트가 허용된 폴더인지 확인
    first = path.replace('\\', '/').split('/')[0]
    if first not in ALLOWED_ROOTS:
        raise HTTPException(status_code=403, detail="접근 불가")

    target = os.path.normpath(os.path.join(base, path))
    if not target.startswith(base):
        raise HTTPException(status_code=403, detail="접근 불가")
    if not os.path.exists(target):
        raise HTTPException(status_code=404, detail="경로를 찾을 수 없습니다")
    if not os.path.isdir(target):
        raise HTTPException(status_code=400, detail="파일 경로는 지원하지 않습니다")

    try:
        entries = sorted(
            os.listdir(target),
            key=lambda n: (not os.path.isdir(os.path.join(target, n)), n.lower())
        )
        items = []
        for name in entries:
            full = os.path.join(target, name)
            rel = os.path.relpath(full, base)
            is_dir = os.path.isdir(full)
            items.append({
                "name": name, "path": rel, "is_dir": is_dir,
                "size": None if is_dir else os.path.getsize(full),
                "modified": dt.fromtimestamp(os.path.getmtime(full)).strftime("%Y-%m-%d %H:%M"),
            })
    except PermissionError:
        raise HTTPException(status_code=403, detail="권한 없음")
    return {"status": "success", "current_path": path, "data": items}


@router.get("/files/download")
async def download_file(path: str = "", _: str = Depends(_require_api_key)):
    """서버 파일 다운로드 — logs / results 폴더의 파일만 허용. X-API-Key 헤더 인증."""
    from fastapi.responses import FileResponse
    import os

    ALLOWED_ROOTS = {'logs', 'results'}
    base = os.path.abspath(settings.datapath)

    first = path.replace('\\', '/').split('/')[0]
    if first not in ALLOWED_ROOTS:
        raise HTTPException(status_code=403, detail="접근 불가")

    target = os.path.normpath(os.path.join(base, path))
    if not target.startswith(base):
        raise HTTPException(status_code=403, detail="접근 불가")
    if not os.path.exists(target):
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다")
    if os.path.isdir(target):
        raise HTTPException(status_code=400, detail="디렉토리는 다운로드할 수 없습니다")

    return FileResponse(
        target,
        filename=os.path.basename(target),
        media_type="application/octet-stream"
    )


@router.get("/app/config")
async def get_app_config(_: str = Depends(_require_api_key)):
    """모바일 앱 설정 및 버전 정보"""
    import settings.settings as s
    s._instance.load()
    return {
        "status": "success",
        "data": {
            "app_name": "나만의 안전신문고",
            "version": "1.0.0",
            "support_email": "support@example.com",
            "exclude_withdraw": s.exclude_withdraw,
            "normalize_police": s.normalize_police,
            "auto_export_excel": s.config.getboolean('SETTINGS', 'auto_export_excel', fallback=True),
            "auto_export_sheet": s.config.getboolean('SETTINGS', 'auto_export_sheet', fallback=True),
        }
    }


@router.post("/settings")
async def update_settings(request: Request, _: str = Depends(_require_api_key)):
    """기타 데이터 필터 세팅 저장 (normalize_police, exclude_withdraw)"""
    import settings.settings as app_settings
    body = await request.json()
    if "normalize_police" in body:
        app_settings._instance.update_config('SETTINGS', 'normalize_police', body["normalize_police"])
    if "exclude_withdraw" in body:
        app_settings._instance.update_config('SETTINGS', 'exclude_withdraw', body["exclude_withdraw"])
    if "crawl_type" in body and body["crawl_type"] in ("api", "web"):
        app_settings._instance.update_config('Crawler', 'crawl_type', body["crawl_type"])
    if "auto_export_excel" in body:
        app_settings._instance.update_config('SETTINGS', 'auto_export_excel', body["auto_export_excel"])
    if "auto_export_sheet" in body:
        app_settings._instance.update_config('SETTINGS', 'auto_export_sheet', body["auto_export_sheet"])
    app_settings._instance.save()
    return {"status": "success"}
