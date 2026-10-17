"""원본 보기 읽기·다시 쓰기(2026-10-17 명세 4절, 웹·모바일 공용).

상세 원본 표(`mysafety_report_detail_raw`)에서 행을 읽어 zlib 해제·sha256 검증을
거친 뒤 돌려준다. 검증에 실패한 행은 "손상"으로 보고 쓰지 않는다(조용히 버리지
않고 대체 진단 기록 + 경고 로그를 남긴다 — `services/db_optimize._stored_result`
와 같은 규칙). `services/db_optimize.py` 는 건드리지 않는다.
"""
from __future__ import annotations

import json

from sqlalchemy import select

from core.database import models
from core.utils import raw_detail
from core.utils.fallback import note_fallback
from core.utils import logger


class RawDetailMissing(LookupError):
    """원본 행이 없음(ID 검증 실패 포함 — 경로 이탈 시도를 구분해 알리지 않는다)."""


class RawDetailCorrupt(ValueError):
    """행은 있으나 zlib 해제·sha256·JSON 복원에 실패(손상)."""


def read(engine, report_id: str) -> dict:
    """원본 한 건 읽기 → `{"id", "fetched_at", "source", "raw_sha256", "json"}`.

    `json` 은 신고자 키 5개가 이미 빠진 저장 원본 그대로(dict)다. 없으면
    `RawDetailMissing`, 손상이면 `RawDetailCorrupt` 를 올린다.
    """
    if not raw_detail.is_safe_report_id(report_id):
        raise RawDetailMissing(f"원본 없음: {report_id!r}")
    with engine.connect() as conn:
        row = conn.execute(
            select(models.report_detail_raw_table).where(
                models.report_detail_raw_table.c.ID == report_id)
        ).mappings().first()
    if row is None:
        raise RawDetailMissing(f"원본 없음: {report_id}")
    try:
        payload = raw_detail.verify(row["raw_zlib"], row["raw_sha256"])
    except ValueError as exc:
        note_fallback("raw_detail_view.corrupt", exc)
        logger.LoggerFactory.logbot.warning(f"[raw_detail] {report_id} 원본 손상(sha 불일치)")
        raise RawDetailCorrupt("상세 원본 sha256 불일치(손상)") from exc
    try:
        envelope = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        note_fallback("raw_detail_view.decode", exc)
        logger.LoggerFactory.logbot.warning(f"[raw_detail] {report_id} 원본 복원 실패")
        raise RawDetailCorrupt("상세 원본 복원 실패(손상)") from exc
    if not isinstance(envelope, dict):
        shape_exc = ValueError(f"예상 밖 원본 형식: {type(envelope).__name__}")
        note_fallback("raw_detail_view.shape", shape_exc)
        logger.LoggerFactory.logbot.warning(f"[raw_detail] {report_id} 원본 형식 오류")
        raise RawDetailCorrupt("상세 원본 형식 오류(손상)") from shape_exc
    return {
        "id": report_id,
        "fetched_at": row["fetched_at"],
        "source": row["source"],
        "raw_sha256": row["raw_sha256"],
        "json": envelope,
    }


def rewrite_file(engine, report_id: str) -> str:
    """DB 원본으로 `<datapath>/raw_details/<ID>.json` 을 다시 쓴다(관리자 동작).

    성공하면 쓴 경로, 파일 쓰기 실패면 `RuntimeError`. 없음·손상은
    `read` 와 같은 예외를 그대로 올린다.
    """
    data = read(engine, report_id)
    path = raw_detail.write_raw_file(report_id, data["json"])
    if path is None:
        raise RuntimeError("원본 파일 쓰기 실패")
    return path
