"""DB 백업/복원 유틸.

- export_clean_db(): WAL 체크포인트 후 .db 단일 파일을 임시 경로에 복사 (-shm/-wal 없이).
- detect_db_kind(): 업로드된 .db 파일이 서버 형식인지 모바일 형식인지 판별.
- restore_from_server_db(): 서버 형식 DB로 복원 (현재 DB 백업 후 교체).
- restore_from_mobile_db(): 모바일 단일 reports 테이블 → 서버 3개 merge 테이블로 변환 복원.
"""
from __future__ import annotations
import hashlib
import os
import re
import shutil
import sqlite3
import tempfile
from datetime import datetime
from typing import Literal, Tuple

import settings.settings as settings
from core.utils import logger


DbKind = Literal["server", "mobile", "unknown"]


def _load_mobile_raw_payloads(src_conn: sqlite3.Connection) -> dict[str, dict[str, object]]:
    payloads: dict[str, dict[str, object]] = {}

    try:
        rows = src_conn.execute(
            "SELECT ID, raw_content, raw_type, saved_at FROM report_raw"
        ).fetchall()
    except Exception:
        rows = []

    for row in rows:
        record_id = str(row["ID"] or "")
        if not record_id:
            continue
        payloads[record_id] = {
            "raw_content": row["raw_content"] or "",
            "raw_type": row["raw_type"] or "",
            "saved_at": row["saved_at"],
        }

    return payloads


def _load_mobile_sync_meta(src_conn: sqlite3.Connection) -> list[dict[str, str]]:
    rows = []
    try:
        fetched = src_conn.execute("SELECT key, value FROM sync_meta").fetchall()
    except Exception:
        fetched = []

    for row in fetched:
        key = str(row["key"] or "").strip()
        if not key:
            continue
        rows.append({
            "key": key,
            "value": str(row["value"] or ""),
        })

    return rows


def _normalize_duplicate_raw_content(raw_content) -> str:
    text = str(raw_content or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n+", "\n", text)
    return text.strip()


def _duplicate_payload_hash(raw_content: str) -> str:
    return hashlib.sha256(raw_content.encode("utf-8")).hexdigest()


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


def _backup_current_db() -> str:
    """현재 DB를 같은 디렉토리에 백업. 백업 경로 반환."""
    src = settings.db_path
    if not os.path.exists(src):
        return ""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = os.path.join(settings.datapath, "backups")
    os.makedirs(backup_dir, exist_ok=True)
    backup_path = os.path.join(backup_dir, f"data_before_restore_{ts}.db")
    _wal_checkpoint(src)
    shutil.copy2(src, backup_path)
    # WAL/SHM 잔여 파일도 함께 백업 (있으면)
    for ext in ("-wal", "-shm"):
        side = src + ext
        if os.path.exists(side):
            shutil.copy2(side, backup_path + ext)
    return backup_path


def _remove_sidecar_files(db_path: str) -> None:
    """SQLite의 -wal / -shm 파일 정리."""
    for ext in ("-wal", "-shm"):
        side = db_path + ext
        if os.path.exists(side):
            try:
                os.remove(side)
            except Exception as e:
                logger.LoggerFactory.logbot.warning(f"{side} 삭제 실패: {e}")


def restore_from_server_db(uploaded_path: str) -> Tuple[str, int]:
    """서버 형식 DB 파일로 현재 DB를 교체. (current_backup_path, imported_count) 반환."""
    from core.database import database
    from core.database.engine import create_sqlite_engine

    backup = _backup_current_db()
    dst = settings.db_path

    _remove_sidecar_files(dst)
    shutil.copy2(uploaded_path, dst)
    database.upgrade_schema(create_sqlite_engine(db_path=dst))

    # imported count: 검증용 — mysafety 행 수 반환
    try:
        conn = sqlite3.connect(dst)
        try:
            count = conn.execute("SELECT COUNT(*) FROM mysafety").fetchone()[0]
        finally:
            conn.close()
    except Exception:
        count = -1

    logger.LoggerFactory.logbot.info(
        f"서버 DB 복원 완료. 백업: {backup}, mysafety 행수: {count}"
    )
    return backup, count


def restore_from_mobile_db(uploaded_path: str) -> Tuple[str, int]:
    """모바일 단일 reports 테이블 → 서버 3개 merge 테이블로 변환 복원.

    importFromServerDb의 역방향 변환:
      reports.category == 'traffic' → mysafetymerge_traffic
      reports.category == 'parking' → mysafetymerge_parking
      reports.category == 'other'   → mysafetymerge_other
      sync_meta('watchlist') CSV → mysafety_watchlist 테이블

    또한 mysafety / mysafetydetail_* 테이블도 reports 데이터에서 채워 넣음
    (서버는 title/detail/merge 3중 구조, merge만 채우면 다른 흐름이 깨질 수 있음).

    주의:
    - 모바일 DB에는 admin_users / api_keys 같은 서버 전용 테이블이 없으므로
      현재 서버 DB 파일을 통째로 교체하면 관리자 계정과 API 키가 유실될 수 있다.
    - 따라서 현재 구현은 "크롤링 데이터 테이블만 교체"하고, 모바일에 없는 서버 전용
      테이블은 그대로 보존한다.
    """
    from core.database import database, models
    from core.database.engine import create_sqlite_engine

    backup = _backup_current_db()
    engine = create_sqlite_engine()
    database.upgrade_schema(engine)

    # 모바일 DB에서 reports 읽기
    src_conn = sqlite3.connect(f"file:{uploaded_path}?mode=ro", uri=True)
    src_conn.row_factory = sqlite3.Row
    rows = src_conn.execute("SELECT * FROM reports").fetchall()
    raw_payloads = _load_mobile_raw_payloads(src_conn)
    sync_meta_records = _load_mobile_sync_meta(src_conn)

    # 모바일 reports 컬럼 확인
    src_cols = {desc[0] for desc in src_conn.execute("SELECT * FROM reports LIMIT 0").description}

    # 서버 title 컬럼: ID 상태 신고번호 신고명 신고일 만족도조사여부 별점 별점사유 감시목록
    # 서버 detail 컬럼: ID 처리상태 차량번호 위반법규 범칙금_과태료 벌점 처리기관 담당자 답변일
    #                   발생일자 발생시각 위반장소 종결여부 신고내용 처리내용 지도 첨부사진 첨부파일

    title_records = []
    detail_records_by_cat = {"traffic": [], "parking": [], "other": []}
    raw_content_records = []

    for r in rows:
        rd = dict(r)
        category = (rd.get("category") or "other").strip().lower()
        if category not in detail_records_by_cat:
            category = "other"

        title_records.append({
            "ID": rd.get("ID", ""),
            "상태": rd.get("상태", ""),
            "신고번호": rd.get("신고번호", ""),
            "신고명": rd.get("신고명", ""),
            "신고일": rd.get("신고일", ""),
            "만족도조사여부": rd.get("만족도조사여부", ""),
            "별점": rd.get("별점") if "별점" in src_cols else None,
            "별점사유": rd.get("별점사유", "") if "별점사유" in src_cols else "",
            "감시목록": rd.get("감시목록", "N"),
        })

        detail_records_by_cat[category].append({
            "ID": rd.get("ID", ""),
            "처리상태": rd.get("처리상태", ""),
            "차량번호": rd.get("차량번호", ""),
            "위반법규": rd.get("위반법규", ""),
            "범칙금_과태료": rd.get("범칙금_과태료", ""),
            "벌점": rd.get("벌점", ""),
            "처리기관": rd.get("처리기관", ""),
            "담당자": rd.get("담당자", ""),
            "답변일": rd.get("답변일", ""),
            "발생일자": rd.get("발생일자", ""),
            "발생시각": rd.get("발생시각", ""),
            "위반장소": rd.get("위반장소", ""),
            "종결여부": rd.get("종결여부", "N"),
            "신고내용": rd.get("신고내용", ""),
            "처리내용": rd.get("처리내용", ""),
            "지도": rd.get("지도", ""),
            "첨부사진": rd.get("첨부사진", ""),
            "첨부파일": rd.get("첨부파일", ""),
            "synced_at": rd.get("synced_at"),
        })

        record_id = rd.get("ID", "")
        raw_payload = raw_payloads.get(record_id, {})
        raw_content = raw_payload.get("raw_content")
        if raw_content is None:
            raw_content = rd.get("raw_content", "")
        raw_content = str(raw_content or "")
        if raw_content:
            raw_content_records.append({
                "ID": record_id,
                "raw_content": raw_content,
                "raw_type": str(raw_payload.get("raw_type") or ""),
                "saved_at": raw_payload.get("saved_at", rd.get("synced_at")),
            })

    # 감시목록(sync_meta('watchlist') CSV) → mysafety_watchlist
    watchlist_nums = []
    watchlist_found = False
    try:
        wm = src_conn.execute(
            "SELECT value FROM sync_meta WHERE key = 'watchlist'"
        ).fetchone()
        if wm is not None:
            watchlist_found = True
            if wm["value"]:
                watchlist_nums = [s.strip() for s in wm["value"].split(",") if s.strip()]
    except Exception:
        pass

    # entry_value 매핑 (있으면)
    entry_value_records = []
    if "entry_value" in src_cols:
        for r in rows:
            ev = (dict(r).get("entry_value") or "").strip()
            if ev:
                entry_value_records.append({"ID": dict(r).get("ID", ""), "entry_value": ev})

    sync_meta_lookup = {row["key"]: row["value"] for row in sync_meta_records}

    duplicate_member_ids_by_group: dict[str, list[str]] = {}
    duplicate_member_records = []
    duplicate_group_id_map: dict[str, str] = {}
    try:
        member_rows = src_conn.execute(
            """
            SELECT group_id, report_id, report_number, category, is_representative,
                   priority_score, raw_match, field_match, created_at, updated_at
            FROM duplicate_member
            """
        ).fetchall()
        for row in member_rows:
            group_id = str(row["group_id"] or "").strip()
            report_id = str(row["report_id"] or "").strip()
            if not group_id or not report_id:
                continue
            duplicate_member_ids_by_group.setdefault(group_id, []).append(report_id)
            duplicate_member_records.append({
                "group_id": group_id,
                "report_id": report_id,
                "report_number": str(row["report_number"] or "").strip(),
                "category": str(row["category"] or "").strip() or "other",
                "is_representative": int(row["is_representative"] or 0),
                "priority_score": int(row["priority_score"] or 0),
                "raw_match": int(row["raw_match"] or 0),
                "field_match": int(row["field_match"] or 0),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            })
    except Exception:
        pass

    report_lookup_by_id = {str(dict(r).get("ID", "")).strip(): dict(r) for r in rows}

    duplicate_group_records = []
    try:
        dup_rows = src_conn.execute(
            "SELECT * FROM duplicate_group"
        ).fetchall()
        for row in dup_rows:
            source_group_id = str(row["group_id"] or "").strip()
            if not source_group_id:
                continue
            status = str(row["status"] or "").strip() or "confirmed_duplicate"
            canonical_group_id = source_group_id
            for report_id in duplicate_member_ids_by_group.get(source_group_id, []):
                raw_payload = raw_payloads.get(report_id, {})
                raw_content = raw_payload.get("raw_content")
                if raw_content is None or str(raw_content).strip() == "":
                    raw_content = report_lookup_by_id.get(report_id, {}).get("raw_content", "")
                normalized_raw = _normalize_duplicate_raw_content(raw_content)
                if normalized_raw:
                    canonical_group_id = _duplicate_payload_hash(normalized_raw)
                    break
            duplicate_group_id_map[source_group_id] = canonical_group_id
            duplicate_group_records.append({
                "group_id": canonical_group_id,
                "fingerprint": canonical_group_id,
                "match_type": str(row["match_type"] or "payload_exact").strip() or "payload_exact",
                "status": status,
                "representative_mode": str(row["representative_mode"] or "auto").strip() or "auto",
                "representative_id": str(row["representative_id"] or "").strip(),
                "member_count": int(row["member_count"] or 0),
                "apply_globally": int(row["apply_globally"] or 0)
                if "apply_globally" in row.keys()
                else (1 if status == "confirmed_duplicate" else 0),
                "note": str(row["note"] or ""),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            })
    except Exception:
        pass

    valid_duplicate_group_ids = {row["group_id"] for row in duplicate_group_records}
    mapped_duplicate_member_records = []
    for row in duplicate_member_records:
        mapped_group_id = duplicate_group_id_map.get(row["group_id"], row["group_id"])
        if mapped_group_id not in valid_duplicate_group_ids:
            continue
        mapped_duplicate_member_records.append({
            **row,
            "group_id": mapped_group_id,
        })

    src_conn.close()

    # bulk INSERT (배치)
    BATCH = 200
    with engine.begin() as conn:
        # 기존 크롤링 데이터만 교체한다.
        # admin_users / api_keys 같은 서버 전용 테이블은 유지해야 한다.
        conn.execute(models.merge_traffic_table.delete())
        conn.execute(models.merge_parking_table.delete())
        conn.execute(models.merge_other_table.delete())
        conn.execute(models.detail_traffic_table.delete())
        conn.execute(models.detail_parking_table.delete())
        conn.execute(models.detail_other_table.delete())
        conn.execute(models.title_table.delete())
        if watchlist_found:
            conn.execute(models.watchlist_table.delete())
        conn.execute(models.entry_value_table.delete())
        conn.execute(models.raw_content_table.delete())
        conn.execute(models.sync_meta_table.delete())
        conn.execute(models.duplicate_member_table.delete())
        conn.execute(models.duplicate_group_table.delete())

        # title
        for i in range(0, len(title_records), BATCH):
            chunk = title_records[i:i + BATCH]
            conn.execute(models.title_table.insert(), chunk)

        # detail per category
        for cat, recs in detail_records_by_cat.items():
            if not recs:
                continue
            tbl = {
                "traffic": models.detail_traffic_table,
                "parking": models.detail_parking_table,
                "other": models.detail_other_table,
            }[cat]
            for i in range(0, len(recs), BATCH):
                conn.execute(tbl.insert(), recs[i:i + BATCH])

        # watchlist
        if watchlist_found and watchlist_nums:
            conn.execute(
                models.watchlist_table.insert(),
                [{"신고번호": n} for n in watchlist_nums],
            )

        # entry_value
        if "entry_value" in src_cols and entry_value_records:
            for i in range(0, len(entry_value_records), BATCH):
                conn.execute(
                    models.entry_value_table.insert(),
                    entry_value_records[i:i + BATCH],
                )

        if raw_content_records:
            for i in range(0, len(raw_content_records), BATCH):
                conn.execute(
                    models.raw_content_table.insert(),
                    raw_content_records[i:i + BATCH],
                )

        if sync_meta_records:
            for i in range(0, len(sync_meta_records), BATCH):
                conn.execute(
                    models.sync_meta_table.insert(),
                    sync_meta_records[i:i + BATCH],
                )

        elif "last_sync" in sync_meta_lookup:
            conn.execute(
                models.sync_meta_table.insert(),
                [{"key": "last_sync", "value": sync_meta_lookup["last_sync"]}],
            )

    # title + detail → merge 재생성
    database.merge_final(engine)

    if duplicate_group_records:
        with engine.begin() as conn:
            conn.execute(models.duplicate_member_table.delete())
            conn.execute(models.duplicate_group_table.delete())
            for i in range(0, len(duplicate_group_records), BATCH):
                conn.execute(
                    models.duplicate_group_table.insert(),
                    duplicate_group_records[i:i + BATCH],
                )
            if mapped_duplicate_member_records:
                for i in range(0, len(mapped_duplicate_member_records), BATCH):
                    conn.execute(
                        models.duplicate_member_table.insert(),
                        mapped_duplicate_member_records[i:i + BATCH],
                    )

    logger.LoggerFactory.logbot.info(
        f"모바일 DB 복원 완료. 백업: {backup}, 신고건수: {len(title_records)}"
    )
    return backup, len(title_records)
