"""원본 보기 웹 라우트(2026-10-17 명세 4절).

관리자 세션 인증은 전역 `auth_middleware`(세션 `admin_logged_in`, 비로그인이면
302/`session_expired` 401)가 맡는다. 이 라우터는 `/api/v1/` 아래가 아니라 그
보호를 그대로 받는다(라우터 자체에서 인증을 재검사하지 않는다).

- `GET /reports/{report_id}/raw-detail` — 저장 원본 그대로
  `{id, fetched_at, source, raw_sha256, json}`. 없음 404
  `{error: "raw_detail_missing"}`, 손상 409 `{error: "raw_detail_corrupt"}`.
- `POST /reports/{report_id}/raw-detail/rewrite` — DB 원본으로
  `<datapath>/raw_details/<ID>.json` 재생성(관리자 동작). JSON POST 라
  `core/utils/csrf.py verify_json_post` 로 CSRF 를 확인한다(전역 버전·CSRF
  미들웨어와 같은 방식 — `session-requests.js` 가 토큰을 자동으로 붙인다).
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from core.database.engine import get_engine
from core.utils import csrf
from services import raw_detail_service

router = APIRouter()
engine = get_engine()

_MISSING = {"error": "raw_detail_missing"}
_CORRUPT = {"error": "raw_detail_corrupt"}


@router.get("/reports/{report_id}/raw-detail")
def get_raw_detail(report_id: str):
    try:
        return raw_detail_service.read(engine, report_id)
    except raw_detail_service.RawDetailMissing:
        return JSONResponse(_MISSING, status_code=404)
    except raw_detail_service.RawDetailCorrupt:
        return JSONResponse(_CORRUPT, status_code=409)


@router.post("/reports/{report_id}/raw-detail/rewrite")
def rewrite_raw_detail(report_id: str, request: Request):
    reason = csrf.verify_json_post(request)
    if reason:
        return JSONResponse({"error": "csrf_failed", "reason": reason}, status_code=403)
    try:
        path = raw_detail_service.rewrite_file(engine, report_id)
    except raw_detail_service.RawDetailMissing:
        return JSONResponse(_MISSING, status_code=404)
    except raw_detail_service.RawDetailCorrupt:
        return JSONResponse(_CORRUPT, status_code=409)
    except RuntimeError as exc:
        return JSONResponse({"error": "raw_detail_rewrite_failed", "detail": str(exc)},
                            status_code=500)
    import os

    return {"status": "success", "id": report_id, "filename": os.path.basename(path)}
