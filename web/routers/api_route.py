from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.security import APIKeyHeader
from sqlalchemy import create_engine
import settings.settings as settings
from services import data_service
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

@router.post("/crawl/enqueue")
async def enqueue_crawl(request: Request, _: str = Depends(_require_api_key)):
    """신고번호를 받아 크롤링 큐에 추가 (안드로이드 알림 연동용)"""
    import sys
    import os
    from services.crawl_manager import crawl_manager

    body = await request.json()
    report_number = body.get("report_number")
    if not report_number:
        raise HTTPException(status_code=400, detail="report_number is required")

    if crawl_manager.is_crawling():
        return {"status": "busy", "message": "크롤링이 이미 실행 중입니다. 잠시 후 다시 시도해 주세요."}

    is_frozen = getattr(sys, 'frozen', False)
    if is_frozen:
        cmd = [sys.executable, "--mode", "crawl"]
    else:
        cmd = [sys.executable, "-u", "start.py"]

    queue_file = os.path.join(settings.datapath, 'mobile_queue.txt')
    with open(queue_file, 'w', encoding='utf-8') as qf:
        qf.write(str(report_number))

    cmd.append("--queue")
    cmd.append(queue_file)

    log_dir = os.path.join(settings.datapath, 'logs')
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, 'current_crawl.log')
    work_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))

    if crawl_manager.start_crawl(cmd, cwd=work_dir, log_file=log_file):
        return {"status": "success", "message": f"Report {report_number} has been enqueued and crawling started."}
    else:
        return {"status": "error", "message": "크롤링 프로세스를 시작하지 못했습니다."}

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


@router.get("/app/config")
async def get_app_config(_: str = Depends(_require_api_key)):
    """모바일 앱 설정 및 버전 정보"""
    return {
        "status": "success",
        "data": {
            "app_name": "나만의 안전신문고",
            "version": "1.0.0",
            "support_email": "support@example.com",
            "exclude_withdraw": settings.exclude_withdraw
        }
    }
