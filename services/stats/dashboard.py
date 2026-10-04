"""대시보드 응답 조립(EO R-05)."""
from datetime import datetime, timedelta

import pandas as pd
from sqlalchemy import func, select

from core.database import database
from core.utils.fallback import note_fallback
from services import report_policy
import settings.settings as app_settings
from services import duplicate_group_service
from services.report_cache import cached
from services.stats.common import _normalize_mode, _recent_answer_sort_key, _row_to_dict, _sanitize_jsonable
from services.stats.reads import _last_sync_label


@cached
def get_dashboard_stats(engine, mode: str = "canonical"):
    total = 0
    accept_count = 0
    partial_count = 0
    reject_count = 0
    processing_count = 0
    supplement_count = 0
    completed_count = 0
    withdraw_count = 0
    t_fine_count = 0
    t_penalty_count = 0
    t_reject_count = 0
    t_unconfirmed_count = 0
    recent_answers = []
    watchlist_items = []

    today = datetime.now().date()
    three_days_ago = today - timedelta(days=3)

    def _text_series(df, column):
        if column not in df.columns:
            return pd.Series([""] * len(df), index=df.index, dtype="object")
        return df[column].fillna("").astype(str)

    def _status_series(df):
        return report_policy.status_series(df)

    def _response_dates(df):
        if "답변일" not in df.columns:
            return pd.Series(pd.NaT, index=df.index)
        return pd.to_datetime(df["답변일"], errors="coerce").dt.date

    table_category_map = {
        database.merge_traffic_table: "traffic",
        database.merge_parking_table: "parking",
        database.merge_other_table: "other",
    }

    combined_frames = []
    with engine.connect() as conn:
        # 세 분류 집계·최근 답변·감시목록·마지막 동기화를 한 읽기 스냅샷에서 읽는다. 사이에 신고가 분류를 옮겨도
        # 두 번 세거나 빠뜨리지 않는다(기술일지 A1-07, _load_stats_frames 와 같은 방식).
        conn.exec_driver_sql('BEGIN')
        last_crawl_time = _last_sync_label(conn)
        for table_obj in [database.merge_traffic_table, database.merge_parking_table, database.merge_other_table]:
            # Full-population SQL aggregation; no report bodies in the count path.
            query = select(table_obj.c['처리상태'], table_obj.c['범칙금_과태료'], func.count().label('_weight')).group_by(
                table_obj.c['처리상태'], table_obj.c['범칙금_과태료'])
            df = pd.read_sql_query(duplicate_group_service.canonical_sql(table_obj, query, mode), conn)
            if df.empty:
                continue
            category = table_category_map.get(table_obj, "")
            df["category"] = category
            combined_frames.append(df)
            recent_query = select(table_obj).where(table_obj.c['답변일'] >= str(three_days_ago),
                table_obj.c['답변일'] < str(today + timedelta(days=1)))
            if app_settings.exclude_withdraw:
                recent_query = recent_query.where(report_policy.sql_not_withdrawn(table_obj.c['처리상태']))
            recent_query = duplicate_group_service.canonical_sql(table_obj, recent_query, mode).order_by(
                table_obj.c.synced_at.desc(), table_obj.c['답변일'].desc(), table_obj.c['신고번호'].desc()).limit(200)
            for row in conn.execute(recent_query):
                item = _row_to_dict(row._mapping)
                item['category'] = category
                recent_answers.append(item)

        try:
            watch_df = pd.read_sql_query(select(database.watchlist_table.c.신고번호), conn)
        except Exception as exc:
            note_fallback("report_stats.dashboard_watchlist", exc)
            watch_df = pd.DataFrame()
        watch_ids = watch_df["신고번호"].tolist() if "신고번호" in watch_df.columns else []

        if watch_ids:
            for table_obj in [database.merge_traffic_table, database.merge_parking_table, database.merge_other_table]:
                query = select(table_obj).where(table_obj.c.신고번호.in_(watch_ids))
                try:
                    df_watch_part = pd.read_sql_query(query, conn)
                except Exception as exc:
                    note_fallback("report_stats.dashboard_watch_rows", exc)
                    continue
                category = table_category_map.get(table_obj, "")
                for _, row in df_watch_part.iterrows():
                    item = _row_to_dict(row)
                    item["category"] = category
                    watchlist_items.append(item)

    combined_df = pd.concat(combined_frames, ignore_index=True) if combined_frames else pd.DataFrame()

    if not combined_df.empty:
        status_series = _status_series(combined_df)
        weights = combined_df['_weight']
        total += int(weights.sum())
        accept_count += int(weights[status_series == "수용"].sum())
        reject_count += int(weights[status_series.isin(report_policy.REJECT_STATUSES)].sum())
        partial_count += int(weights[status_series == "일부수용"].sum())
        processing_count += int(weights[status_series.isin(report_policy.PROCESSING_STATUSES)].sum())
        supplement_count += int(weights[status_series == report_policy.SUPPLEMENT_STATUS].sum())
        completed_count += int(weights[status_series.isin(report_policy.COMPLETED_STATUSES)].sum())
        withdraw_count += int(weights[status_series == report_policy.WITHDRAWN_STATUS].sum())

        traffic = report_policy.traffic_dashboard_masks(combined_df)
        t_fine_count += int(weights[traffic["fine"]].sum())
        t_penalty_count += int(weights[traffic["penalty"]].sum())
        t_reject_count += int(weights[traffic["reject"]].sum())
        t_unconfirmed_count += int(weights[traffic["unconfirmed"]].sum())

    recent_answers.sort(
        key=_recent_answer_sort_key,
        reverse=True,
    )
    watchlist_items.sort(key=lambda item: item["신고번호"] or "", reverse=True)

    effective_withdraw_count = 0 if app_settings.exclude_withdraw else withdraw_count
    valid_total = (accept_count + partial_count + reject_count + processing_count + supplement_count) if app_settings.exclude_withdraw else total
    t_bar_total = t_fine_count + t_penalty_count + t_reject_count + t_unconfirmed_count

    return _sanitize_jsonable({
        "last_crawl_time": last_crawl_time,
        "total": total,
        "acceptCount": accept_count,
        "partialCount": partial_count,
        "rejectCount": reject_count,
        "processingCount": processing_count,
        "supplementCount": supplement_count,
        "completedCount": completed_count,
        "withdrawCount": withdraw_count,
        "withdrawRawCount": withdraw_count,
        "withdrawGraphCount": effective_withdraw_count,
        "tFineCount": t_fine_count,
        "tPenaltyCount": t_penalty_count,
        "tRejectCount": t_reject_count,
        "tUnconfirmedCount": t_unconfirmed_count,
        "accept_pct": round((accept_count / valid_total * 100), 1) if valid_total > 0 else 0,
        "partial_pct": round((partial_count / valid_total * 100), 1) if valid_total > 0 else 0,
        "reject_pct": round((reject_count / valid_total * 100), 1) if valid_total > 0 else 0,
        "processing_pct": round((processing_count / valid_total * 100), 1) if valid_total > 0 else 0,
        "supplement_pct": round((supplement_count / valid_total * 100), 1) if valid_total > 0 else 0,
        "withdraw_pct": round((effective_withdraw_count / valid_total * 100), 1) if valid_total > 0 else 0,
        "tfine_pct": round((t_fine_count / t_bar_total * 100), 1) if t_bar_total > 0 else 0,
        "tpenalty_pct": round((t_penalty_count / t_bar_total * 100), 1) if t_bar_total > 0 else 0,
        "treject_pct": round((t_reject_count / t_bar_total * 100), 1) if t_bar_total > 0 else 0,
        "tunconfirmed_pct": round((t_unconfirmed_count / t_bar_total * 100), 1) if t_bar_total > 0 else 0,
        "recent_answers": recent_answers[:200],
        "watchlist": watchlist_items,
        "exclude_withdraw": app_settings.exclude_withdraw,
        "dedupe_mode": _normalize_mode(mode),
    })
