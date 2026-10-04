"""수집 대상 선정(EO R-06에서 database.py 에서 분리): 신규·미종결·목록 상태가 바뀐 신고를 상세 수집 대상으로 고른다.

목록 재조회 규칙은 contracts/community-ingest/vectors/list_refetch.json(list-refetch-v1). community.db 의 상세 상태·초기화 실패·
캡처 재시도는 sidecar 로 읽고, 읽지 못하면 기록을 남기고 그 축만 비운다(core/utils/fallback).
"""
import pandas as pd
from sqlalchemy import exists, func, select

from core.database.models import detail_other_table, detail_parking_table, detail_traffic_table, title_table
from core.utils import logger
from core.utils.fallback import note_fallback


def _get_title_ids_for_scan(conn, *, message: str):
    logger.LoggerFactory.logbot.info(message)
    query = select(title_table.c.ID)
    return pd.read_sql_query(query, conn)

def _get_new_and_incomplete_ids(conn):
    logger.LoggerFactory.logbot.info("신규, 미종결 신고 건 스캔 시작")
    query_new = select(title_table.c.ID).where(
        ~exists().where(title_table.c.ID == detail_traffic_table.c.ID)
    ).where(
        ~exists().where(title_table.c.ID == detail_parking_table.c.ID)
    ).where(
        ~exists().where(title_table.c.ID == detail_other_table.c.ID)
    )

    incomplete_queries = [
        # 종결여부 NULL(모름)도 미종결로 본다. `!= 'Y'` 만 쓰면 NULL 행이 영영 빠진다(S-19).
        select(t.c.ID).where(func.coalesce(t.c.종결여부, '') != 'Y')
        for t in (detail_traffic_table, detail_parking_table, detail_other_table)
    ]

    df_new = pd.read_sql_query(query_new, conn)
    df_incomplete = pd.concat(
        [pd.read_sql_query(q, conn) for q in incomplete_queries],
        ignore_index=True,
    )

    merged = pd.concat([
        df_new, df_incomplete,
    ]).drop_duplicates()
    return merged

def _labels_differ(left, right) -> bool:
    """목록 라벨 비교. None 을 != 로 직접 비교하지 않는다(계약 list-refetch-v1)."""
    if left is None or right is None:
        return (left is None) != (right is None)
    return str(left) != str(right)


def should_refetch_list_item(*, in_personal_detail: bool, list_label,
                             detail_status_label, closed, supplement_open,
                             in_capture_retry: bool = False,
                             rebuild_failed_permanent: bool = False,
                             failed_list_label=None) -> bool:
    """contracts/community-ingest/vectors/list_refetch.json 규칙 그대로.

    closed/supplement_open 은 detail 표 원본(사용자 override 무시 — 호출자가
    detail 표에서 읽어 넘긴다). 처리상태 canonical 은 비교하지 않는다.
    """
    if not in_personal_detail:
        return True
    if (closed or "") != "Y":
        return True
    if (supplement_open or "") == "Y":
        return True
    if in_capture_retry:
        return True
    if detail_status_label is None:
        if rebuild_failed_permanent and not _labels_differ(list_label, failed_list_label):
            return False
        return True
    return _labels_differ(list_label, detail_status_label)


def _community_detail_status_labels() -> dict:
    """community.db detail_status {report_id: c_now_label}. 실패하면 빈 dict."""
    try:
        from services.community_store import CommunityStore
        store = CommunityStore.open()
        dataset_id = store.local_dataset_id()
        rows = store.connect().execute(
            "SELECT source_report_id, c_now_label FROM detail_status WHERE local_dataset_id=?",
            (dataset_id,)).fetchall()
        return {row["source_report_id"]: row["c_now_label"] for row in rows}
    except Exception as exc:
        note_fallback("collection_policy.community_detail_status_labels", exc)
        return {}


def _community_rebuild_permanent_labels() -> dict:
    """마지막 rebuild 의 failed_permanent {report_id: last_list_label}. 실패하면 빈 dict."""
    try:
        from services.community_store import CommunityStore
        store = CommunityStore.open()
        rows = store.connect().execute(
            "SELECT i.source_report_id AS rid, i.last_list_label AS label, j.updated_at AS updated"
            " FROM rebuild_items i JOIN rebuild_jobs j ON j.run_id=i.run_id"
            " WHERE i.state='failed_permanent' ORDER BY j.updated_at DESC").fetchall()
        labels = {}
        for row in rows:
            labels.setdefault(row["rid"], row["label"])
        return labels
    except Exception as exc:
        note_fallback("collection_policy.community_rebuild_permanent_labels", exc)
        return {}


def _community_capture_retry_ids() -> set:
    """T4 community_capture.capture_retry_ids(). 없으면 빈 집합(파일 직접 읽기 금지)."""
    try:
        from services import community_capture as capture
        fn = getattr(capture, "capture_retry_ids", None)
        if fn is None:
            return set()
        return set(fn() or set())
    except Exception as exc:
        note_fallback("collection_policy.community_capture_retry_ids", exc)
        return set()


def _list_refetch_extra_ids(conn) -> set:
    """list_refetch 벡터로 다시 읽을 ID. detail 표 원본만 본다(override 무시)."""
    title_rows = conn.execute(select(title_table.c.ID, title_table.c.상태)).fetchall()
    if not title_rows:
        return set()
    detail_site: dict = {}
    for table in (detail_traffic_table, detail_parking_table, detail_other_table):
        for row in conn.execute(
                select(table.c.ID, table.c.종결여부, table.c.보완_미응답)).fetchall():
            detail_site.setdefault(str(row[0]), (row[1], row[2]))
    status_labels = _community_detail_status_labels()
    permanent_labels = _community_rebuild_permanent_labels()
    retry_ids = _community_capture_retry_ids()
    extra = set()
    for report_id, list_label in title_rows:
        key = str(report_id)
        in_detail = key in detail_site
        closed, supplement = detail_site.get(key, (None, None))
        if should_refetch_list_item(
                in_personal_detail=in_detail, list_label=list_label,
                detail_status_label=status_labels.get(key),
                closed=closed, supplement_open=supplement,
                in_capture_retry=(key in retry_ids),
                rebuild_failed_permanent=(key in permanent_labels),
                failed_list_label=permanent_labels.get(key)):
            extra.add(report_id)
    return extra


def get_pending_detail_ids(engine, force=False):
    with engine.connect() as conn:
        if force:
            df = _get_title_ids_for_scan(conn, message="전체 신고 건을 다시 스캔합니다.")
        else:
            detail_rows = sum(
                conn.execute(select(func.count()).select_from(t)).scalar()
                for t in (detail_traffic_table, detail_parking_table, detail_other_table)
            )
            if detail_rows == 0:  # 주정차 표도 센다(S-19)
                df = _get_title_ids_for_scan(conn, message="detail 테이블 비어 있어 전체 스캔 시작")
            else:
                df = _get_new_and_incomplete_ids(conn)
                try:
                    # 목록 상태 변경 재조회(벡터 list-refetch-v1) — 기존 후보와 합집합.
                    extra = _list_refetch_extra_ids(conn)
                    if extra:
                        df = pd.concat([df, pd.DataFrame({"ID": list(extra)})],
                                       ignore_index=True).drop_duplicates()
                except Exception as exc:
                    logger.LoggerFactory.logbot.warning(f"목록 재조회 선정 생략: {exc}")
        
        if df.empty:
            return []

        df_sorted = df.sort_values(by='ID', ascending=True)
        detaillist = df_sorted['ID'].tolist()
        logger.LoggerFactory.logbot.debug("스캔대상 ID 리스트화 완료")
        logger.LoggerFactory.logbot.info(f"스캔대상 ID 총 {len(detaillist)}건")
        return detaillist
