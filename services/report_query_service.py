import pandas as pd
from sqlalchemy import desc, select, func
from sqlalchemy.exc import OperationalError

from core.database import database
import settings.settings as app_settings
from services import duplicate_group_service, plate_normalize, rating_eligibility, report_category, report_policy
from services.report_filter_spec import ReportFilterSpec

# 화면용 표 하나에 모든 분류가 있다(스키마 6). 분류는 category 열로 거른다.
EFFECTIVE = database.report_effective_table
CATEGORIES = report_category.CATEGORIES


def _install_law_matcher(conn):
    from services.law_values import matches_laws
    conn.connection.driver_connection.create_function(
        'sr_law_matches', 3, lambda value, query, partial: int(matches_laws(value, query, partial=bool(partial))), deterministic=True)


def _safe_read(conn, table):
    return pd.read_sql_query(select(table), conn)


def _get_watch_ids(conn):
    df_watch = pd.read_sql_query(select(database.watchlist_table.c.신고번호), conn)
    return set(df_watch["신고번호"].tolist()) if "신고번호" in df_watch.columns else set()


_INTEGER_COLUMNS = [c.name for c in EFFECTIVE.columns if type(c.type).__name__ == "Integer"]


def _apply_record_defaults(df, *, watch_ids: set, category: str = "", exact_values: bool = False):
    """exact_values=True(모바일 API 채널): NULL 은 None, 정수 열은 정수로 — DB 파일 교환과 같은 값(S-35).
    False(웹 화면 표시): 예전처럼 NULL 을 '' 로 채운다(템플릿 JS 가 문자열을 가정).
    category 를 주면 그 값으로 덮는다(구앱 호환: 합쳐 보낸 'other' 묶음은 모두 'other' 로 보인다)."""
    if df.empty:
        return df
    if category:
        df["category"] = category
    df["감시목록"] = df["신고번호"].apply(lambda value: "Y" if value in watch_ids else "N")
    if not exact_values:
        return df.fillna("")
    for column in _INTEGER_COLUMNS:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce").astype("Int64")
    return df.astype(object).where(df.notna(), None)


def _filter_withdraw(df):
    if app_settings.exclude_withdraw and not df.empty and "처리상태" in df.columns:
        return df[report_policy.status_series(df) != report_policy.WITHDRAWN_STATUS]
    return df


_normalize_mode = duplicate_group_service.normalize_mode


def _project_records(engine, records, *, mode="raw"):
    normalized_mode = _normalize_mode(mode)
    if normalized_mode == "raw":
        return records
    return duplicate_group_service.project_records(engine, records, mode=normalized_mode)


def _as_categories(category) -> tuple[str, ...] | None:
    """'traffic' 이나 ('safety', 'living') 처럼 받는다. None 이면 모든 분류."""
    if category is None:
        return None
    values = (category,) if isinstance(category, str) else tuple(category)
    unknown = [v for v in values if v not in CATEGORIES]
    if unknown:
        raise ValueError(f"알 수 없는 분류: {unknown}")
    return values


def _build_records_query(table_obj, filters=None, categories=None):
    query = select(table_obj).order_by(desc(table_obj.c["신고번호"]))
    if categories is not None:
        query = query.where(table_obj.c["category"].in_(categories))
    if app_settings.exclude_withdraw and "처리상태" in table_obj.c:
        query = query.where(report_policy.sql_not_withdrawn(table_obj.c["처리상태"]))

    if not filters:
        return query

    status = filters.get("status")
    if status and "처리상태" in table_obj.c:
        query = query.where(report_policy.sql_list_status_filter(table_obj.c["처리상태"], status))

    fine = filters.get("fine")
    if fine and "범칙금_과태료" in table_obj.c and "처리상태" in table_obj.c:
        clause = report_policy.sql_list_fine_filter(table_obj.c["범칙금_과태료"], table_obj.c["처리상태"], fine)
        if clause is not None:
            query = query.where(clause)

    person = filters.get("person")
    if person and "담당자" in table_obj.c:
        query = query.where(table_obj.c["담당자"] == person)

    law = filters.get("law")
    if law and "위반법규" in table_obj.c:
        query = query.where(func.sr_law_matches(table_obj.c["위반법규"], str(law), int(not filters.get('lawExact'))) == 1)

    rating_cause = filters.get("ratingCause")
    if rating_cause and "별점사유" in table_obj.c:
        query = query.where(table_obj.c["별점사유"].contains(rating_cause))

    return query


def get_report_page(engine, category, *, offset=0, limit=200, mode='canonical', label: str | None = None, search_filter=None):
    """Additive SQL pagination. Legacy full-list functions remain unchanged.
    category 는 하나 또는 여러 분류(구앱 호환 묶음). label 을 주면 응답 category·행 category 를 그 값으로 보인다."""
    categories = _as_categories(category)
    table = EFFECTIVE
    query = duplicate_group_service.canonical_sql(table, _build_records_query(table, categories=categories), mode)
    projection = duplicate_group_service.ProjectionContext.with_members(mode, {})
    with engine.connect() as conn:
        # One read transaction for count and page; no full materialization.
        conn.exec_driver_sql('BEGIN')
        if search_filter is not None:
            from services.client_report_search import install_predicate
            query = query.where(install_predicate(conn, table, search_filter))
        total = conn.execute(select(func.count()).select_from(query.order_by(None).subquery())).scalar_one()
        df = pd.read_sql_query(query.order_by(None).order_by(table.c["신고번호"].desc(), table.c.ID.desc()).offset(offset).limit(limit), conn)
        watch_ids = _get_watch_ids(conn)
        if projection.mode == 'canonical' and not df.empty:
            page_ids = set(df['ID'].astype(str))
            # 페이지 신고가 속한 그룹만 같은 snapshot 에서 읽는다(기술일지 B-04)
            projection = duplicate_group_service.ProjectionContext.load(conn, mode, page_ids)
            members = projection.members
            group_ids = {members[rid]['group_id'] for rid in page_ids if rid in members}
            related = {rid for rid, meta in members.items() if meta['group_id'] in group_ids}
            watched_groups = set()
            if related and watch_ids:
                rows = conn.execute(select(table.c.ID).where(table.c.ID.in_(related), table.c.신고번호.in_(watch_ids)))
                watched_groups.update(members[str(row[0])]['group_id'] for row in rows)
            for _, row in df.iterrows():
                meta = members.get(str(row['ID']))
                if meta and meta['group_id'] in watched_groups: watch_ids.add(row['신고번호'])
    shown = label or (categories[0] if categories and len(categories) == 1 else '')
    df = _apply_record_defaults(df, watch_ids=watch_ids, category=label or '', exact_values=True)
    records = projection.project_records(df.to_dict(orient='records')) if not df.empty else []
    return {'category':shown,'total':total,'offset':offset,'limit':limit,'count':len(records),
            'next_offset':offset+len(records) if offset+len(records)<total else None,
            'dedupe_mode':mode,'data':records}


def _get_records_from_table(engine, table_obj, filters=None, category: str = '', mode: str = 'raw', exact_values: bool = False,
                            categories=None):
    with engine.connect() as conn:
        conn.exec_driver_sql('BEGIN')
        records = _get_records_from_connection(conn, table_obj, filters, category=category,
                                               mode=mode, exact_values=exact_values, categories=categories)
    return records


def _get_records_from_connection(conn, table_obj, filters=None, *, category='', mode='raw', exact_values=False, categories=None):
    _install_law_matcher(conn)
    df = pd.read_sql_query(_build_records_query(table_obj, filters, categories), conn)
    watch_ids = _get_watch_ids(conn)
    projection = duplicate_group_service.ProjectionContext.load(conn, mode)

    agency_key = (filters or {}).get("agencyKey")
    if agency_key and not df.empty and "처리기관" in df.columns:
        from services.agency_registry import resolve_stats_agency
        names = df["처리기관"].fillna("").astype(str)
        codes = df["처리기관코드"].fillna("").astype(str) if "처리기관코드" in df else [""] * len(df)
        pairs = list(zip(codes, names))
        keys = {pair: resolve_stats_agency(*pair)[1] for pair in set(pairs)}
        df = df[[keys[pair] == agency_key for pair in pairs]]

    if not exact_values and not df.empty and "처리기관" in df.columns:
        from services.agency_registry import resolve_display_agency

        codes = df["처리기관코드"] if "처리기관코드" in df.columns else [None] * len(df)
        df["처리기관"] = [
            resolve_display_agency(code, name)[0] or (name if isinstance(name, str) else "")
            for code, name in zip(codes, df["처리기관"])
        ]

    if filters and not df.empty:
        # 기관(표시명)·경찰 조건: 통계와 같은 ReportFilterSpec(EO R-02). 기관 키가 있으면 위에서 이미 골랐다.
        spec = ReportFilterSpec.from_filters({
            "agency": "" if agency_key else filters.get("agency"), "agencyExact": filters.get("agencyExact"),
            "excludePolice": filters.get("excludePolice"), "onlyPolice": filters.get("onlyPolice")})
        df = spec.apply_rows(df)
        rating = filters.get('rating')
        if rating and "별점" in df.columns:
            if rating == "__none__":
                rating_series = pd.to_numeric(df["별점"], errors="coerce")
                df = df[rating_series.isna() | (rating_series <= 0)]
            else:
                wanted = pd.to_numeric(pd.Series([rating]), errors="coerce").iloc[0]
                if pd.isna(wanted):
                    df = df.iloc[0:0]
                else:
                    rating_series = pd.to_numeric(df["별점"], errors="coerce")
                    df = df[rating_series == wanted]

    df = _apply_record_defaults(df, watch_ids=watch_ids, category=category, exact_values=exact_values)
    records = df.to_dict(orient="records") if not df.empty else []
    return projection.project_records(records)


def get_category_records(engine, category, filters=None, mode: str = "raw", exact_values: bool = False,
                         label: str | None = None):
    """한 분류(또는 여러 분류 묶음)의 화면용 행. label 을 주면 행의 category 를 그 값으로 보인다(구앱 호환 묶음)."""
    categories = _as_categories(category)
    return _get_records_from_table(engine, EFFECTIVE, filters, category=label or "", mode=mode,
                                   exact_values=exact_values, categories=categories)


def get_traffic_records(engine, filters=None, mode: str = "raw", exact_values: bool = False):
    return get_category_records(engine, "traffic", filters, mode, exact_values)


def get_parking_records(engine, filters=None, mode: str = "raw", exact_values: bool = False):
    return get_category_records(engine, "parking", filters, mode, exact_values)


def get_safety_records(engine, filters=None, mode: str = "raw", exact_values: bool = False):
    return get_category_records(engine, "safety", filters, mode, exact_values)


def get_living_records(engine, filters=None, mode: str = "raw", exact_values: bool = False):
    return get_category_records(engine, "living", filters, mode, exact_values)


def get_other_records(engine, filters=None, mode: str = "raw", exact_values: bool = False):
    return get_category_records(engine, "other", filters, mode, exact_values)


def get_all_records(engine, filters=None, mode: str = 'raw'):
    combined = _get_records_from_table(engine, EFFECTIVE, filters, mode=mode)
    combined.sort(key=lambda item: item.get('신고번호', '') or '', reverse=True)
    return combined


def search_by_vehicle(engine, vehicle_number: str, mode: str = "raw"):
    vehicle_number = vehicle_number.strip()
    if not vehicle_number:
        return []

    with engine.connect() as conn:
        conn.exec_driver_sql('BEGIN')
        projection = duplicate_group_service.ProjectionContext.load(conn, mode)
        watch_ids = _get_watch_ids(conn)
        query = select(EFFECTIVE).where(EFFECTIVE.c.차량번호.contains(vehicle_number)).order_by(desc(EFFECTIVE.c.신고번호))
        df = _filter_withdraw(pd.read_sql_query(query, conn))
        results = _apply_record_defaults(df, watch_ids=watch_ids).to_dict(orient="records") if not df.empty else []

    results.sort(key=lambda item: item.get("신고번호", "") or "", reverse=True)
    return projection.project_records(results)


def search_by_address(engine, address: str, mode: str = "raw"):
    address = address.strip()
    if not address:
        return []

    with engine.connect() as conn:
        conn.exec_driver_sql('BEGIN')
        projection = duplicate_group_service.ProjectionContext.load(conn, mode)
        watch_ids = _get_watch_ids(conn)
        query = select(EFFECTIVE).where(EFFECTIVE.c.위반장소.contains(address)).order_by(desc(EFFECTIVE.c.신고번호))
        df = _filter_withdraw(pd.read_sql_query(query, conn))
        results = _apply_record_defaults(df, watch_ids=watch_ids).to_dict(orient="records") if not df.empty else []

    results.sort(key=lambda item: item.get("신고번호", "") or "", reverse=True)
    return projection.project_records(results)


def _plate_group_key(raw_plate: str) -> str:
    """중복차량 그룹핑 키 — 정규화에 성공하면 정규화 값, 아니면(미인식·모호) 원문 그대로를 키로 쓴다.
    원문 열(차량번호)은 바꾸지 않는다(읽을 때만 정규화, 2026-10-12 결정) — 서로 다른 미인식 값들이
    하나의 그룹으로 뭉치지 않도록 정규화 실패 시 raw 를 그대로 키로 쓴다."""
    return plate_normalize.normalize_plate(raw_plate) or raw_plate


def get_duplicate_records(engine, mode: str = "raw"):
    with engine.connect() as conn:
        conn.exec_driver_sql('BEGIN')
        projection = duplicate_group_service.ProjectionContext.load(conn, mode)
        df_all = _safe_read(conn, EFFECTIVE)
        if df_all.empty:
            return []

        watch_ids = _get_watch_ids(conn)
        df_all["감시목록"] = df_all["신고번호"].apply(lambda value: "Y" if value in watch_ids else "N")
        df_all = df_all[df_all["차량번호"].str.strip() != ""]
        # 중복차량 매칭은 정규화된 번호판으로 한다(군더더기·설명 문구가 섞여도 같은 차량으로 묶인다) —
        # 화면에 보이는 차량번호 열 자체는 각 신고의 원문 그대로다.
        df_all["_plate_key"] = df_all["차량번호"].map(_plate_group_key)

        total_counts = df_all["_plate_key"].value_counts().to_dict()
        valid_counts = df_all[report_policy.status_series(df_all) != report_policy.WITHDRAWN_STATUS]["_plate_key"].value_counts().to_dict()
        counts = df_all["_plate_key"].value_counts()
        duplicates = counts[counts > 1].index.tolist()
        df_dups = df_all[df_all["_plate_key"].isin(duplicates)].copy()
        df_dups["total_count"] = df_dups["_plate_key"].map(total_counts)
        df_dups["valid_count"] = df_dups["_plate_key"].map(valid_counts).fillna(0).astype(int)

        max_rnums = df_dups.groupby("_plate_key")["신고번호"].max().reset_index()
        max_rnums.rename(columns={"신고번호": "최근신고번호"}, inplace=True)
        df_dups = df_dups.merge(max_rnums, on="_plate_key")
        df_dups = df_dups.sort_values(by=["최근신고번호", "_plate_key", "신고번호"], ascending=[False, True, False])
        df_dups = df_dups.drop(columns=["최근신고번호"])

        if app_settings.exclude_withdraw:
            df_dups = df_dups[report_policy.status_series(df_dups) != report_policy.WITHDRAWN_STATUS]
            if not df_dups.empty:
                remaining_counts = df_dups["_plate_key"].value_counts()
                single_after_filter = remaining_counts[remaining_counts <= 1].index.tolist()
                if single_after_filter:
                    df_dups = df_dups[~df_dups["_plate_key"].isin(single_after_filter)]

        df_dups = df_dups.drop(columns=["_plate_key"])
        records = df_dups.fillna("").to_dict("records")
        return projection.project_records(records)


def resolve_to_report_numbers(engine, mixed_list):
    values = list(dict.fromkeys(mixed_list))
    if not values:
        return []
    final_report_numbers = set()
    with engine.connect() as conn:
        rows = []
        # 400 * 2 predicates stays below the legacy SQLite 999-variable limit.
        for offset in range(0, len(values), 400):
            chunk = values[offset:offset + 400]
            rows.extend(conn.execute(select(EFFECTIVE.c.ID, EFFECTIVE.c.신고번호).where(
                EFFECTIVE.c.ID.in_(chunk) | EFFECTIVE.c.신고번호.in_(chunk))).all())
        numbers = {row[1] for row in rows}
        by_id = {}
        for identifier, number in rows:
            by_id.setdefault(identifier, set()).add(number)
        for value in values:
            if value in numbers:
                final_report_numbers.add(value)
            else:
                final_report_numbers.update(by_id.get(value, ()))

    return list(final_report_numbers)


def get_unrated_records(engine):
    with engine.connect() as conn:
        # 보수적인 SQL prefilter 뒤 동일한 순수 판정으로 공백/NULL까지 확인한다.
        query = select(EFFECTIVE).where(
            func.coalesce(EFFECTIVE.c['만족도조사여부'], '').not_in(['참여 완료', '참여 불가']),
            report_policy.sql_norm(EFFECTIVE.c['처리상태']).not_in(
                [report_policy.WITHDRAWN_STATUS, '답변 대기', *report_policy.PROCESSING_ORDER]))
        df = pd.read_sql_query(query, conn)
    if df.empty:
        return []
    # 별점 대상 규칙은 제출 단계와 같은 함수(모바일 RatingService.ineligibleReason 과 같은 규칙)
    eligible = [
        rating_eligibility.ineligible_reason(poll, status) is None
        for poll, status in zip(df["만족도조사여부"], df["처리상태"])
    ]
    df = df[eligible]
    if df.empty:
        return []
    df = df.sort_values(by="신고번호", ascending=False)
    return df.fillna("").to_dict("records")


def get_all_watchlist(engine):
    with engine.connect() as conn:
        df_watch = pd.read_sql_query(select(database.watchlist_table.c.신고번호), conn)
        if df_watch.empty:
            return []

        watch_ids = list(dict.fromkeys(df_watch['신고번호'].tolist()))
        frames = []
        for offset in range(0, len(watch_ids), 500):
            frame = pd.read_sql_query(select(EFFECTIVE).where(EFFECTIVE.c['신고번호'].in_(watch_ids[offset:offset + 500])), conn)
            if not frame.empty:
                frames.append(frame)
        df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        if df.empty:
            return []

        df = df[df["신고번호"].isin(watch_ids)]
        if df.empty:
            return []

        df["감시목록"] = "Y"
        df = df.sort_values(by="신고번호", ascending=False)
        records = df.fillna("").to_dict("records")
        return records


def update_watchlist_status(engine, report_numbers, status):
    if not report_numbers:
        return 0

    with engine.begin() as conn:
        if status == "Y":
            records = [{"신고번호": report_number} for report_number in report_numbers]
            from sqlalchemy.dialects.sqlite import insert as sqlite_insert

            stmt = sqlite_insert(database.watchlist_table).values(records)
            stmt = stmt.on_conflict_do_nothing(index_elements=["신고번호"])
            conn.execute(stmt)
        else:
            from sqlalchemy import delete

            stmt = delete(database.watchlist_table).where(database.watchlist_table.c.신고번호.in_(report_numbers))
            removed = conn.execute(stmt).rowcount
        database.refresh_watch_flags(conn, report_numbers)

    return len(report_numbers) if status == "Y" else int(removed or 0)
