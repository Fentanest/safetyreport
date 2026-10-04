import math
import re
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from core.database import database
from core.utils.fallback import note_fallback
import settings.settings as app_settings
from services import duplicate_group_service
from services.report_query_service import _safe_read
from services import fine_estimate
from services.report_cache import cached


_STATS_COLUMNS = [
    "ID",
    "신고명",
    "신고번호",
    "신고일",
    "답변일",
    "처리기관",
    "처리기관코드",
    "담당자",
    "처리상태",
    "범칙금_과태료",
    "위반법규",
    "위반장소",
    "발생일자",
    "발생시각",
    "별점",
    "synced_at",
    "차량번호",
    "사진_첫촬영",
    "사진_끝촬영",
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
    # 통계 화면과 같은 조건(법규·발생일시·담당자)으로 지도를 좁힐 때 쓰는 열(2026-09-28)
    "위반법규",
    "발생일자",
    "발생시각",
    "담당자",
    "주소정규화",
    "행정구역",
    "위도",
    "경도",
    "처리기관",
    "처리기관코드",
]


def _extract_fine_amount(text) -> int:
    if not text:
        return 0
    text = str(text)
    if "과태료" not in text:
        return 0
    # '40.000원' 처럼 점을 천 단위 구분자로 쓴 답변도 있다. 모바일 `extractFineAmount` 와 같은 규칙.
    match = re.search(r"([\d,.]+)\s*원", text)
    if match:
        digits = re.sub(r"[,.]", "", match.group(1))
        return int(digits) if digits.isdigit() else 0
    return 0


def _round_half_up(value, digits: int) -> float:
    """통계 표시용 반올림. Python round()(짝수 쪽 반올림)와 달리 x.x5 를 올린다.

    float 의 정확한 값 기준이라 모바일 Dart `toStringAsFixed` 와 같은 결과가 나온다(통계 요약·기관표 전용).
    """
    return float(Decimal(value).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP))



def _is_fine_amount_unknown(text) -> bool:
    """과태료 처분인데 금액을 읽을 수 없는 경우(0원과 구분, statistics-spec S-05)."""
    return "과태료" in str(text or "") and _extract_fine_amount(text) == 0


def _count_fine_amount_unknown(group_df: pd.DataFrame) -> int:
    if '_metric_unknown_fine' in group_df:
        return int(group_df['_metric_unknown_fine'].sum())
    if "범칙금_과태료" not in group_df.columns:
        return 0
    return int(group_df["범칙금_과태료"].apply(_is_fine_amount_unknown).sum())


def _fine_amounts(frame):
    if '_metric_fine_amount' in frame:
        return frame['_metric_fine_amount']
    return frame['범칙금_과태료'].apply(_extract_fine_amount)


def _prepare_metrics(frame):
    """요청 소유 프레임에서 원문·통계 의미를 바꾸지 않고 파생값을 한 번 계산한다."""
    if frame.empty:
        return frame
    frame = frame.copy()
    text = frame.get('범칙금_과태료', pd.Series('', index=frame.index)).fillna('')
    frame['_metric_fine_amount'] = text.apply(_extract_fine_amount)
    frame['_metric_unknown_fine'] = text.astype(str).str.contains('과태료', regex=False) & (frame['_metric_fine_amount'] == 0)
    frame['_metric_rating'] = pd.to_numeric(frame.get('별점', pd.Series(None, index=frame.index)), errors='coerce')
    frame['_metric_status'] = _stats_status_series(frame)
    try:
        end = pd.to_datetime(frame['답변일'].astype(str).str.slice(0, 10), errors='coerce', format='%Y-%m-%d')
        start = pd.to_datetime(frame['신고일'].astype(str).str.slice(0, 10), errors='coerce', format='%Y-%m-%d')
        days = (end - start).dt.days
        frame['_metric_days'] = days.where((days >= 0) & frame['_metric_status'].isin(_OVERVIEW_COMPLETED_STATUSES))
    except (KeyError, TypeError, ValueError):
        frame['_metric_days'] = float('nan')
    for name, mask in _stats_row_disposition_masks(frame).items():
        frame['_metric_disposition_' + name] = mask
    frame['_metric_estimated_amount'] = 0
    frame['_metric_estimated_count'] = 0
    columns = [c for c in ('category','entry_value','신고명','위반법규','차량번호','사진_첫촬영','사진_끝촬영','발생시각') if c in frame]
    eligible = frame.loc[frame['_metric_unknown_fine'], columns].fillna('')
    if columns and not eligible.empty:
        combinations = eligible.drop_duplicates()
        estimates = {}
        for values in combinations.itertuples(index=False, name=None):
            result = fine_estimate.estimate(dict(zip(columns, values)))
            estimates[values] = result['amount'] if result is not None else None
        amounts = [estimates[values] for values in eligible.itertuples(index=False, name=None)]
        frame.loc[eligible.index, '_metric_estimated_amount'] = [amount or 0 for amount in amounts]
        frame.loc[eligible.index, '_metric_estimated_count'] = [int(amount is not None) for amount in amounts]
    return frame

_REPORT_FIELDS = [
    "ID",
    "신고번호",
    "신고명",
    "신고일",
    "답변일",
    "처리기관",
    "처리기관코드",
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
    "사진_첫촬영",
    "사진_끝촬영",
    "사진_촬영수",
]

_MAP_MISSING_COLUMNS = list(dict.fromkeys(_REPORT_FIELDS + [
    "주소정규화",
    "행정구역",
    "위도",
    "경도",
]))


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


def _build_stats_query(table_obj, filters=None, column_names=None):
    query = _build_select_for_columns(table_obj, column_names or _STATS_COLUMNS)
    if not filters:
        return query

    if filters.get("year") and filters["year"] not in ("all", "", None) and "답변일" in table_obj.c:
        query = query.where(table_obj.c["답변일"].startswith(filters["year"]))

    for prefix, column, width in [('reportDate', '신고일', 10), ('occurDate', '발생일자', 10),
                                  ('responseDate', '답변일', 10), ('occurTime', '발생시각', 5)]:
        if column not in table_obj.c:
            continue
        value = func.substr(table_obj.c[column], 1, width)
        # 앞자리 비교(substr)는 의미 그대로 두고, 같은 범위를 원래 열 비교로 한 번 더 건다 — 원래 열의 인덱스
        # (ix_merge_*_answer 등)로 범위를 좁힌 뒤 substr 로 정확히 거른다(기술일지 B-05). 결과 행은 같다.
        if filters.get(prefix + 'Start'):
            query = query.where(value >= filters[prefix + 'Start'])
            if width == 10 and len(str(filters[prefix + 'Start'])) == 10:
                query = query.where(table_obj.c[column] >= filters[prefix + 'Start'])
        if filters.get(prefix + 'End'):
            query = query.where(value <= filters[prefix + 'End'], func.length(value) == width)
            if width == 10 and len(str(filters[prefix + 'End'])) == 10:
                query = query.where(table_obj.c[column] < str(filters[prefix + 'End']) + "\U0010ffff")

    if filters.get("excludePolice") and "처리기관" in table_obj.c:
        # 처리기관이 비어 있는(NULL) 신고는 경찰이 아니다 — 목록 필터와 같게 남긴다(기술일지 A1-06)
        query = query.where(~func.coalesce(table_obj.c["처리기관"], "").contains("경찰"))
    if filters.get("onlyPolice") and "처리기관" in table_obj.c:
        query = query.where(table_obj.c["처리기관"].contains("경찰"))

    report_name = filters.get("reportName")
    if report_name and "신고명" in table_obj.c and _can_push_simple_text_query(report_name):
        query = query.where(table_obj.c["신고명"].contains(_text_or_empty(report_name)))

    location = filters.get("location")
    if location and "위반장소" in table_obj.c and _can_push_simple_text_query(location):
        query = query.where(table_obj.c["위반장소"].contains(_text_or_empty(location)))

    return query


def _read_stats_frame(conn, table_obj, filters=None, column_names=None):
    return pd.read_sql_query(_build_stats_query(table_obj, filters, column_names=column_names), conn)


def _normalize_mode(mode: str | None) -> str:
    normalized = _text_or_empty(mode).lower() or "raw"
    return normalized if normalized in {"raw", "canonical"} else "raw"


def _project_stats_frame(engine, df: pd.DataFrame, *, mode: str = "raw", members=None) -> pd.DataFrame:
    normalized_mode = _normalize_mode(mode)
    if normalized_mode == "raw" or df.empty or "ID" not in df.columns:
        return df
    if members is None:
        with engine.connect() as conn:
            _, members = duplicate_group_service.build_projection_map(conn)
    excluded = {rid for rid, meta in members.items() if not meta['is_representative']}
    projected = df[~df['ID'].astype(str).isin(excluded)].copy()
    if '감시목록' in df and members:
        watched = {members[rid]['group_id'] for rid in df.loc[df['감시목록'] == 'Y', 'ID'].astype(str) if rid in members}
        representatives = {rid for rid, meta in members.items() if meta['group_id'] in watched}
        projected.loc[projected['ID'].astype(str).isin(representatives), '감시목록'] = 'Y'
    return projected


def _canonical_query(table, query, mode):
    if _normalize_mode(mode) != 'canonical':
        return query
    member, group = database.duplicate_member_table, database.duplicate_group_table
    excluded = select(member.c.report_id).join(group, member.c.group_id == group.c.group_id).where(
        member.c.report_id == table.c.ID, member.c.is_representative != 1, group.c.status == 'confirmed_duplicate').exists()
    return query.where(~excluded)


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
        df_years = pd.read_sql_query(query, conn)
        if df_years.empty or "year" not in df_years.columns:
            continue
        years = df_years["year"].dropna().astype(str)
        available_years.update(years[years.str.match(r"^\d{4}$", na=False)].tolist())
    return sorted(available_years, reverse=True)


def get_last_sync_label(engine) -> str:
    """마지막 크롤링(동기화) 시각. 없으면 '기록 없음'.

    서버는 크롤링 종료 시 mysafety_sync_meta.last_sync 에 ISO8601 시각을 저장한다.
    모바일도 같은 키/형식으로 기록하므로 서버↔모바일 DB import 시 round-trip 으로 보존된다.
    """
    with engine.connect() as conn:
        return _last_sync_label(conn)


def _last_sync_label(conn) -> str:
    row = conn.execute(
        select(database.sync_meta_table.c.value).where(
            database.sync_meta_table.c.key == "last_sync"
        )
    ).fetchone()
    if row and row[0]:
        return datetime.fromisoformat(row[0]).strftime("%Y-%m-%d %H:%M:%S")
    return "기록 없음"


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
        # 세 분류 집계·최근 답변·감시목록·마지막 동기화를 한 읽기 스냅샷에서 읽는다. 사이에 신고가 분류를 옮겨도
        # 두 번 세거나 빠뜨리지 않는다(기술일지 A1-07, _load_stats_frames 와 같은 방식).
        conn.exec_driver_sql('BEGIN')
        last_crawl_time = _last_sync_label(conn)
        for table_obj in [database.merge_traffic_table, database.merge_parking_table, database.merge_other_table]:
            # Full-population SQL aggregation; no report bodies in the count path.
            query = select(table_obj.c['처리상태'], table_obj.c['범칙금_과태료'], func.count().label('_weight')).group_by(
                table_obj.c['처리상태'], table_obj.c['범칙금_과태료'])
            df = pd.read_sql_query(_canonical_query(table_obj, query, mode), conn)
            if df.empty:
                continue
            category = table_category_map.get(table_obj, "")
            df["category"] = category
            combined_frames.append(df)
            recent_query = select(table_obj).where(table_obj.c['답변일'] >= str(three_days_ago),
                table_obj.c['답변일'] < str(today + timedelta(days=1)))
            if app_settings.exclude_withdraw:
                recent_query = recent_query.where(table_obj.c['처리상태'] != '취하')
            recent_query = _canonical_query(table_obj, recent_query, mode).order_by(
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
        reject_count += int(weights[status_series.isin(["불수용", "기타"])].sum())
        partial_count += int(weights[status_series == "일부수용"].sum())
        processing_count += int(weights[status_series.isin(["처리중", "진행", "진행중", "검토중"])].sum())
        supplement_count += int(weights[status_series == "보완요청"].sum())
        completed_count += int(weights[status_series.isin(["수용", "불수용", "일부수용", "기타", "답변완료"])].sum())
        withdraw_count += int(weights[status_series == "취하"].sum())

        traffic_df = combined_df[combined_df["category"].fillna("").astype(str) == "traffic"] if "category" in combined_df.columns else pd.DataFrame()
        if not traffic_df.empty:
            fine_series = _text_series(traffic_df, "범칙금_과태료")
            traffic_status = _status_series(traffic_df)
            tw = traffic_df['_weight']
            t_fine_count += int(tw[fine_series.str.contains("과태료", na=False)].sum())
            t_penalty_count += int(tw[fine_series.str.contains("경고|범칙금", na=False)].sum())
            t_reject_count += int(tw[traffic_status.isin(["불수용", "기타"])].sum())
            t_unconfirmed_count += int(tw[(fine_series == "미확인") & (~traffic_status.isin(["불수용", "기타"]))].sum())

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


def _apply_stats_row_filters(df: pd.DataFrame, filters=None) -> pd.DataFrame:
    """`get_agency_stats` 와 `get_stats_overview` 가 공유하는 행 필터(연도·날짜·기관·텍스트)."""
    if filters:
        if filters.get("year") and filters["year"] not in ("all", "", None) and "답변일" in df.columns:
            df = df[df["답변일"].str.startswith(filters["year"], na=False)]
        if filters.get("reportName") and "신고명" in df.columns:
            df = _apply_text_query(df, "신고명", filters["reportName"])
        if filters.get("location") and "위반장소" in df.columns:
            df = _apply_text_query(df, "위반장소", filters["location"])
        for prefix, column, width in [('reportDate', '신고일', 10), ('occurDate', '발생일자', 10),
                                      ('responseDate', '답변일', 10), ('occurTime', '발생시각', 5)]:
            if column not in df:
                continue
            minimum, maximum = filters.get(prefix + 'Start'), filters.get(prefix + 'End')
            if not minimum and not maximum:
                continue
            value = df[column].fillna('').astype(str).str[:width]
            valid = value.str.len() == width
            if width == 10:
                parsed = pd.to_datetime(value, format='%Y-%m-%d', errors='coerce')
                valid &= parsed.notna()
            else:
                valid &= value.str.match(r'^([01][0-9]|2[0-3]):[0-5][0-9]$')
            if minimum:
                valid &= value >= minimum
            if maximum:
                valid &= value <= maximum
            df = df[valid]
        if filters.get("agency") and "처리기관" in df.columns:
            agency_query = filters["agency"]
            use_exact_agency = filters.get("agencyExact") and "&" not in agency_query and "," not in agency_query
            df = _apply_text_query(df, "처리기관", agency_query, exact=use_exact_agency)
        if filters.get("excludePolice") and "처리기관" in df.columns:
            df = df[~df["처리기관"].str.contains("경찰", na=False)]
        if filters.get("onlyPolice") and "처리기관" in df.columns:
            df = df[df["처리기관"].str.contains("경찰", na=False)]
    return df


def _apply_stats_law_filter(df: pd.DataFrame, filters=None) -> pd.DataFrame:
    if filters and filters.get("law") and "위반법규" in df.columns:
        if filters["law"] == "__없음__":
            df = df[df["위반법규"].fillna("").astype(str).str.strip() == ""]
        else:
            # S-09: 완전 일치(드롭다운 값 그대로). 부분 일치는 이름이 겹치는 다른 법규를 섞는다.
            df = df[df["위반법규"].fillna("").astype(str).str.strip() == str(filters["law"]).strip()]
    return df


def _load_stats_frames(engine, filters=None, mode: str = "canonical"):
    """DB 에서 3개 카테고리 프레임을 읽고 대표건 projection 까지 적용한다. (available_years, df_t, df_p, df_o)"""
    with engine.connect() as conn:
        conn.exec_driver_sql('BEGIN')
        available_years = _load_available_years(conn)
        df_t = _read_stats_frame(conn, database.merge_traffic_table, filters)
        df_p = _read_stats_frame(conn, database.merge_parking_table, filters)
        df_o = _read_stats_frame(conn, database.merge_other_table, filters)
        df_entry = pd.read_sql_query(select(database.entry_value_table.c.ID, database.entry_value_table.c.entry_value), conn)
        _, members = duplicate_group_service.build_projection_map(conn) if mode == 'canonical' else ({}, {})

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
    combined_df = _project_stats_frame(engine, combined_df, mode=mode, members=members)
    if not combined_df.empty and "처리기관" in combined_df.columns:
        combined_df = _apply_registry_agency_display(combined_df)
    if not combined_df.empty and "ID" in combined_df.columns:
        # 처분 분류(처분 대상 아님)와 추정 과태료 규칙이 신고 메뉴(entry_value)를 쓴다.
        entry_map = dict(zip(df_entry["ID"].astype(str), df_entry["entry_value"].fillna("").astype(str)))
        combined_df["entry_value"] = combined_df["ID"].astype(str).map(entry_map).fillna("")
    # 대표건 투영이 모든 행을 뺐어도(예: 비대표건만 걸리는 연도) 투영 결과로 바꾼다. 예전에는 빈 결과일 때 투영 전
    # 프레임을 그대로 돌려 제외해야 할 비대표건이 통계에 들어갔다(기술일지 A1-01).
    if "category" in combined_df.columns:
        df_t = combined_df[combined_df["category"] == "traffic"].copy()
        df_p = combined_df[combined_df["category"] == "parking"].copy()
        df_o = combined_df[combined_df["category"] == "other"].copy()
    else:
        df_t, df_p, df_o = (frame.iloc[0:0].copy() for frame in (df_t, df_p, df_o))
    return available_years, df_t, df_p, df_o


def _calc_avg_days_with_count(group_df):
    """(평균 처리일, 유효 표본 수). 표본 = 완료 신고 중 두 날짜가 모두 유효하고 차이 ≥ 0 인 행."""
    # S-10: 처리기간은 처리가 끝난 신고만. 이송 답변일이 붙은 처리중 신고·취하는 넣지 않는다.
    if '_metric_days' in group_df:
        days = group_df['_metric_days'].dropna()
        return (_round_half_up(float(days.mean()), 1) if len(days) > 0 else None), int(len(days))
    group_df = group_df[_stats_status_series(group_df).isin(_OVERVIEW_COMPLETED_STATUSES)]
    try:
        # 2026-09-24 사용자 결정: 처리일 = 답변일(날짜) − 신고일(날짜). 신고 시각은 버린다(12/30 23:40 → 1/2 = 3일).
        # 모바일 overview `_parse_overview_date` 와 같은 정의.
        d_end = pd.to_datetime(group_df["답변일"].astype(str).str.slice(0, 10), errors="coerce", format="%Y-%m-%d")
        d_start = pd.to_datetime(group_df["신고일"].astype(str).str.slice(0, 10), errors="coerce", format="%Y-%m-%d")
        days = (d_end - d_start).dt.days.dropna()
        days = days[days >= 0]
        return (_round_half_up(float(days.mean()), 1) if len(days) > 0 else None), int(len(days))
    except Exception as exc:
        note_fallback("report_stats.average_processing_days", exc)
        return None, 0


def _calc_avg_days(group_df):
    return _calc_avg_days_with_count(group_df)[0]


def _apply_registry_agency_display(df: pd.DataFrame) -> pd.DataFrame:
    """registry가 해석한 현행 기관 표시·통계 키로 푼다(2026-09-29 전체자료 색인).

    확인된 코드(현존·승계·별칭 유일)는 현행명 + agency_stat_key, (구) 분기는
    '(구)' 표시 + 별도 src 키, 미확정·열 없음·NaN이면 기존 normalize를
    그대로 쓰고 src 키를 단다. 원문 열은 건드리지 않으며 표시·키 열만 둔다.
    """
    if "처리기관" not in df.columns:
        return df
    from services import agency_registry
    has_code = "처리기관코드" in df.columns

    # Resolve each distinct code/name pair once, then map vectorially.
    names = df["처리기관"].fillna("").astype(str)
    codes = df["처리기관코드"].fillna("").astype(str) if has_code else pd.Series("", index=df.index)
    pairs = list(zip(names, codes))
    resolved = {pair: agency_registry.resolve_stats_agency(pair[1], pair[0]) for pair in set(pairs)}
    df["처리기관"] = [resolved[pair][0] for pair in pairs]
    df["_agency_key"] = [resolved[pair][1] for pair in pairs]
    return df


def _calc_avg_rating(group_df):
    if "별점" not in group_df.columns:
        return None, 0
    ratings = (group_df['_metric_rating'] if '_metric_rating' in group_df else pd.to_numeric(group_df["별점"], errors="coerce")).dropna()
    ratings = ratings[(ratings >= 1) & (ratings <= 5)]
    if len(ratings) == 0:
        return None, 0
    return _round_half_up(float(ratings.mean()), 2), int(len(ratings))


def _build_stats_tables(df: pd.DataFrame, category: str | None = None):
    """필터가 끝난 한 카테고리 프레임 → (기관별, 담당자별, 법규별) 행 목록. 모바일 `LocalDbService._buildCategory` 와 같은 규칙."""
    # S-10: 표 포함 여부는 처리상태가 아니라 기관·담당자 값이 있는지로 정한다.
    # 배정된 처리중 신고도 기관/담당자 행에 들어가고 `in_progress` 로 따로 센다.
    df["처리기관"] = df.get("처리기관", pd.Series("", index=df.index, dtype="object")).fillna("").astype(str).str.strip()
    df["담당자"] = df.get("담당자", pd.Series("", index=df.index, dtype="object")).fillna("").astype(str).str.strip()
    df["범칙금_과태료"] = df.get("범칙금_과태료", pd.Series("", index=df.index, dtype="object")).fillna("")
    # 표시 미적용 경로: 원문 표시 그대로 묶는 src 키.
    if "_agency_key" not in df.columns:
        df["_agency_key"] = "src:-:" + df["처리기관"]
    if category and "category" not in df.columns:
        df["category"] = category
    # 2026-09-28 사용자 결정: 기관·담당자·법규 표는 답변이 완료된 신고만(처리중·보완요청·이송·취하는 넣지 않는다).
    # 처리중은 답변이 없어 처리기관·담당자도 없는 게 정상이다(실제 DB 처리중 83건 전부 기관 없음). 모바일 buildStatsCategory 와 같은 규칙.
    completed = _stats_status_series(df).isin(_OVERVIEW_COMPLETED_STATUSES)
    df = df[completed].copy()
    df_agency = df[df["처리기관"] != ""]
    df_person = df_agency[~df_agency["담당자"].isin(_UNASSIGNED_PERSON_VALUES)]

    def _row_metrics(group):
        total = len(group)
        counts = _stats_row_disposition_counts(group)
        avg_rating, rating_count = _calc_avg_rating(group)

        def _pct(key):
            return _round_half_up((counts[key] / total) * 100, 1) if total > 0 else 0

        avg_days, avg_days_count = _calc_avg_days_with_count(group)
        return {
            "total": total,
            "avg_days": avg_days,
            # 2026-09-28 추가: 평균 처리기간 표본 수. 표 합계 행이 행 평균을 이 수로 가중해 전체 평균을 낸다(행 수·총 건수 가중 아님).
            "avg_days_count": avg_days_count,
            "total_fine_amount": int(_fine_amounts(group).sum()),
            "fine_amount_unknown": _count_fine_amount_unknown(group),
            **_estimated_fine_totals(group),
            **{key: counts[key] for key in counts},
            **{f"{key}_pct": _pct(key) for key in counts},
            "avg_rating": avg_rating,
            "rating_count": rating_count,
        }

    stats_person = [
        {"agency": display, "agency_key": key, "person": person, **_row_metrics(group)}
        for (key, display, person), group in (
            df_person.assign(_person=df_person["담당자"])
            .groupby(["_agency_key", "처리기관", "_person"], sort=False)
        )
    ]
    stats_agency = [
        {"agency": display, "agency_key": key, **_row_metrics(group)}
        for (key, display), group in (
            df_agency.groupby(["_agency_key", "처리기관"], sort=False)
        )
    ]
    stats_person.sort(key=lambda r: (r["agency"], r["person"], r["agency_key"]))
    stats_agency.sort(key=lambda r: (r["agency"], r["agency_key"]))

    stats_law = []
    if "위반법규" in df.columns:
        df_law = df.copy()
        df_law["위반법규"] = df_law["위반법규"].fillna("").astype(str)
        df_law = df_law[df_law["위반법규"].str.strip() != ""]
        stats_law = [
            {"law": law, **_row_metrics(group)}
            for law, group in df_law.groupby("위반법규")
        ]
    return stats_agency, stats_person, stats_law


def get_agency_stats(engine, filters=None, mode: str = "canonical"):
    available_years, df_t, df_p, df_o = _load_stats_frames(engine, filters, mode)
    # 웹 get_stats_page 와 같이 날짜·처분·금액 파생값을 한 번만 계산한다(모바일 /api/v1/stats, 기술일지 B-08).
    # 결과는 같다(test_stats_page_matches_separate_calls).
    df_t, df_p, df_o = map(_prepare_metrics, (df_t, df_p, df_o))
    return _compute_agency_stats(available_years, df_t, df_p, df_o, filters, mode)


@cached
def get_stats_page(engine, filters=None, mode: str = "canonical"):
    """웹 통계 화면: 표(`get_agency_stats`)와 요약·차트(`get_stats_overview`)를 한 번 읽은 같은 프레임으로 만든다.

    두 결과는 각 공개 함수와 같다(테스트 `test_stats_page_matches_separate_calls`). 표 계산이 프레임 열을 고치므로 복사본을 넘긴다.
    """
    available_years, df_t, df_p, df_o = _load_stats_frames(engine, filters, mode)
    df_t, df_p, df_o = map(_prepare_metrics, (df_t, df_p, df_o))
    overview = _compute_stats_overview(available_years, df_t, df_p, df_o, filters, mode)
    records = _compute_agency_stats(available_years, df_t.copy(), df_p.copy(), df_o.copy(), filters, mode)
    return records, overview


def _compute_agency_stats(available_years, df_t, df_p, df_o, filters=None, mode: str = "canonical"):
    def calc_stats(df, category):
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

        df = _apply_stats_row_filters(df, filters)

        df = _exclude_withdraw_rows(df)

        if "위반법규" in df.columns:
            laws = df["위반법규"].dropna().astype(str)
            nonempty_laws = laws[laws.str.strip() != ""]
            available_laws = sorted(nonempty_laws.unique().tolist())
            has_empty_law = bool((df["위반법규"].fillna("").astype(str).str.strip() == "").any())
        else:
            available_laws = []
            has_empty_law = False

        df = _apply_stats_law_filter(df, filters)

        if df.empty:
            # 법규 필터로 이 카테고리가 비어도 법규 선택지는 유지한다(모바일 computeStats 와 같음, 2026-09-25 동등성 검사)
            return _sanitize_jsonable({
                **empty_payload,
                **_estimated_fine_totals(df),
                "available_laws": available_laws,
                "has_empty_law": has_empty_law,
            })

        stats_agency, stats_person, stats_law = _build_stats_tables(df, category)

        category_total_fine = int(_fine_amounts(df).sum())
        category_estimates = _estimated_fine_totals(df)

        def _sort(items, key="total"):
            if not items:
                return []
            frame = pd.DataFrame(items)
            # 동점 결정성: total 내림차순, 표시명·키 오름차순(모바일과 같은 규칙).
            by = [key] + [c for c in ("agency", "person", "agency_key", "law", "month") if c in frame.columns]
            ascending = [False] + [True] * (len(by) - 1)
            return frame.sort_values(by=by, ascending=ascending).to_dict("records")

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
            **category_estimates,
            "estimate_rule_version": fine_estimate.RULE_VERSION,
            "available_laws": available_laws,
            "has_empty_law": has_empty_law,
        })

    res_t = calc_stats(df_t, "traffic")
    res_p = calc_stats(df_p, "parking")
    res_o = calc_stats(df_o, "other")
    return _sanitize_jsonable({
        "traffic": res_t,
        "parking": res_p,
        "other": res_o,
        "available_years": available_years,
        "traffic_total_fine": int(_fine_amounts(df_t).sum()) if not df_t.empty else 0,
        "dedupe_mode": _normalize_mode(mode),
    })


_OVERVIEW_COMPLETED_STATUSES = {"수용", "불수용", "일부수용", "기타", "답변완료"}
_OVERVIEW_PROCESSING_STATUSES = {"처리중", "진행", "진행중", "검토중"}


def _parse_overview_date(value):
    text = _text_or_empty(value)
    if len(text) < 10:
        return None
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _summarize_overview_frame(df: pd.DataFrame) -> dict:
    """통계 요약 카드/월별 추이용 집계. 모바일 Standalone `LocalDbService.computeStatsOverview` 와 같은 정의."""
    total = int(len(df))
    statuses = df["처리상태"].fillna("").astype(str).str.strip() if "처리상태" in df.columns else pd.Series(dtype=str)
    report_dates = [_parse_overview_date(v) for v in (df["신고일"] if "신고일" in df.columns else [])]
    answer_dates = [_parse_overview_date(v) for v in (df["답변일"] if "답변일" in df.columns else [])]
    if len(report_dates) < total:
        report_dates += [None] * (total - len(report_dates))
    if len(answer_dates) < total:
        answer_dates += [None] * (total - len(answer_dates))

    day_samples = []
    reversed_count = 0
    reported_by_month: dict[str, int] = {}
    answered_by_month: dict[str, int] = {}
    completed_flags = [status in _OVERVIEW_COMPLETED_STATUSES for status in statuses]
    if len(completed_flags) < total:
        completed_flags += [False] * (total - len(completed_flags))
    for reported, answered, is_completed in zip(report_dates, answer_dates, completed_flags):
        if reported is not None:
            key = reported.strftime("%Y-%m")
            reported_by_month[key] = reported_by_month.get(key, 0) + 1
        if answered is not None:
            key = answered.strftime("%Y-%m")
            answered_by_month[key] = answered_by_month.get(key, 0) + 1
        # S-10: 평균 처리기간은 완료 신고만(기관표 `_calc_avg_days` 와 같은 기준).
        if is_completed and reported is not None and answered is not None:
            days = (answered - reported).days
            if days < 0:
                reversed_count += 1
            else:
                day_samples.append(days)

    def _count(predicate) -> int:
        return int(sum(1 for status in statuses if predicate(status)))

    # 2026-09-28 통계 개편(추가 필드). 모바일 `LocalDbService.summarizeOverviewRows` 와 같은 정의.
    fine_texts = (
        df["범칙금_과태료"].fillna("").astype(str).tolist() if "범칙금_과태료" in df.columns else [""] * total
    )
    # 답변월 기준 과태료 건수: 월별 처리 추이의 보조 계열(같은 답변일 기준). 과태료 = 처분 문구에 '과태료'.
    answered_fine_by_month: dict[str, int] = {}
    for answered, fine_text in zip(answer_dates, fine_texts):
        if answered is not None and "과태료" in fine_text:
            key = answered.strftime("%Y-%m")
            answered_fine_by_month[key] = answered_fine_by_month.get(key, 0) + 1

    return {
        "total": total,
        "completed": _count(lambda s: s in _OVERVIEW_COMPLETED_STATUSES),
        "accept": _count(lambda s: s == "수용"),
        "partial": _count(lambda s: s == "일부수용"),
        "reject": _count(lambda s: s in {"불수용", "기타"}),
        "supplement": _count(lambda s: s == "보완요청"),
        "processing": _count(lambda s: s in _OVERVIEW_PROCESSING_STATUSES),
        "withdraw": _count(lambda s: s == "취하"),
        "avg_days": _round_half_up(sum(day_samples) / len(day_samples), 1) if day_samples else None,
        "avg_days_count": len(day_samples),
        "reversed_date_count": reversed_count,
        "undated_report_count": int(sum(1 for d in report_dates if d is None)),
        "monthly_reported": [{"month": k, "count": v} for k, v in sorted(reported_by_month.items())],
        "monthly_answered": [{"month": k, "count": v} for k, v in sorted(answered_by_month.items())],
        "monthly_answered_fine": [{"month": k, "count": v} for k, v in sorted(answered_fine_by_month.items())],
        "disposition": _overview_disposition(df),
        "fine_amount": _overview_fine_amount(df),
        "report_types": _overview_report_types(df),
        "violation_laws": _overview_violation_laws(df),
        "result_distribution": {
            "accept": _count(lambda s: s == "수용"),
            "partial": _count(lambda s: s == "일부수용"),
            "reject": _count(lambda s: s in {"불수용", "기타"}),
            "unknown": _count(lambda s: s == "답변완료"),
        },
    }


def _overview_disposition(df: pd.DataFrame) -> dict[str, int]:
    """카테고리 전체(기관 유무와 무관) 처분 분류. 통계표 행과 같은 규칙(`_stats_row_disposition_counts`).

    과태료·경고/범칙금·불수용/기타는 한 신고에 겹칠 수 있다(문구에 과태료와 범칙금이 함께 있는 등).
    `overlap` = 세 항목 합 − 셋 중 하나 이상에 해당하는 신고 수. 0 이 아니면 세 항목 합이 신고 수보다 크다.
    """
    keys = ("fines", "warnings", "rejects", "unconfirmed", "in_progress", "disposition_unknown", "no_penalty", "unclassified")
    if df.empty:
        return {**{key: 0 for key in keys}, "overlap": 0}
    counts = _stats_row_disposition_counts(df)
    fine_series = df.get("범칙금_과태료", pd.Series("", index=df.index, dtype="object")).fillna("").astype(str)
    status_series = _stats_status_series(df)
    decided = int((
        fine_series.str.contains("과태료", na=False)
        | fine_series.str.contains("경고|범칙금", na=False)
        | status_series.isin(["불수용", "기타"])
    ).sum())
    result = {key: int(counts.get(key, 0)) for key in keys}
    result["overlap"] = result["fines"] + result["warnings"] + result["rejects"] - decided
    return result


def _overview_fine_amount(df: pd.DataFrame) -> dict[str, int]:
    """확정(원문 금액)과 추정(법정 최저 기준) 과태료를 따로 센다. 둘을 더한 값은 내려주지 않는다(PROJECT_RULES §3-2)."""
    if df.empty or "범칙금_과태료" not in df.columns:
        return {"confirmed_amount": 0, "confirmed_count": 0, "unknown_count": 0, "estimated_amount": 0, "estimated_count": 0}
    fine_series = df["범칙금_과태료"].fillna("")
    amounts = _fine_amounts(df)
    has_fine = fine_series.astype(str).str.contains("과태료", na=False)
    estimates = _estimated_fine_totals(df)
    return {
        "confirmed_amount": int(amounts.sum()),
        # 확정 건수 = 금액을 읽은 과태료 건(모바일 기관 카드 `과태료 − 금액 미확인` 과 같은 값)
        "confirmed_count": int((has_fine & (amounts > 0)).sum()),
        "unknown_count": _count_fine_amount_unknown(df),
        "estimated_amount": estimates["estimated_fine_amount"],
        "estimated_count": estimates["estimated_fine_count"],
    }


def _overview_report_types(df: pd.DataFrame) -> list[dict]:
    """위반 유형(신고명 원문, 앞뒤 공백 제거) 건수. 전체 목록을 건수 내림차순·이름 오름차순으로. 빈 신고명은 name ''."""
    if df.empty or "신고명" not in df.columns:
        return []
    names = df["신고명"].fillna("").astype(str).str.strip()
    counts: dict[str, int] = {}
    for name in names:
        counts[name] = counts.get(name, 0) + 1
    return [{"name": name, "count": count} for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]


def _overview_violation_laws(df: pd.DataFrame) -> list[dict]:
    """One report per exact stored normalized law string (including multi-law strings).

    This preserves the existing exact law filter and list drilldown population.
    Missing law is its own bucket; report titles never supply law information.
    """
    if df.empty:
        return []
    laws = df.get("위반법규", pd.Series("", index=df.index)).fillna("").astype(str).str.strip()
    return [{"name": name, "filter": name or "__없음__", "count": int(count)}
            for name, count in sorted(laws.value_counts().items(), key=lambda item: (-item[1], item[0]))]


def get_stats_overview(engine, filters=None, mode: str = "canonical"):
    """통계 화면 요약 카드 + 월별 추이(신고일/답변일 기준 각각).

    `get_agency_stats` 와 같은 행(필터·대표건·취하 제외·법규)을 쓰므로 카드와 기관표가 같은 데이터 집합을 본다.
    평균 처리일은 기관 평균을 합치지 않고 원자료에서 직접 계산하며 표본 수(`avg_days_count`)를 함께 내려준다.
    """
    available_years, df_t, df_p, df_o = _load_stats_frames(engine, filters, mode)
    return _compute_stats_overview(available_years, df_t, df_p, df_o, filters, mode)


def _compute_stats_overview(available_years, df_t, df_p, df_o, filters=None, mode: str = "canonical"):
    def _filtered(df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        df = _apply_stats_row_filters(df, filters)
        df = _exclude_withdraw_rows(df)
        return _apply_stats_law_filter(df, filters)

    frames = {"traffic": _filtered(df_t), "parking": _filtered(df_p), "other": _filtered(df_o)}
    non_empty = [frame for frame in frames.values() if not frame.empty]
    combined = pd.concat(non_empty, ignore_index=True) if non_empty else pd.DataFrame()
    payload = {key: _summarize_overview_frame(frame) for key, frame in frames.items()}
    payload["all"] = _summarize_overview_frame(combined)
    payload.update({
        "available_years": available_years,
        "year_basis": "답변일",
        "exclude_withdraw": bool(app_settings.exclude_withdraw),
        "dedupe_mode": _normalize_mode(mode),
    })
    return _sanitize_jsonable(payload)


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


_UNASSIGNED_PERSON_VALUES = {"", "미지정"}


def _stats_status_series(group_df: pd.DataFrame) -> pd.Series:
    if '_metric_status' in group_df:
        return group_df['_metric_status']
    return group_df.get("처리상태", pd.Series(dtype="object", index=group_df.index)).fillna("").astype(str).str.strip()


def _stats_row_disposition_counts(group_df: pd.DataFrame) -> dict[str, int]:
    """통계표(기관/담당자/법규) 행 처분 분류. S-10: 처리중은 '미분류'에 섞지 않고 `in_progress` 로 뺀다.

    처리중 = 완료(수용·일부수용·불수용·기타·답변완료)도 취하도 아닌 상태(처리중·진행·검토중·보완요청·이송·빈 값 등).
    모바일 Standalone `_AgencyAgg` 와 같은 정의. 대시보드용 `_disposition_counts` 는 바꾸지 않는다.
    """
    if '_metric_disposition_fines' in group_df:
        return {name: int(group_df['_metric_disposition_' + name].sum()) for name in (
            'fines', 'warnings', 'rejects', 'unconfirmed', 'in_progress',
            'disposition_unknown', 'no_penalty', 'unclassified')}
    return {name: int(mask.sum()) for name, mask in _stats_row_disposition_masks(group_df).items()}


def _stats_row_disposition_masks(group_df):
    fine_series = group_df.get("범칙금_과태료", pd.Series(dtype="object", index=group_df.index)).fillna("").astype(str)
    raw_status = group_df.get('처리상태', pd.Series(dtype='object', index=group_df.index)).fillna('').astype(str)
    status_series = _stats_status_series(group_df)
    decided = (
        fine_series.str.contains("과태료", na=False)
        | fine_series.str.contains("경고|범칙금", na=False)
        | status_series.isin(["불수용", "기타"])
    )
    in_progress = ~decided & ~status_series.isin(_OVERVIEW_COMPLETED_STATUSES) & (status_series != "취하")
    unconfirmed = ~decided & ~in_progress
    # 2026-09-24 (b) 열 분리 — `unconfirmed` 는 그대로 두고(모바일 API 호환) 하위 분류를 추가한다.
    #   disposition_unknown: 과태료 대상 신고인데 파서가 처분을 못 읽은 건(`범칙금_과태료` == '미확인')
    #   no_penalty: 과태료 대상이 아닌 유형(시설물 등)의 완료 신고
    #   unclassified: 나머지(과태료 대상 유형인데 처분 문구 없는 완료, 취하 등)
    eligible = _penalty_eligible_mask(group_df)
    # 과태료 미확인(2026-09-28 이름 변경, 필드명은 그대로): 처분 칸이 '미확인'이거나, 이미 저장된 주정차·버스전용차로·쓰레기 메뉴의
    # 일부수용 + 처분 없음(파서가 예전엔 비워 뒀다 — 지금은 '미확인'을 넣는다).
    disposition_unknown = unconfirmed & (
        (fine_series.str.strip() == "미확인")
        | ((status_series == "일부수용") & (fine_series.str.strip() == "") & _partial_unknown_menu_mask(group_df))
    )
    no_penalty = unconfirmed & ~disposition_unknown & ~eligible & status_series.isin(_OVERVIEW_COMPLETED_STATUSES)
    return {'fines': fine_series.str.contains('과태료', na=False),
            'warnings': fine_series.str.contains('경고|범칙금', na=False),
            'rejects': raw_status.isin(['불수용', '기타']),
            'in_progress': in_progress, 'unconfirmed': unconfirmed,
            'disposition_unknown': disposition_unknown, 'no_penalty': no_penalty,
            'unclassified': unconfirmed & ~disposition_unknown & ~no_penalty}


def _partial_unknown_menu_mask(group_df: pd.DataFrame) -> pd.Series:
    """주정차·버스전용차로·쓰레기 메뉴(파서 `_PARTIAL_UNKNOWN_MENUS` 와 같은 메뉴, 주정차 분류 포함)."""
    index = group_df.index
    category = group_df.get("category", pd.Series("", index=index, dtype="object")).fillna("").astype(str)
    entry = group_df.get("entry_value", pd.Series("", index=index, dtype="object")).fillna("").astype(str)
    return (
        (category == "parking")
        | entry.str.contains("불법주정차신고", regex=False)
        | entry.str.contains("버스전용차로 위반", regex=False)
        | entry.str.contains("쓰레기, 폐기물", regex=False)
    )


def _penalty_eligible_mask(group_df: pd.DataFrame) -> pd.Series:
    """과태료가 붙을 수 있는 유형: 교통위반, 불법주정차, 쓰레기·폐기물 메뉴. 그 밖(시설물 등)은 처분 대상 아님."""
    index = group_df.index
    category = group_df.get("category", pd.Series("", index=index, dtype="object")).fillna("").astype(str)
    entry = group_df.get("entry_value", pd.Series("", index=index, dtype="object")).fillna("").astype(str)
    return (
        category.isin(["traffic", "parking"])
        | entry.str.contains("자동차·교통위반", regex=False)
        | entry.str.contains("불법주정차신고", regex=False)
        | entry.str.contains("쓰레기, 폐기물", regex=False)
    )


def _estimated_fine_totals(group_df: pd.DataFrame) -> dict[str, int]:
    """금액 없는 과태료 행의 법정 최저 기준 추정 합계. 확정 금액(`total_fine_amount`)과 섞지 않는다."""
    if '_metric_estimated_amount' in group_df:
        return {'estimated_fine_amount': int(group_df['_metric_estimated_amount'].sum()),
                'estimated_fine_count': int(group_df['_metric_estimated_count'].sum())}
    if group_df.empty or "범칙금_과태료" not in group_df.columns:
        return {"estimated_fine_amount": 0, "estimated_fine_count": 0}
    amount = 0
    count = 0
    unknown = group_df['범칙금_과태료'].apply(_is_fine_amount_unknown)
    columns = [c for c in ('category','entry_value','신고명','위반법규','차량번호','사진_첫촬영','사진_끝촬영','발생시각') if c in group_df]
    if not unknown.any() or not columns:
        return {"estimated_fine_amount": 0, "estimated_fine_count": 0}
    eligible = group_df.loc[unknown, columns].fillna('')
    combinations = eligible.groupby(columns, dropna=False, sort=False).size().reset_index(name='_weight')
    for record in combinations.to_dict(orient="records"):
        result = fine_estimate.estimate(record)
        if result is None:
            continue
        amount += result["amount"] * record['_weight']
        count += record['_weight']
    return {"estimated_fine_amount": int(amount), "estimated_fine_count": int(count)}


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
    frame = group_df.copy()
    frame["처리기관"] = frame["처리기관"].fillna("").astype(str).map(lambda value: value.strip())
    frame = frame[frame["처리기관"] != ""]
    if frame.empty:
        return []
    if "_agency_key" not in frame.columns:
        frame["_agency_key"] = "src:-:" + frame["처리기관"]
    grouped = frame.groupby(["_agency_key", "처리기관"], sort=False).size().reset_index(name="count")
    total = int(len(frame))
    results = []
    for _, row in grouped.iterrows():
        results.append({
            "name": str(row["처리기관"]),
            "agency_key": str(row["_agency_key"]),
            "count": int(row["count"]),
            "pct": round((int(row["count"]) / total) * 100, 1) if total > 0 else 0,
        })
    results.sort(key=lambda item: (-item["count"], item["name"], item["agency_key"]))
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


def _normalize_map_category_value(category: str | None) -> str:
    normalized = (category or "all").strip().lower()
    return normalized if normalized in {"all", "traffic", "parking", "other"} else "all"


def _is_finite_number(value) -> bool:
    if value is None or pd.isna(value):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


_MAP_TARGET_KEYS = ("targetAgency", "targetPerson", "targetAgencyKey", "completedOnly")


@cached
def _load_map_records_frame(
    engine,
    *,
    year: str | None = None,
    category: str = "all",
    mode: str = "canonical",
    column_names: list[str] | None = None,
    filters: dict | None = None,
):
    """지도용 행. `filters` 는 통계 화면과 같은 조건(법규·상세 검색)이며 같은 순서로 적용한다(2026-09-28 추가, 없으면 예전과 같음).

    `targetAgency`/`targetPerson` 은 통계 상세 패널의 '지도에서 보기' 대상이다. 통계표의 기관명은 경찰서 정규화 뒤 이름이므로
    정규화 뒤 정확히 일치로 거른다(목록 `/data` 의 agencyExact 와 같은 순서).
    """
    stats_filters = {key: value for key, value in (filters or {}).items() if key not in _MAP_TARGET_KEYS and value not in (None, "", False)}
    target_agency = _text_or_empty((filters or {}).get("targetAgency"))
    target_agency_key = _text_or_empty((filters or {}).get("targetAgencyKey"))
    target_person = _text_or_empty((filters or {}).get("targetPerson"))
    completed_only = (filters or {}).get("completedOnly") is True
    filters = dict(stats_filters)
    if year and year not in ("all", "", None):
        filters["year"] = str(year)

    normalized_category = _normalize_map_category_value(category)
    selected_columns = list(column_names or _MAP_COLUMNS)

    with engine.connect() as conn:
        conn.exec_driver_sql('BEGIN')
        available_years = _load_available_years(conn)
        frames = []
        for table_obj, table_category in [
            (database.merge_traffic_table, "traffic"),
            (database.merge_parking_table, "parking"),
            (database.merge_other_table, "other"),
        ]:
            if normalized_category != "all" and normalized_category != table_category:
                continue
            df = _read_stats_frame(conn, table_obj, filters, column_names=selected_columns)
            if not df.empty:
                df["category"] = table_category
                frames.append(df)
        _, members = duplicate_group_service.build_projection_map(conn) if mode == 'canonical' else ({}, {})

    combined_df = (
        pd.concat(frames, ignore_index=True)
        if frames
        else pd.DataFrame(columns=selected_columns + ["category"])
    )
    for column in selected_columns:
        if column not in combined_df.columns:
            combined_df[column] = ""
    if "category" not in combined_df.columns:
        combined_df["category"] = normalized_category if normalized_category != "all" else "other"

    combined_df = _ensure_id_column(combined_df)
    combined_df = _project_stats_frame(engine, combined_df, mode=mode, members=members)
    if not combined_df.empty and "처리기관" in combined_df.columns:
        combined_df = _apply_registry_agency_display(combined_df)
    if stats_filters and not combined_df.empty:
        combined_df = _apply_stats_row_filters(combined_df, filters)
    combined_df = _exclude_withdraw_rows(combined_df)
    if stats_filters and not combined_df.empty:
        combined_df = _apply_stats_law_filter(combined_df, filters)

    if combined_df.empty:
        return normalized_category, available_years, combined_df

    if "category" not in combined_df.columns:
        combined_df["category"] = normalized_category if normalized_category != "all" else "other"

    if completed_only:
        combined_df = combined_df[_stats_status_series(combined_df).isin(_OVERVIEW_COMPLETED_STATUSES)].copy()
    if target_agency_key:
        combined_df = combined_df[combined_df["_agency_key"] == target_agency_key].copy()
    elif target_agency and "처리기관" in combined_df.columns:
        combined_df = combined_df[combined_df["처리기관"].fillna("").astype(str).str.strip() == target_agency].copy()
    if target_person and "담당자" in combined_df.columns:
        combined_df = combined_df[combined_df["담당자"].fillna("").astype(str).str.strip() == target_person].copy()

    combined_df["위반장소"] = combined_df.get("위반장소", pd.Series(dtype="object")).fillna("").astype(str)
    combined_df["주소정규화"] = combined_df.get("주소정규화", pd.Series(dtype="object")).fillna("").astype(str)
    combined_df["행정구역"] = combined_df.get("행정구역", pd.Series(dtype="object")).fillna("").astype(str)
    combined_df["위도"] = pd.to_numeric(combined_df.get("위도"), errors="coerce")
    combined_df["경도"] = pd.to_numeric(combined_df.get("경도"), errors="coerce")
    combined_df["유효좌표"] = combined_df["위도"].apply(_is_finite_number) & combined_df["경도"].apply(_is_finite_number)
    combined_df["주소키"] = combined_df["주소정규화"].str.strip()
    combined_df.loc[combined_df["주소키"] == "", "주소키"] = combined_df["위반장소"].str.strip()
    return normalized_category, available_years, combined_df


@cached
def get_report_map_stats(engine, *, year: str | None = None, category: str = "all", mode: str = "canonical", filters: dict | None = None, max_points: int | None = None, bounds: tuple | None = None, zoom: int = 7):
    category, available_years, combined_df = _load_map_records_frame(
        engine,
        year=year,
        category=category,
        mode=mode,
        column_names=_MAP_COLUMNS,
        filters=filters,
    )

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
                "viewport_reports": 0,
                "rendered_points": 0,
                "point_budget": max_points,
                "clustered": False,
            },
        })

    geocoded_df = combined_df[combined_df["유효좌표"]].copy()
    missing_df = combined_df[
        (combined_df["위반장소"].str.strip() != "")
        & ~combined_df["유효좌표"]
    ].copy()

    visible = geocoded_df
    if bounds is not None:
        south, west, north, east = bounds
        visible = visible[visible['위도'].between(south, north) & visible['경도'].between(west, east)].copy()
    points = _aggregate_map_points(visible, max_points=max_points, zoom=zoom)

    points.sort(key=lambda item: item["total"], reverse=True)
    if "_agency_key" in combined_df.columns:
        key_series = combined_df["_agency_key"].fillna("").astype(str).map(lambda value: value.strip())
    else:
        key_series = combined_df.get("처리기관", pd.Series(dtype="object")).fillna("").astype(str).map(lambda value: "src:-:" + value.strip())
    agency_count = int(key_series[key_series != ""].nunique())
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
            "address_groups": int(len(geocoded_df[['위도','경도','주소키']].drop_duplicates())),
            "agency_count": agency_count,
            "viewport_reports": int(len(visible)),
            "rendered_points": len(points),
            "point_budget": max_points,
            "clustered": any(p.get("cluster") for p in points),
        },
    })


def _aggregate_map_points(frame, *, max_points=None, zoom=7):
    if frame.empty:
        return []
    frame = frame.copy()
    group_cols = ["위도", "경도", "주소키"]
    clustered = False
    if max_points and len(frame[group_cols].drop_duplicates()) > max_points:
        # Zoom-sensitive spatial cells. Every report contributes to its cell;
        # centroids are display coordinates, never written to stored raw coordinates.
        step = 360 / (2 ** (max(0, min(19, zoom)) + 3))
        while True:
            frame['_lat_cell'] = (frame['위도'] / step).astype('int64')
            frame['_lng_cell'] = (frame['경도'] / step).astype('int64')
            group_cols = ['_lat_cell', '_lng_cell']
            if len(frame[group_cols].drop_duplicates()) <= max_points:
                break
            step *= 2
        clustered = True
    frame['_group'] = frame.groupby(group_cols, dropna=False, sort=False).ngroup()
    grouped = frame.groupby('_group', sort=False)
    coordinate_agg = 'mean' if clustered else 'first'
    base = grouped.agg(lat=('위도',coordinate_agg), lng=('경도',coordinate_agg), total=('ID','size'),
                       address=('위반장소','first'), region=('행정구역','first'))
    statuses = frame['처리상태'].fillna('').astype(str)
    fine = frame['범칙금_과태료'].fillna('').astype(str)
    fine_mask = fine.str.contains('과태료', regex=False)
    warning = fine.str.contains('경고|범칙금')
    reject = statuses.isin(['불수용','기타'])
    frame['_fine'] = fine_mask.astype(int)
    frame['_warning'] = warning.astype(int)
    frame['_reject'] = reject.astype(int)
    frame['_unknown'] = (~(fine_mask | warning | reject)).astype(int)
    dispositions = grouped[['_fine','_warning','_reject','_unknown']].sum()
    status_counts = frame.groupby(['_group','처리상태'], dropna=False).size()
    category_counts = frame.groupby(['_group','category']).size()
    agency_counts = frame.groupby(['_group','_agency_key','처리기관'], dropna=False).size() if '_agency_key' in frame else None
    status_by = {}
    for (gid, status), count in status_counts.items():
        label = '처리중' if pd.isna(status) or status in ('','진행','진행중','검토중') else str(status)
        bucket = status_by.setdefault(gid, {})
        bucket[label] = bucket.get(label, 0) + int(count)
    categories_by = {}
    for (gid, cat), count in category_counts.items(): categories_by.setdefault(gid, {})[cat] = int(count)
    agencies_by = {}
    if agency_counts is not None:
        for (gid, key, name), count in agency_counts.items():
            if name: agencies_by.setdefault(gid, []).append({'name':str(name),'agency_key':str(key),'count':int(count)})
    points = []
    for gid, row in base.iterrows():
        total = int(row.total)
        agency = sorted(agencies_by.get(gid, []), key=lambda a: (-a['count'],a['name'],a['agency_key']))
        for a in agency: a['pct'] = round(a['count'] / sum(i['count'] for i in agency) * 100, 1)
        point = dict(lat=float(row.lat), lng=float(row.lng), total=total,
                     address='이 영역의 신고' if clustered else str(row.address),
                     region='영역 집계' if clustered else str(row.region or row.address),
                     status_breakdown=[_ratio_item(k,v,total) for k,v in status_by.get(gid,{}).items()],
                     disposition_breakdown=[_ratio_item(label,int(dispositions.loc[gid,key]),total) for key,label in
                         [('_fine','과태료'),('_warning','경고/범칙금'),('_reject','불수용/기타'),('_unknown','미확인')]
                         if dispositions.loc[gid,key] > 0],
                     agency_breakdown=agency,
                     category_breakdown=[_ratio_item(label,categories_by.get(gid,{}).get(key,0),total) for key,label in
                         [('traffic','교통위반'),('parking','주정차위반'),('other','기타위반')]])
        if clustered: point['cluster'] = True
        points.append(point)
    return points


def get_report_map_missing_summary(engine, *, year: str | None = None, category: str = "all", mode: str = "canonical",
                                   filters: dict | None = None) -> dict:
    """지도 첫 화면용: 좌표 없는 신고의 주소 그룹 수·신고 수만. 목록(본문 열 포함)은 모달을 열 때
    /stats/map/missing 으로 받는다(기술일지 B-03). 판정은 get_report_map_missing_groups 와 같다."""
    category, _available_years, combined_df = _load_map_records_frame(
        engine, year=year, category=category, mode=mode, column_names=_MAP_COLUMNS, filters=filters)
    if combined_df.empty:
        return {"group_count": 0, "report_count": 0}
    missing = combined_df[(combined_df["주소키"].str.strip() != "") & ~combined_df["유효좌표"]]
    return {"group_count": int(missing["주소키"].nunique(dropna=False)), "report_count": int(len(missing))}


def get_report_map_missing_groups(engine, *, year: str | None = None, category: str = "all", mode: str = "canonical", filters: dict | None = None):
    category, available_years, combined_df = _load_map_records_frame(
        engine,
        year=year,
        category=category,
        mode=mode,
        column_names=_MAP_MISSING_COLUMNS,
        filters=filters,
    )

    if combined_df.empty:
        return _sanitize_jsonable({
            "groups": [],
            "meta": {
                "available_years": available_years,
                "current_year": year or "all",
                "selected_category": category,
                "dedupe_mode": _normalize_mode(mode),
                "group_count": 0,
                "report_count": 0,
            },
        })

    missing_df = combined_df[
        (combined_df["주소키"].str.strip() != "")
        & ~combined_df["유효좌표"]
    ].copy()

    if missing_df.empty:
        return _sanitize_jsonable({
            "groups": [],
            "meta": {
                "available_years": available_years,
                "current_year": year or "all",
                "selected_category": category,
                "dedupe_mode": _normalize_mode(mode),
                "group_count": 0,
                "report_count": 0,
            },
        })

    sort_columns = [column for column in ["신고일", "신고번호"] if column in missing_df.columns]
    if sort_columns:
        ascending = [False] + [True] * (len(sort_columns) - 1)
        missing_df = missing_df.sort_values(sort_columns, ascending=ascending, kind="stable")

    groups = []
    for address_key, group in missing_df.groupby("주소키", sort=False, dropna=False):
        region_name = _first_nonempty_value(group, "행정구역")
        address_name = _first_nonempty_value(group, "위반장소") or _first_nonempty_value(group, "주소정규화")
        reports = []
        for _, row in group.iterrows():
            report = _row_to_dict(row.to_dict())
            report["category"] = _text_or_empty(row.get("category"))
            reports.append(report)
        groups.append({
            "address": address_name or str(address_key or "").strip(),
            "normalized_address": _first_nonempty_value(group, "주소정규화") or str(address_key or "").strip(),
            "region": region_name,
            "report_count": int(len(group)),
            "reports": reports,
        })

    groups.sort(key=lambda item: (-int(item["report_count"]), str(item["address"])))
    return _sanitize_jsonable({
        "groups": groups,
        "meta": {
            "available_years": available_years,
            "current_year": year or "all",
            "selected_category": category,
            "dedupe_mode": _normalize_mode(mode),
            "group_count": int(len(groups)),
            "report_count": int(len(missing_df)),
        },
    })
