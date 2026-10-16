"""통계 원천 읽기(EO R-05): SQL 후보 조회·한 읽기 snapshot·대표건 투영·기관 표시·행 필터."""
from collections import OrderedDict
from datetime import datetime

import pandas as pd
from sqlalchemy import func, select

from core.database import database
from services import report_category, report_policy
from services.report_filter_spec import ReportFilterSpec
import settings.settings as app_settings
from services import duplicate_group_service
from services.report_cache import cached
from services.stats.common import _MAP_COLUMNS, _MAP_TARGET_KEYS, _STATS_COLUMNS, _is_finite_number, _normalize_map_category_value, _text_or_empty
from services.stats.metrics import _stats_status_series


def _build_select_for_columns(table_obj, column_names):
    columns = [table_obj.c[column] for column in column_names if column in table_obj.c]
    return select(*columns)


def _build_stats_query(table_obj, filters=None, column_names=None):
    """SQL 후보 축소만 한다(ReportFilterSpec.sql_candidates — 최종 결과의 상위 집합). 최종 판정은 pandas 단계."""
    query = _build_select_for_columns(table_obj, column_names or _STATS_COLUMNS)
    for clause in ReportFilterSpec.from_filters(filters).sql_candidates(table_obj):
        query = query.where(clause)
    return query


def _read_stats_frame(conn, table_obj, filters=None, column_names=None):
    return pd.read_sql_query(_build_stats_query(table_obj, filters, column_names=column_names), conn)


def _ensure_id_column(df: pd.DataFrame) -> pd.DataFrame:
    if "ID" not in df.columns:
        df["ID"] = ""
    else:
        df["ID"] = df["ID"].fillna("").astype(str)
    return df


def _exclude_withdraw_rows(df: pd.DataFrame) -> pd.DataFrame:
    if not app_settings.exclude_withdraw or df.empty or "처리상태" not in df.columns:
        return df
    return df[report_policy.status_series(df) != report_policy.WITHDRAWN_STATUS].copy()


def _load_available_years(conn):
    available_years = set()
    table_obj = database.report_effective_table
    query = (
        select(func.substr(table_obj.c["답변일"], 1, 4).label("year"))
        .where(table_obj.c["답변일"].is_not(None))
        .distinct()
    )
    df_years = pd.read_sql_query(query, conn)
    if not df_years.empty and "year" in df_years.columns:
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


def _apply_stats_row_filters(df: pd.DataFrame, filters=None) -> pd.DataFrame:
    """`get_agency_stats` 와 `get_stats_overview` 가 공유하는 행 필터(연도·날짜·기관·텍스트·경찰). 최종 판정."""
    return ReportFilterSpec.from_filters(filters).apply_rows(df) if filters else df


def _apply_stats_law_filter(df: pd.DataFrame, filters=None) -> pd.DataFrame:
    """S-09 + 2026-10-16: 저장 키 소속 판정(`matches_law_keys`). 드롭다운·막대가 넘긴 키 그대로(앞뒤 공백 무시),
    하위 항·호·목은 포함, 쉼표·& 분해와 법령명 접두 검색은 없다. 법규 막대 건수(`law_memberships`)와 같은 판정이다."""
    return ReportFilterSpec.from_filters(filters).apply_law(df) if filters else df


def _load_stats_frames(engine, filters=None, mode: str = "canonical", taxonomy: int = 5):
    """DB 에서 분류별 프레임을 읽고 대표건 projection 까지 적용한다. (available_years, frames)

    `frames` 는 분류 순서를 지키는 dict{category: DataFrame}. taxonomy=5(기본): report_category.CATEGORIES
    5개(traffic·parking·safety·living·other). taxonomy=3: 예전 3분류(traffic·parking·other) — 안전신고·생활불편은
    report_category.legacy_category 로 행 단계에서 기타에 접는다(구앱·구화면 호환, 기존 동작과 동일).
    """
    categories = report_category.CATEGORIES if taxonomy == 5 else report_category.LEGACY_CATEGORIES
    with engine.connect() as conn:
        conn.exec_driver_sql('BEGIN')
        available_years = _load_available_years(conn)
        combined_df = _read_stats_frame(conn, database.report_effective_table, filters)
        projection = duplicate_group_service.ProjectionContext.load(conn, mode)

    combined_df = _ensure_id_column(combined_df)
    if not combined_df.empty:
        if taxonomy == 3:
            combined_df["category"] = combined_df["category"].map(report_category.legacy_category)
        # 처분 분류(처분 대상 아님)와 추정 과태료 규칙이 신고 메뉴(entry_value)를 쓴다.
        combined_df["entry_value"] = combined_df["entry_value"].fillna("").astype(str)
    combined_df = projection.project_frame(combined_df)
    if not combined_df.empty and "처리기관" in combined_df.columns:
        combined_df = _apply_registry_agency_display(combined_df)
    # 대표건 투영이 모든 행을 뺐어도(예: 비대표건만 걸리는 연도) 투영 결과로 바꾼다. 예전에는 빈 결과일 때 투영 전
    # 프레임을 그대로 돌려 제외해야 할 비대표건이 통계에 들어갔다(기술일지 A1-01).
    frames = OrderedDict()
    if "category" in combined_df.columns:
        for category in categories:
            frames[category] = combined_df[combined_df["category"] == category].copy()
    else:
        for category in categories:
            frames[category] = combined_df.iloc[0:0].copy()
    return available_years, frames


def _apply_registry_agency_display(df: pd.DataFrame) -> pd.DataFrame:
    """registry가 해석한 현행 기관 표시·통계 키로 푼다(2026-09-29 전체자료 색인).

    확인된 코드(현존·승계·별칭 유일)는 현행명 + agency_stat_key, (구) 분기는
    '(구)' 표시 + 별도 src 키, 미확정·열 없음·NaN이면 기존 normalize를
    그대로 쓰고 src 키를 단다. `처리기관` 열을 표시명으로 바꾸고 `_agency_key` 를 단 **새 프레임**을 돌려준다
    (입력 프레임은 바꾸지 않는다 — EO R-05).
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
    return df.assign(처리기관=[resolved[pair][0] for pair in pairs], _agency_key=[resolved[pair][1] for pair in pairs])


@cached
def _load_map_records_frame(
    engine,
    *,
    year: str | None = None,
    category: str = "all",
    mode: str = "canonical",
    column_names: list[str] | None = None,
    filters: dict | None = None,
    taxonomy: int = 3,
):
    """지도용 행. `filters` 는 통계 화면과 같은 조건(법규·상세 검색)이며 같은 순서로 적용한다(2026-09-28 추가, 없으면 예전과 같음).

    `targetAgency`/`targetPerson` 은 통계 상세 패널의 '지도에서 보기' 대상이다. 통계표의 기관명은 경찰서 정규화 뒤 이름이므로
    정규화 뒤 정확히 일치로 거른다(목록 `/data` 의 agencyExact 와 같은 순서).

    taxonomy=3(기본, 예전과 같음): traffic·parking·other(안전신고·생활불편은 기타에 접는다). taxonomy=5: 5분류 그대로.
    """
    stats_filters = {key: value for key, value in (filters or {}).items() if key not in _MAP_TARGET_KEYS and value not in (None, "", False)}
    target_agency = _text_or_empty((filters or {}).get("targetAgency"))
    target_agency_key = _text_or_empty((filters or {}).get("targetAgencyKey"))
    target_person = _text_or_empty((filters or {}).get("targetPerson"))
    completed_only = (filters or {}).get("completedOnly") is True
    filters = dict(stats_filters)
    if year and year not in ("all", "", None):
        filters["year"] = str(year)

    normalized_category = _normalize_map_category_value(category, taxonomy)
    selected_columns = list(column_names or _MAP_COLUMNS)

    with engine.connect() as conn:
        conn.exec_driver_sql('BEGIN')
        available_years = _load_available_years(conn)
        frames = []
        df = _read_stats_frame(conn, database.report_effective_table, filters,
                               column_names=list(dict.fromkeys([*selected_columns, "category"])))
        if not df.empty:
            if taxonomy == 3:
                df["category"] = df["category"].map(report_category.legacy_category)  # 구앱·구화면 호환(3분류)
            if normalized_category != "all":
                df = df[df["category"] == normalized_category]
            frames.append(df)
        projection = duplicate_group_service.ProjectionContext.load(conn, mode)

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
    combined_df = projection.project_frame(combined_df)
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
        combined_df = combined_df[_stats_status_series(combined_df).isin(report_policy.COMPLETED_STATUSES)].copy()
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
