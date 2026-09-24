"""DB 백업/복원 유틸.

- export_clean_db(): WAL 체크포인트 후 .db 단일 파일을 임시 경로에 복사 (-shm/-wal 없이).
- detect_db_kind(): 업로드된 .db 파일이 서버 형식인지 모바일 형식인지 판별.
- restore_from_server_db(): 서버 형식 DB로 복원 (임시 사본 검사 → 백업 → 원자적 교체).
- restore_from_mobile_db(): 모바일 DB → 서버 형식 변환 복원. 변환 규칙은 core/storage/exchange.py.
"""
from __future__ import annotations
import os
import re
import sqlite3
import tempfile
from typing import Literal, Tuple

import settings.settings as settings
from core.utils import logger


DbKind = Literal["server", "mobile", "unknown"]
_BACKFILL_STATE_KEY = "map_backfill_state"


def _wal_checkpoint(db_path: str) -> None:
    """WAL/SHM을 메인 DB로 머지하고 WAL 파일을 잘라낸다."""
    try:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.LoggerFactory.logbot.warning(f"WAL checkpoint 실패: {e}")


def export_clean_db() -> str:
    """현재 DB를 정리해 임시 파일로 복사. 호출자가 사용 후 삭제 책임.

    sqlite3.Connection.backup()을 사용해 일관성 있는 단일 파일 생성.
    -shm/-wal이 없는 깨끗한 .db 파일 반환.
    """
    src = settings.db_path
    if not os.path.exists(src):
        raise FileNotFoundError(f"DB 파일 없음: {src}")

    # 1) 기존 WAL을 main DB로 머지 (필수 — backup() 만으로는 WAL 잔존 가능성)
    _wal_checkpoint(src)

    # 2) sqlite3.backup으로 일관성 있는 단일 파일 작성
    fd, tmp_path = tempfile.mkstemp(suffix=".db", prefix="safetyreport_export_")
    os.close(fd)

    src_conn = sqlite3.connect(src)
    dst_conn = sqlite3.connect(tmp_path)
    try:
        src_conn.backup(dst_conn)
    finally:
        dst_conn.close()
        src_conn.close()

    return tmp_path


def detect_db_kind(db_path: str) -> DbKind:
    """업로드된 DB 파일의 종류 판별.

    - 서버: mysafety, mysafetymerge_traffic 등 다중 테이블 보유
    - 모바일: 단일 reports 테이블 + sync_meta + category 컬럼
    """
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            cur = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
            tables = {row[0] for row in cur.fetchall()}
            if "reports" in tables and "sync_meta" in tables and "mysafety" not in tables:
                # 모바일: reports 테이블에 category 컬럼 존재 여부 확인
                cur = conn.execute("PRAGMA table_info(reports)")
                cols = {row[1] for row in cur.fetchall()}
                if "category" in cols:
                    return "mobile"
            if "mysafety" in tables and any(
                t.startswith("mysafetymerge_") for t in tables
            ):
                return "server"
            return "unknown"
        finally:
            conn.close()
    except Exception as e:
        logger.LoggerFactory.logbot.warning(f"DB 종류 판별 실패: {e}")
        return "unknown"


def restore_from_server_db(uploaded_path: str) -> Tuple[str, int]:
    """서버 형식 DB 파일로 교체. 임시 사본에서 업그레이드·무결성 검사 후 원자적으로 바꾼다(core/storage/exchange.py)."""
    from core.storage import exchange

    return exchange.restore(uploaded_path, "server")


def restore_from_mobile_db(uploaded_path: str) -> Tuple[str, int]:
    """모바일 DB → 서버 형식 변환 복원. 현재 DB 사본 위에 변환해 서버 전용 데이터(관리자·API 키·캐시·변경 기록)를 보존한다."""
    from core.storage import exchange

    backup, count = exchange.restore(uploaded_path, "mobile")
    try:
        from core.database.engine import get_engine
        from services import geocode_service
        geocode_service.ensure_map_backfill_started(get_engine(), batch_size=120)
    except Exception as exc:
        logger.LoggerFactory.logbot.warning(f"[geocode] 모바일 DB 복원 후 자동 백필 시작 실패: {exc}")
    return backup, count
