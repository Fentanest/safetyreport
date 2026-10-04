"""DB 백업(다운로드) / 복원(업로드) 라우터."""
import os
from datetime import datetime

from fastapi import APIRouter, Request, UploadFile, File, HTTPException
from starlette.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask

from core.utils.templating import templates
from services import db_backup
from web.db_upload import refused_response, staged_db_upload


router = APIRouter()


@router.get("/backup")
def view_backup(request: Request):
    return templates.TemplateResponse(request, "backup.html", {
        "title": "데이터 백업/복원",
    })


@router.get("/backup/download")
def download_db():
    """WAL/SHM이 정리된 단일 .db 파일 다운로드. 다운로드 후 임시 파일 자동 삭제."""
    try:
        tmp_path = db_backup.export_clean_db()
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB 추출 실패: {e}")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return FileResponse(
        tmp_path,
        filename=f"safetyreport_{ts}.db",
        media_type="application/octet-stream",
        background=BackgroundTask(_safe_unlink, tmp_path),
    )


def _safe_unlink(path: str):
    try:
        os.remove(path)
    except Exception:
        pass


@router.post("/backup/upload")
async def upload_db(file: UploadFile = File(...)):
    """DB 파일 업로드 → 서버/모바일 자동 감지 후 복원(services.db_backup.restore_uploaded_db)."""
    from core.storage.exchange import RestoreRefused

    async with staged_db_upload(file) as tmp_path:
        try:
            result = await run_in_threadpool(db_backup.restore_uploaded_db, tmp_path)
        except RestoreRefused as exc:
            return refused_response(exc)
    message = (f"서버 형식 DB로 복원 완료. ({result.imported}건)" if result.kind == "server"
               else f"모바일 DB → 서버 형식 변환 복원 완료. ({result.imported}건)")
    return JSONResponse({"status": "ok", "kind": result.kind, "imported": result.imported,
                         "backup": result.backup_name, "message": message})
