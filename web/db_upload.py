"""DB 업로드 공통 처리(EO R-12): 임시 저장·정리와 거절 응답. 웹(/backup/upload)과 API(/api/v1/settings/db/upload)가 같이 쓴다.

복원 절차 자체는 HTTP 와 무관한 services.db_backup.restore_uploaded_db 에 있다.
"""
from __future__ import annotations

import os
import tempfile
from contextlib import asynccontextmanager

from fastapi import HTTPException, UploadFile
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

CHUNK_BYTES = 1024 * 1024


def _remove(path: str) -> None:
    """임시 DB 와 SQLite 부속 파일(-wal·-shm·-journal: 형식·무결성 검사가 열면서 만든다)을 함께 지운다."""
    for candidate in (path, path + "-wal", path + "-shm", path + "-journal"):
        try:
            os.remove(candidate)
        except FileNotFoundError:
            pass


@asynccontextmanager
async def staged_db_upload(file: UploadFile):
    """.db 파일만 받아 임시 파일에 나눠 쓰고(디스크 쓰기는 이벤트 루프 밖, 기술일지 B-02) 경로를 준다.
    성공·실패·예외와 관계없이 끝나면 임시 파일과 부속 파일을 지운다(이전에는 -wal·-shm 이 남았다)."""
    if not file.filename or not file.filename.lower().endswith(".db"):
        raise HTTPException(status_code=400, detail=".db 파일만 업로드 가능합니다.")
    fd, tmp_path = tempfile.mkstemp(suffix=".db", prefix="safetyreport_upload_")
    try:
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = await file.read(CHUNK_BYTES)
                if not chunk:
                    break
                await run_in_threadpool(out.write, chunk)
        yield tmp_path
    finally:
        _remove(tmp_path)


def refused_response(exc) -> JSONResponse:
    """복원 거절(RestoreRefused 계열: 형식·손상·이전/미래 버전·크롤링 중 등) → 409. 아무것도 바뀌지 않았다."""
    return JSONResponse({"status": "error", "code": exc.code, "detail": str(exc), "message": str(exc)}, status_code=409)
