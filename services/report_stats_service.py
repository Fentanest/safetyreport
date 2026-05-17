import re
from datetime import datetime, timedelta

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from core.database import database
import settings.settings as app_settings
from services import duplicate_group_service
from services.report_query_service import _safe_read


_STATS_COLUMNS = [
    "ID",
    "신고명",
    "신고번호",
    "신고일",
    "답변일",
    "처리기관",
    "담당자",
    "처리상태",
    "범칙금_과태료",
    "위반법규",
    "위반장소",
    "발생일자",
    "발생시각",
    "별점",
    "synced_at",
]

_MAP_COLUMNS = [
    "ID",
    "신고번호",
    "신고명",
    "신고일",
    "답변일",
    "처리상태",
    "범칙금_과태료",
    "위반장소",
    "주소정규화",
    "행정구역",
    "위도",
    "경도",
    "처리기관",
]


def _extract_fine_amount(text) -> int:
    if not text:
        return 0
    text = str(text)
    if "과태료" not in text:
        return 0
    match = re.search(r"([\d,]+)\s*원", text)
    if match:
        return int(match.group(1).replace(",", ""))
    return 0


_REPORT_FIELDS = [
    "ID",
    "신고번호",
    "신고명",
    "신고일",
    "답변일",
    "처리기관",
    "담당자",
    "처리상태",
    "범칙금_과태료",
    "벌점",
    "차량번호",
    "위반법규",
    "위반장소",
    "발생일자",
    "발생시각",
    "신고내용",
    "처리내용",
    "첨부사진",
    "첨부파일",
    "지도",
    "만족도조사여부",
    "별점",
    "별점사유",
    "감시목록",
    "synced_at",
    "보완횟수",
    "보완_미응답",
    "보완_요청자",
    "보완_요청일시",
    "보완_완료일시",
    "보완_요청_내용",
    "보완_신고자_의견",
]


def _sanitize_jsonable(value):
    if isinstance(value, dict):
        return {key: _sanitize_jsonable(inner) for key, inner in value.items()}
    if isinstance(value, list):
        return [_sanitize_jsonable(inner) for inner in value]
    if isinstance(value, tuple):
        return [_sanitize_jsonable(inner) for inner in value]
    if pd.isna(value):
        return None
    return value


def _row_to_dict(row) -> dict:
    data = {}
    for field in _REPORT_FIELDS:
        value = row.get(field, "")
        if pd.isna(value):
            value = ""
        data[field] = value
    data["ID"] = str(data["ID"])
    data["결과"] = data["처리상태"]
    return data


def _text_or_empty(value) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()


def _int_or_default(value, default: int = -1) -> int:
    if value is None or pd.isna(value):
        return default
    if isinstance(value, str) and not value.strip():
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default


def _recent_answer_sort_key(item):
    synced_at = _int_or_default(item.get("synced_at"))
    report_number = _text_or_empty(item.get("신고번호"))
    response_date = _text_or_empty(item.get("답변일"))
    if synced_at >= 0:
        return (1, synced_at, report_number, "")
    return (0, response_date, report_number, "")


def _parse_and_or_groups(query: str):
    text = _text_or_empty(query)
    if not text:
        return []
    groups = []
    for raw_group in text.split(","):
        terms = [_text_or_empty(term) for term in raw_group.split("&")]
        terms = [term for term in terms if term]
        if terms:
            groups.append(terms)
    return groups


def _matches_and_or_text(value, query: str, exact: bool = False) -> bool:
    groups = _parse_and_or_groups(query)
    if not groups:
        return True

    source_cmp = _text_or_empty(value).casefold()
    if exact and len(groups) == 1 and len(groups[0]) == 1:
        return source_cmp == groups[0][0].casefold()

    return any(all(term.casefold() in source_cmp for term in group) for group in groups)


def _apply_text_query(df, column: str, query: str, exact: bool = False):
    if df.empty or column not in df.columns or not _text_or_empty(query):
        return df
    series = df[column].fillna("").astype(str)
    mask = series.apply(lambda value: _matches_and_or_text(value, query, exact=exact))
    return df[mask]


def _can_push_simple_text_query(query: str) -> bool:
    text = _text_or_empty(query)
    return bool(text) and "&" not in text and "," not in text


def _build_select_for_columns(table_obj, column_names):
    columns = [table_obj.c[column] for column in column_names if column in table_obj.c]
    return select(*columns)


def _build_stats_select(table_obj):
    return _build_select_for_columns(table_obj, _STATS_COLUMNS)


def _build_stats_query(table_obj, filters=None, column_names=None):
    query = _build_select_for_columns(table_obj, column_names or _STATS_COLUMNS)
    if not filters:
        return query

    if filters.get("year") and filters["year"] not in ("all", "", None) and "답변일" in table_obj.c:
        query = query.where(table_obj.c["답변일"].startswith(filters["year"]))

    if filters.get("reportDateStart") and "신고일" in table_obj.c:
        query = query.where(table_obj.c["신고일"] >= filters["reportDateStart"])
    if filters.get("reportDateEnd") and "신고일" in table_obj.c:
        query = query.where(table_obj.c["신고일"] <= filters["reportDateEnd"] + " 23:59:59")

    if filters.get("occurDateStart") and "발생일자" in table_obj.c:
        query = query.where(table_obj.c["발생일자"] >= filters["occurDateStart"])
    if filters.get("occurDateEnd") and "발생일자" in table_obj.c:
        query = query.where(table_obj.c["발생일자"] <= filters["occurDateEnd"])

    if filters.get("responseDateStart") and "답변일" in table_obj.c:
        query = query.where(table_obj.c["답변일"] >= filters["responseDateStart"])
    if filters.get("responseDateEnd") and "답변일" in table_obj.c:
        query = query.where(table_obj.c["답변일"] <= filters["responseDateEnd"])

    if filters.get("occurTimeStart") and "발생시각" in table_obj.c:
        query = query.where(table_obj.c["발생시각"] >= filters["occurTimeStart"])
    if filters.get("occurTimeEnd") and "발생시각" in table_obj.c:
        query = query.where(table_obj.c["발생시각"] <= filters["occurTimeEnd"])

    if filters.get("excludePolice") and "처리기관" in table_obj.c:
        query = query.where(~table_obj.c["처리기관"].contains("경찰"))
    if filters.get("onlyPolice") and "처리기관" in table_obj.c:
        query = query.where(table_obj.c["처리기관"].contains("경찰"))

    report_name = filters.get("reportName")
    if report_name and "신고명" in table_obj.c and _can_push_simple_text_query(report_name):
        query = query.where(table_obj.c["신고명"].contains(_text_or_empty(report_name)))

    location = filters.get("location")
    if location and "위반장소" in table_obj.c and _can_push_simple_text_query(location):
        query = query.where(table_obj.c["위반장소"].contains(_text_or_empty(location)))

    agency = filters.get("agency")
    if agency and "처리기관" in table_obj.c and _can_push_simple_text_query(agency):
        if filters.get("agencyExact") and not app_settings.normalize_police:
            query = query.where(table_obj.c["처리기관"] == _text_or_empty(agency))
        elif not filters.get("agencyExact"):
            query = query.where(table_obj.c["처리기관"].contains(_text_or_empty(agency)))

    return query


def _read_stats_frame(conn, table_obj, filters=None, column_names=None):
    try:
        return pd.read_sql_query(_build_stats_query(table_obj, filters, column_names=column_names), conn)
    except OperationalError:
        return pd.DataFrame()


def _normalize_mode(mode: str | None) -> str:
    normalized = _text_or_empty(mode).lower() or "raw"
    return normalized if normalized in {"raw", "canonical"} else "raw"


def _project_stats_frame(engine, df: pd.DataFrame, *, mode: str = "raw") -> pd.DataFrame:
    normalized_mode = _normalize_mode(mode)
    if normalized_mode == "raw" or df.empty or "ID" not in df.columns:
        return df
    records = duplicate_group_service.project_records(engine, df.to_dict(orient="records"), mode=normalized_mode)
    if not records:
        return pd.DataFrame(columns=df.columns)
    projected = pd.DataFrame(records)
    for column in df.columns:
        if column not in projected.columns:
            projected[column] = ""
    return projected[df.columns]


def _ensure_id_column(df: pd.DataFrame) -> pd.DataFrame:
    if "ID" not in df.columns:
        df["ID"] = ""
    else:
        df["ID"] = df["ID"].fillna("").astype(str)
    return df


def _exclude_withdraw_rows(df: pd.DataFrame) -> pd.DataFrame:
    if not app_settings.exclude_withdraw or df.empty or "처리상태" not in df.columns:
        return df
    return df[df["처리상태"].fillna("").astype(str) != "취하"].copy()


def _load_available_years(conn):
    available_years = set()
    for table_obj in [database.merge_traffic_table, database.merge_parking_table, database.merge_other_table]:
        if "답변일" not in table_obj.c:
            continue
        query = (
            select(func.substr(table_obj.c["답변일"], 1, 4).label("year"))
            .where(table_obj.c["답변일"].is_not(None))
            .distinct()
        )
        try:
            df_years = pd.read_sql_query(query, conn)
        except OperationalError:
            continue
        if df_years.empty or "year" not in df_years.columns:
            continue
        years = df_years["year"].dropna().astype(str)
        available_years.update(years[years.str.match(r"^\d{4}$", na=False)].tolist())
    return sorted(available_years, reverse=True)


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

    # 서버는 크롤링 종료 시 mysafety_sync_meta.last_sync 에 ISO8601 시각을 저장한다.
    # 모바일도 같은 키/형식으로 기록하므로 서버↔모바일 DB import 시 round-trip 으로 보존된다.
    last_crawl_time = "기록 없음"
    with engine.connect() as conn:
        row = conn.execute(
            select(database.sync_meta_table.c.value).where(
                database.sync_meta_table.c.key == "last_sync"
            )
        ).fetchone()
    if row and row[0]:
        last_crawl_time = datetime.fromisoformat(row[0]).strftime("%Y-%m-%d %H:%M:%S")

    today = datetime.now().date()
    three_days_ago = today - timedelta(days=3)

    def _text_series(df, column):
        if column not in df.columns:
            return pd.Series([""] * len(df), index=df.index, dtype="object")
        return df[column].fillna("").astype(str)

    def _status_series(df):
        return _text_series(df, "처리상태")

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
        for table_obj in [database.merge_traffic_table, database.merge_parking_table, database.merge_other_table]:
            df = _safe_read(conn, table_obj)
            if df.empty:
                continue
            category = table_category_map.get(table_obj, "")
            df["category"] = category
            combined_frames.append(df)

        try:
            watch_df = pd.read_sql_query(select(database.watchlist_table.c.신고번호), conn)
        except Exception:
            watch_df = pd.DataFrame()
        watch_ids = watch_df["신고번호"].tolist() if "신고번호" in watch_df.columns else []

        if watch_ids:
            for table_obj in [database.merge_traffic_table, database.merge_parking_table, database.merge_other_table]:
                query = select(table_obj).where(table_obj.c.신고번호.in_(watch_ids))
                try:
                    df_watch_part = pd.read_sql_query(query, conn)
                except Exception:
                    continue
                category = table_category_map.get(table_obj, "")
                for _, row in df_watch_part.iterrows():
                    item = _row_to_dict(row)
                    item["category"] = category
                    watchlist_items.append(item)

    combined_df = pd.concat(combined_frames, ignore_index=True) if combined_frames else pd.DataFrame()
    combined_df = _project_stats_frame(engine, combined_df, mode=mode)

    if not combined_df.empty:
        status_series = _status_series(combined_df)
        total += len(combined_df)
        accept_count += int((status_series == "수용").sum())
        reject_count += int(status_series.isin(["불수용", "기타"]).sum())
        partial_count += int((status_series == "일부수용").sum())
        processing_count += int(status_series.isin(["처리중", "진행", "진행중", "검토중"]).sum())
        supplement_count += int((status_series == "보완요청").sum())
        completed_count += int(status_series.isin(["수용", "불수용", "일부수용", "기타", "답변완료"]).sum())
        withdraw_count += int((status_series == "취하").sum())

        traffic_df = combined_df[combined_df["category"].fillna("").astype(str) == "traffic"] if "category" in combined_df.columns else pd.DataFrame()
        if not traffic_df.empty:
            fine_series = _text_series(traffic_df, "범칙금_과태료")
            traffic_status = _status_series(traffic_df)
            t_fine_count += int(fine_series.str.contains("과태료", na=False).sum())
            t_penalty_count += int(fine_series.str.contains("경고|범칙금", na=False).sum())
            t_reject_count += int(traffic_status.isin(["불수용", "기타"]).sum())
            t_unconfirmed_count += int(((fine_series == "미확인") & (~traffic_status.isin(["불수용", "기타"]))).sum())

        response_dates = _response_dates(combined_df)
        recent_mask = response_dates.notna() & (response_dates >= three_days_ago) & (response_dates <= today)
        recent_df = combined_df[recent_mask]
        if app_settings.exclude_withdraw:
            recent_df = recent_df[_status_series(recent_df) != "취하"]

        for _, row in recent_df.iterrows():
            item = _row_to_dict(row)
            item["category"] = _text_or_empty(row.get("category"))
            recent_answers.append(item)

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


def get_agency_stats(engine, filters=None, mode: str = "canonical"):
    with engine.connect() as conn:
        available_years = _load_available_years(conn)
        df_t = _read_stats_frame(conn, database.merge_traffic_table, filters)
        df_p = _read_stats_frame(conn, database.merge_parking_table, filters)
        df_o = _read_stats_frame(conn, database.merge_other_table, filters)

    df_t = _ensure_id_column(df_t)
    df_p = _ensure_id_column(df_p)
    df_o = _ensure_id_column(df_o)

    if not df_t.empty:
        df_t["category"] = "traffic"
    if not df_p.empty:
        df_p["category"] = "parking"
    if not df_o.empty:
        df_o["category"] = "other"

    combined_df = pd.concat([df_t, df_p, df_o], ignore_index=True) if not (df_t.empty and df_p.empty and df_o.empty) else pd.DataFrame()
    combined_df = _project_stats_frame(engine, combined_df, mode=mode)
    if not combined_df.empty and "category" in combined_df.columns:
        df_t = combined_df[combined_df["category"] == "traffic"].copy()
        df_p = combined_df[combined_df["category"] == "parking"].copy()
        df_o = combined_df[combined_df["category"] == "other"].copy()
        for df_cat in (df_t, df_p, df_o):
            if "category" in df_cat.columns:
                df_cat.drop(columns=["category"], inplace=True, errors="ignore")

    def _calc_avg_days(group_df):
        try:
            d_end = pd.to_datetime(group_df["답변일"], errors="coerce")
            d_start = pd.to_datetime(group_df["신고일"], errors="coerce")
            days = (d_end - d_start).dt.days.dropna()
            days = days[days >= 0]
            return round(float(days.mean()), 1) if len(days) > 0 else None
        except Exception:
            return None

    def _calc_avg_rating(group_df):
        if "별점" not in group_df.columns:
            return None, 0
        ratings = pd.to_numeric(group_df["별점"], errors="coerce").dropna()
        ratings = ratings[(ratings >= 1) & (ratings <= 5)]
        if len(ratings) == 0:
            return None, 0
        return round(float(ratings.mean()), 2), int(len(ratings))

    def calc_stats(df):
        empty_payload = {
            "by_agency": [],
            "by_person": [],
            "police_by_agency": [],
            "police_by_person": [],
            "other_by_agency": [],
            "other_by_person": [],
            "by_law": [],
            "total_fine_amount": 0,
            "available_laws": [],
        }
        if df.empty:
            return empty_payload

        if filters:
            if filters.get("year") and filters["year"] not in ("all", "", None) and "답변일" in df.columns:
                df = df[df["답변일"].str.startswith(filters["year"], na=False)]
            if filters.get("reportName") and "신고명" in df.columns:
                df = _apply_text_query(df, "신고명", filters["reportName"])
            if filters.get("location") and "위반장소" in df.columns:
                df = _apply_text_query(df, "위반장소", filters["location"])
            if filters.get("reportDateStart") and "신고일" in df.columns:
                df = df[df["신고일"] >= filters["reportDateStart"]]
            if filters.get("reportDateEnd") and "신고일" in df.columns:
                df = df[df["신고일"] <= filters["reportDateEnd"] + " 23:59:59"]
            if filters.get("occurDateStart") and "발생일자" in df.columns:
                df = df[df["발생일자"] >= filters["occurDateStart"]]
            if filters.get("occurDateEnd") and "발생일자" in df.columns:
                df = df[df["발생일자"] <= filters["occurDateEnd"]]
            if filters.get("responseDateStart") and "답변일" in df.columns:
                df = df[df["답변일"] >= filters["responseDateStart"]]
            if filters.get("responseDateEnd") and "답변일" in df.columns:
                df = df[df["답변일"] <= filters["responseDateEnd"]]
            if filters.get("occurTimeStart") and "발생시각" in df.columns:
                df = df[df["발생시각"] >= filters["occurTimeStart"]]
            if filters.get("occurTimeEnd") and "발생시각" in df.columns:
                df = df[df["발생시각"] <= filters["occurTimeEnd"]]
            if filters.get("agency") and "처리기관" in df.columns:
                agency_query = filters["agency"]
                use_exact_agency = filters.get("agencyExact") and "&" not in agency_query and "," not in agency_query
                df = _apply_text_query(df, "처리기관", agency_query, exact=use_exact_agency)
            if filters.get("excludePolice") and "처리기관" in df.columns:
                df = df[~df["처리기관"].str.contains("경찰", na=False)]
            if filters.get("onlyPolice") and "처리기관" in df.columns:
                df = df[df["처리기관"].str.contains("경찰", na=False)]

        df = _exclude_withdraw_rows(df)

        if "위반법규" in df.columns:
            laws = df["위반법규"].dropna().astype(str)
            nonempty_laws = laws[laws.str.strip() != ""]
            available_laws = sorted(nonempty_laws.unique().tolist())
            has_empty_law = bool((df["위반법규"].fillna("").astype(str).str.strip() == "").any())
        else:
            available_laws = []
            has_empty_law = False

        if filters and filters.get("law") and "위반법규" in df.columns:
            if filters["law"] == "__없음__":
                df = df[df["위반법규"].fillna("").astype(str).str.strip() == ""]
            else:
                df = df[df["위반법규"].str.contains(filters["law"], na=False, regex=False)]

        if df.empty:
            return empty_payload

        if app_settings.normalize_police and "처리기관" in df.columns:
            df["처리기관"] = df["처리기관"].apply(database.normalize_police_agency)

        df["처리기관"] = df.get("처리기관", pd.Series()).fillna("알수없음")
        df["담당자"] = df.get("담당자", pd.Series()).fillna("미지정")
        df["처리상태"] = df.get("처리상태", pd.Series()).fillna("처리중")
        df["범칙금_과태료"] = df.get("범칙금_과태료", pd.Series()).fillna("")
        df = df[~((df["담당자"].isin(["", "미지정"])) & (df["처리상태"].isin(["처리중", "진행", "진행중", "검토중", "취하"])))]

        stats_person = []
        for (agency, person), group in df.groupby(["처리기관", "담당자"]):
            total = len(group)
            disposition_counts = _disposition_counts(group)
            avg = _calc_avg_days(group)
            total_fine = int(group["범칙금_과태료"].apply(_extract_fine_amount).sum())
            avg_rating, rating_count = _calc_avg_rating(group)
            stats_person.append({
                "agency": agency,
                "person": person,
                "total": total,
                "avg_days": avg,
                "total_fine_amount": total_fine,
                "fines": disposition_counts["fines"],
                "fines_pct": round((disposition_counts["fines"] / total) * 100, 1) if total > 0 else 0,
                "warnings": disposition_counts["warnings"],
                "warnings_pct": round((disposition_counts["warnings"] / total) * 100, 1) if total > 0 else 0,
                "rejects": disposition_counts["rejects"],
                "rejects_pct": round((disposition_counts["rejects"] / total) * 100, 1) if total > 0 else 0,
                "unconfirmed": disposition_counts["unconfirmed"],
                "unconfirmed_pct": round((disposition_counts["unconfirmed"] / total) * 100, 1) if total > 0 else 0,
                "avg_rating": avg_rating,
                "rating_count": rating_count,
            })

        stats_agency = []
        for agency, group in df.groupby("처리기관"):
            agency = agency[0] if isinstance(agency, tuple) else agency
            total = len(group)
            disposition_counts = _disposition_counts(group)
            avg = _calc_avg_days(group)
            total_fine = int(group["범칙금_과태료"].apply(_extract_fine_amount).sum())
            avg_rating, rating_count = _calc_avg_rating(group)
            stats_agency.append({
                "agency": agency,
                "total": total,
                "avg_days": avg,
                "total_fine_amount": total_fine,
                "fines": disposition_counts["fines"],
                "fines_pct": round((disposition_counts["fines"] / total) * 100, 1) if total > 0 else 0,
                "warnings": disposition_counts["warnings"],
                "warnings_pct": round((disposition_counts["warnings"] / total) * 100, 1) if total > 0 else 0,
                "rejects": disposition_counts["rejects"],
                "rejects_pct": round((disposition_counts["rejects"] / total) * 100, 1) if total > 0 else 0,
                "unconfirmed": disposition_counts["unconfirmed"],
                "unconfirmed_pct": round((disposition_counts["unconfirmed"] / total) * 100, 1) if total > 0 else 0,
                "avg_rating": avg_rating,
                "rating_count": rating_count,
            })

        stats_law = []
        if "위반법규" in df.columns:
            df_law = df.copy()
            df_law["위반법규"] = df_law["위반법규"].fillna("").astype(str)
            df_law = df_law[df_law["위반법규"].str.strip() != ""]
            for law, group in df_law.groupby("위반법규"):
                total = len(group)
                disposition_counts = _disposition_counts(group)
                avg = _calc_avg_days(group)
                total_fine = int(group["범칙금_과태료"].apply(_extract_fine_amount).sum())
                avg_rating, rating_count = _calc_avg_rating(group)
                stats_law.append({
                    "law": law,
                    "total": total,
                    "avg_days": avg,
                    "total_fine_amount": total_fine,
                    "fines": disposition_counts["fines"],
                    "fines_pct": round((disposition_counts["fines"] / total) * 100, 1) if total > 0 else 0,
                    "warnings": disposition_counts["warnings"],
                    "warnings_pct": round((disposition_counts["warnings"] / total) * 100, 1) if total > 0 else 0,
                    "rejects": disposition_counts["rejects"],
                    "rejects_pct": round((disposition_counts["rejects"] / total) * 100, 1) if total > 0 else 0,
                    "unconfirmed": disposition_counts["unconfirmed"],
                    "unconfirmed_pct": round((disposition_counts["unconfirmed"] / total) * 100, 1) if total > 0 else 0,
                    "avg_rating": avg_rating,
                    "rating_count": rating_count,
                })

        category_total_fine = int(df["범칙금_과태료"].apply(_extract_fine_amount).sum())

        def _sort(items, key="total"):
            return pd.DataFrame(items).sort_values(by=[key], ascending=False).to_dict("records") if items else []

        all_agency = _sort(stats_agency)
        all_person = _sort(stats_person)
        all_law = _sort(stats_law)
        return _sanitize_jsonable({
            "by_agency": all_agency,
            "by_person": all_person,
            "police_by_agency": [item for item in all_agency if "경찰" in item["agency"]],
            "police_by_person": [item for item in all_person if "경찰" in item["agency"]],
            "other_by_agency": [item for item in all_agency if "경찰" not in item["agency"]],
            "other_by_person": [item for item in all_person if "경찰" not in item["agency"]],
            "by_law": all_law,
            "total_fine_amount": category_total_fine,
            "available_laws": available_laws,
            "has_empty_law": has_empty_law,
        })

    res_t = calc_stats(df_t)
    res_p = calc_stats(df_p)
    res_o = calc_stats(df_o)
    return _sanitize_jsonable({
        "traffic": res_t,
        "parking": res_p,
        "other": res_o,
        "available_years": available_years,
        "traffic_total_fine": int(df_t["범칙금_과태료"].apply(_extract_fine_amount).sum()) if not df_t.empty else 0,
        "dedupe_mode": _normalize_mode(mode),
    })


def _ratio_item(label: str, count: int, total: int) -> dict:
    safe_total = max(int(total), 0)
    safe_count = max(int(count), 0)
    return {
        "label": label,
        "count": safe_count,
        "pct": round((safe_count / safe_total) * 100, 1) if safe_total > 0 else 0,
    }


def _disposition_counts(group_df: pd.DataFrame) -> dict[str, int]:
    fine_series = group_df.get("범칙금_과태료", pd.Series(dtype="object")).fillna("").astype(str)
    status_series = group_df.get("처리상태", pd.Series(dtype="object")).fillna("").astype(str)

    fine_mask = fine_series.str.contains("과태료", na=False)
    warning_mask = fine_series.str.contains("경고|범칙금", na=False)
    reject_mask = status_series.isin(["불수용", "기타"])
    unconfirmed_mask = ~(fine_mask | warning_mask | reject_mask)

    return {
        "fines": int(fine_mask.sum()),
        "warnings": int(warning_mask.sum()),
        "rejects": int(reject_mask.sum()),
        "unconfirmed": int(unconfirmed_mask.sum()),
    }


def _build_status_breakdown(group_df: pd.DataFrame) -> list[dict]:
    status_series = group_df.get("처리상태", pd.Series(dtype="object")).fillna("").astype(str)
    processing_mask = status_series.isin(["", "진행", "진행중", "검토중", "처리중"])
    ordered = [
        _ratio_item("수용", int((status_series == "수용").sum()), len(group_df)),
        _ratio_item("일부수용", int((status_series == "일부수용").sum()), len(group_df)),
        _ratio_item("불수용", int((status_series == "불수용").sum()), len(group_df)),
        _ratio_item("기타", int((status_series == "기타").sum()), len(group_df)),
        _ratio_item("답변완료", int((status_series == "답변완료").sum()), len(group_df)),
        _ratio_item("보완요청", int((status_series == "보완요청").sum()), len(group_df)),
        _ratio_item("처리중", int(processing_mask.sum()), len(group_df)),
        _ratio_item("취하", int((status_series == "취하").sum()), len(group_df)),
        _ratio_item("이송", int((status_series == "이송").sum()), len(group_df)),
    ]
    return [item for item in ordered if item["count"] > 0]


def _build_disposition_breakdown(group_df: pd.DataFrame) -> list[dict]:
    counts = _disposition_counts(group_df)

    ordered = [
        _ratio_item("과태료", counts["fines"], len(group_df)),
        _ratio_item("경고/범칙금", counts["warnings"], len(group_df)),
        _ratio_item("불수용/기타", counts["rejects"], len(group_df)),
        _ratio_item("미확인", counts["unconfirmed"], len(group_df)),
    ]
    return [item for item in ordered if item["count"] > 0]


def _build_agency_breakdown(group_df: pd.DataFrame) -> list[dict]:
    if "처리기관" not in group_df.columns:
        return []
    agencies = (
        group_df["처리기관"]
        .fillna("")
        .astype(str)
        .map(lambda value: value.strip())
    )
    agencies = agencies[agencies != ""]
    if agencies.empty:
        return []
    counts = agencies.value_counts()
    total = int(len(group_df))
    results = []
    for name, count in counts.items():
        results.append({
            "name": str(name),
            "count": int(count),
            "pct": round((int(count) / total) * 100, 1) if total > 0 else 0,
        })
    return results


def _first_nonempty_value(group_df: pd.DataFrame, column: str) -> str:
    if column not in group_df.columns:
        return ""
    series = group_df[column].fillna("").astype(str)
    for value in series:
        text = value.strip()
        if text:
            return text
    return ""


def get_report_map_stats(engine, *, year: str | None = None, category: str = "all", mode: str = "canonical"):
    filters = {}
    if year and year not in ("all", "", None):
        filters["year"] = str(year)

    category = (category or "all").strip().lower()
    if category not in {"all", "traffic", "parking", "other"}:
        category = "all"

    with engine.connect() as conn:
        available_years = _load_available_years(conn)
        frames = []
        for table_obj, table_category in [
            (database.merge_traffic_table, "traffic"),
            (database.merge_parking_table, "parking"),
            (database.merge_other_table, "other"),
        ]:
            if category != "all" and category != table_category:
                continue
            df = _read_stats_frame(conn, table_obj, filters, column_names=_MAP_COLUMNS)
            if not df.empty:
                df["category"] = table_category
                frames.append(df)

    combined_df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=_MAP_COLUMNS + ["category"])
    combined_df = _ensure_id_column(combined_df)
    combined_df = _project_stats_frame(engine, combined_df, mode=mode)
    combined_df = _exclude_withdraw_rows(combined_df)

    if combined_df.empty:
        return _sanitize_jsonable({
            "points": [],
            "meta": {
                "available_years": available_years,
                "current_year": year or "all",
                "selected_category": category,
                "dedupe_mode": _normalize_mode(mode),
                "total_reports": 0,
                "geocoded_reports": 0,
                "missing_reports": 0,
                "address_groups": 0,
                "agency_count": 0,
            },
        })

    if "category" not in combined_df.columns:
        combined_df["category"] = category if category != "all" else "other"

    if app_settings.normalize_police and "처리기관" in combined_df.columns:
        combined_df["처리기관"] = combined_df["처리기관"].fillna("").astype(str).apply(database.normalize_police_agency)

    combined_df["위반장소"] = combined_df.get("위반장소", pd.Series(dtype="object")).fillna("").astype(str)
    combined_df["주소정규화"] = combined_df.get("주소정규화", pd.Series(dtype="object")).fillna("").astype(str)
    combined_df["행정구역"] = combined_df.get("행정구역", pd.Series(dtype="object")).fillna("").astype(str)
    combined_df["위도"] = pd.to_numeric(combined_df.get("위도"), errors="coerce")
    combined_df["경도"] = pd.to_numeric(combined_df.get("경도"), errors="coerce")
    combined_df["주소키"] = combined_df["주소정규화"].str.strip()
    combined_df.loc[combined_df["주소키"] == "", "주소키"] = combined_df["위반장소"].str.strip()

    geocoded_df = combined_df[
        combined_df["위도"].notna()
        & combined_df["경도"].notna()
        & (combined_df["주소키"].str.strip() != "")
    ].copy()
    missing_df = combined_df[
        (combined_df["위반장소"].str.strip() != "")
        & ~(combined_df["위도"].notna() & combined_df["경도"].notna())
    ].copy()

    points = []
    if not geocoded_df.empty:
        for (_, _, _), group in geocoded_df.groupby(["위도", "경도", "주소키"], dropna=False):
            lat = float(group["위도"].iloc[0])
            lng = float(group["경도"].iloc[0])
            total = int(len(group))
            region_name = _first_nonempty_value(group, "행정구역") or _first_nonempty_value(group, "위반장소")
            address_name = _first_nonempty_value(group, "위반장소") or _first_nonempty_value(group, "주소정규화")

            category_counts = group["category"].fillna("").astype(str).value_counts().to_dict()
            points.append({
                "lat": lat,
                "lng": lng,
                "address": address_name,
                "region": region_name,
                "total": total,
                "status_breakdown": _build_status_breakdown(group),
                "disposition_breakdown": _build_disposition_breakdown(group),
                "agency_breakdown": _build_agency_breakdown(group),
                "category_breakdown": [
                    _ratio_item("교통위반", int(category_counts.get("traffic", 0)), total),
                    _ratio_item("주정차위반", int(category_counts.get("parking", 0)), total),
                    _ratio_item("기타위반", int(category_counts.get("other", 0)), total),
                ],
            })

    points.sort(key=lambda item: item["total"], reverse=True)
    agencies = combined_df.get("처리기관", pd.Series(dtype="object")).fillna("").astype(str).map(lambda value: value.strip())
    agency_count = int((agencies != "").sum()) if agencies.empty else int(agencies[agencies != ""].nunique())
    return _sanitize_jsonable({
        "points": points,
        "meta": {
            "available_years": available_years,
            "current_year": year or "all",
            "selected_category": category,
            "dedupe_mode": _normalize_mode(mode),
            "total_reports": int(len(combined_df)),
            "geocoded_reports": int(len(geocoded_df)),
            "missing_reports": int(len(missing_df)),
            "address_groups": int(len(points)),
            "agency_count": agency_count,
        },
    })
