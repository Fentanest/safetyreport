"""DB 백업(다운로드) / 복원(업로드) 라우터."""
import os
from datetime import datetime

from fastapi import APIRouter, Request, UploadFile, File, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask

from core.utils.templating import templates
from services import db_backup


router = APIRouter()


@router.get("/backup")
async def view_backup(request: Request):
    return templates.TemplateResponse(request, "backup.html", {
        "title": "데이터 백업/복원",
    })


@router.get("/backup/download")
async def download_db():
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
    """DB 파일 업로드 → 서버/모바일 자동 감지 후 복원."""
    if not file.filename or not file.filename.lower().endswith(".db"):
        raise HTTPException(status_code=400, detail=".db 파일만 업로드 가능합니다.")

    # 임시 파일에 저장
    tmp_path = await _save_upload_to_tmp(file)
    try:
        kind = db_backup.detect_db_kind(tmp_path)
        if kind == "server":
            backup, count = db_backup.restore_from_server_db(tmp_path)
            return JSONResponse({
                "status": "ok",
                "kind": "server",
                "imported": count,
                "backup": os.path.basename(backup) if backup else "",
                "message": f"서버 형식 DB로 복원 완료. ({count}건)",
            })
        elif kind == "mobile":
            backup, count = db_backup.restore_from_mobile_db(tmp_path)
            return JSONResponse({
                "status": "ok",
                "kind": "mobile",
                "imported": count,
                "backup": os.path.basename(backup) if backup else "",
                "message": f"모바일 DB → 서버 형식 변환 복원 완료. ({count}건)",
            })
        else:
            raise HTTPException(
                status_code=400,
                detail="알 수 없는 DB 형식 — 서버(mysafety*) 또는 모바일(reports+sync_meta) DB만 허용됩니다.",
            )
    finally:
        _safe_unlink(tmp_path)


async def _save_upload_to_tmp(file: UploadFile) -> str:
    import tempfile
    fd, tmp_path = tempfile.mkstemp(suffix=".db", prefix="safetyreport_upload_")
    try:
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
    except Exception:
        _safe_unlink(tmp_path)
        raise
    return tmp_path
