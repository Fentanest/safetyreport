import pandas as pd
from sqlalchemy import desc, select
from sqlalchemy.exc import OperationalError

from core.database import database
import settings.settings as app_settings
from services import duplicate_group_service


def _safe_read(conn, table):
    try:
        return pd.read_sql_query(select(table), conn)
    except OperationalError:
        return pd.DataFrame()


def _get_watch_ids(conn):
    df_watch = pd.read_sql_query(select(database.watchlist_table.c.신고번호), conn)
    return set(df_watch["신고번호"].tolist()) if "신고번호" in df_watch.columns else set()


def _apply_record_defaults(df, *, watch_ids: set, category: str = ""):
    if df.empty:
        return df
    if category:
        df["category"] = category
    df["감시목록"] = df["신고번호"].apply(lambda value: "Y" if value in watch_ids else "N")
    return df.fillna("")


def _filter_withdraw(df):
    if app_settings.exclude_withdraw and not df.empty and "처리상태" in df.columns:
        return df[df["처리상태"] != "취하"]
    return df


def _normalize_mode(mode: str | None) -> str:
    normalized = str(mode or "raw").strip().lower()
    return normalized if normalized in {"raw", "canonical"} else "raw"


def _project_records(engine, records, *, mode="raw"):
    normalized_mode = _normalize_mode(mode)
    if normalized_mode == "raw":
        return records
    return duplicate_group_service.project_records(engine, records, mode=normalized_mode)


def _build_records_query(table_obj, filters=None):
    query = select(table_obj).order_by(desc(table_obj.c["신고번호"]))
    if app_settings.exclude_withdraw and "처리상태" in table_obj.c:
        query = query.where(table_obj.c["처리상태"] != "취하")

    if not filters:
        return query

    status = filters.get("status")
    if status and "처리상태" in table_obj.c:
        if status == "처리중":
            query = query.where(table_obj.c["처리상태"].in_(["처리중", "진행", "진행중", "검토중"]))
        elif status == "완료":
            query = query.where(table_obj.c["처리상태"].in_(["수용", "불수용", "일부수용", "기타", "답변완료"]))
        elif status == "불수용":
            query = query.where(table_obj.c["처리상태"].in_(["불수용", "기타"]))
        else:
            query = query.where(table_obj.c["처리상태"] == status)

    fine = filters.get("fine")
    if fine and "범칙금_과태료" in table_obj.c and "처리상태" in table_obj.c:
        if fine == "과태료":
            query = query.where(table_obj.c["범칙금_과태료"].contains("과태료"))
        elif fine == "경고":
            query = query.where(
                table_obj.c["범칙금_과태료"].contains("경고")
                | table_obj.c["범칙금_과태료"].contains("범칙금")
            )
        elif fine == "미확인":
            query = query.where(table_obj.c["범칙금_과태료"] == "미확인").where(
                ~table_obj.c["처리상태"].in_(["불수용", "기타"])
            )

    person = filters.get("person")
    if person and "담당자" in table_obj.c:
        query = query.where(table_obj.c["담당자"] == person)

    law = filters.get("law")
    if law and "위반법규" in table_obj.c:
        if law == "__없음__":
            query = query.where(
                (table_obj.c["위반법규"].is_(None))
                | (table_obj.c["위반법규"] == "")
            )
        else:
            query = query.where(table_obj.c["위반법규"].contains(law))

    rating_cause = filters.get("ratingCause")
    if rating_cause and "별점사유" in table_obj.c:
        query = query.where(table_obj.c["별점사유"].contains(rating_cause))

    return query


def _get_records_from_table(engine, table_obj, filters=None, category: str = "", mode: str = "raw"):
    try:
        with engine.connect() as conn:
            df = pd.read_sql_query(_build_records_query(table_obj, filters), conn)
            watch_ids = _get_watch_ids(conn)
    except Exception:
        return []

    if app_settings.normalize_police and not df.empty and "처리기관" in df.columns:
        df["처리기관"] = df["처리기관"].apply(database.normalize_police_agency)

    if filters and not df.empty:
        agency = filters.get("agency")
        if agency and "처리기관" in df.columns:
            if filters.get("agencyExact"):
                df = df[df["처리기관"] == agency]
            else:
                df = df[df["처리기관"].str.contains(agency, na=False, regex=False)]

        rating = filters.get("rating")
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

    df = _apply_record_defaults(df, watch_ids=watch_ids, category=category)
    records = df.to_dict(orient="records") if not df.empty else []
    return _project_records(engine, records, mode=mode)


def get_traffic_records(engine, filters=None, mode: str = "raw"):
    return _get_records_from_table(engine, database.merge_traffic_table, filters, category="traffic", mode=mode)


def get_parking_records(engine, filters=None, mode: str = "raw"):
    return _get_records_from_table(engine, database.merge_parking_table, filters, category="parking", mode=mode)


def get_other_records(engine, filters=None, mode: str = "raw"):
    return _get_records_from_table(engine, database.merge_other_table, filters, category="other", mode=mode)


def get_all_records(engine, filters=None, mode: str = "raw"):
    combined = (
        get_traffic_records(engine, filters, mode=mode)
        + get_parking_records(engine, filters, mode=mode)
        + get_other_records(engine, filters, mode=mode)
    )
    combined.sort(key=lambda item: item.get("신고번호", "") or "", reverse=True)
    return combined


def search_by_vehicle(engine, vehicle_number: str, mode: str = "raw"):
    vehicle_number = vehicle_number.strip()
    if not vehicle_number:
        return []

    results = []
    with engine.connect() as conn:
        watch_ids = _get_watch_ids(conn)
        for table_obj, category in [
            (database.merge_traffic_table, "traffic"),
            (database.merge_parking_table, "parking"),
            (database.merge_other_table, "other"),
        ]:
            if "차량번호" not in table_obj.c:
                continue
            query = select(table_obj).where(table_obj.c.차량번호.contains(vehicle_number)).order_by(desc(table_obj.c.신고번호))
            try:
                df = pd.read_sql_query(query, conn)
            except OperationalError:
                continue
            df = _filter_withdraw(df)
            if df.empty:
                continue
            df = _apply_record_defaults(df, watch_ids=watch_ids, category=category)
            results.extend(df.to_dict(orient="records"))

    results.sort(key=lambda item: item.get("신고번호", "") or "", reverse=True)
    return _project_records(engine, results, mode=mode)


def search_by_address(engine, address: str, mode: str = "raw"):
    address = address.strip()
    if not address:
        return []

    results = []
    with engine.connect() as conn:
        watch_ids = _get_watch_ids(conn)
        for table_obj, category in [
            (database.merge_traffic_table, "traffic"),
            (database.merge_parking_table, "parking"),
            (database.merge_other_table, "other"),
        ]:
            if "위반장소" not in table_obj.c:
                continue
            query = select(table_obj).where(table_obj.c.위반장소.contains(address)).order_by(desc(table_obj.c.신고번호))
            try:
                df = pd.read_sql_query(query, conn)
            except OperationalError:
                continue
            df = _filter_withdraw(df)
            if df.empty:
                continue
            df = _apply_record_defaults(df, watch_ids=watch_ids, category=category)
            results.extend(df.to_dict(orient="records"))

    results.sort(key=lambda item: item.get("신고번호", "") or "", reverse=True)
    return _project_records(engine, results, mode=mode)


def get_duplicate_records(engine, mode: str = "raw"):
    with engine.connect() as conn:
        df_t = pd.read_sql_query(select(database.merge_traffic_table), conn)
        df_p = _safe_read(conn, database.merge_parking_table)
        df_o = pd.read_sql_query(select(database.merge_other_table), conn)
        if not df_t.empty:
            df_t["category"] = "traffic"
        if not df_p.empty:
            df_p["category"] = "parking"
        if not df_o.empty:
            df_o["category"] = "other"

        df_all = pd.concat([df_t, df_p, df_o])
        if df_all.empty:
            return []

        watch_ids = _get_watch_ids(conn)
        df_all["감시목록"] = df_all["신고번호"].apply(lambda value: "Y" if value in watch_ids else "N")
        df_all = df_all[df_all["차량번호"].str.strip() != ""]

        total_counts = df_all["차량번호"].value_counts().to_dict()
        valid_counts = df_all[df_all["처리상태"] != "취하"]["차량번호"].value_counts().to_dict()
        counts = df_all["차량번호"].value_counts()
        duplicates = counts[counts > 1].index.tolist()
        df_dups = df_all[df_all["차량번호"].isin(duplicates)].copy()
        df_dups["total_count"] = df_dups["차량번호"].map(total_counts)
        df_dups["valid_count"] = df_dups["차량번호"].map(valid_counts).fillna(0).astype(int)

        max_rnums = df_dups.groupby("차량번호")["신고번호"].max().reset_index()
        max_rnums.rename(columns={"신고번호": "최근신고번호"}, inplace=True)
        df_dups = df_dups.merge(max_rnums, on="차량번호")
        df_dups = df_dups.sort_values(by=["최근신고번호", "차량번호", "신고번호"], ascending=[False, True, False])
        df_dups = df_dups.drop(columns=["최근신고번호"])

        if app_settings.exclude_withdraw:
            df_dups = df_dups[df_dups["처리상태"] != "취하"]
            if not df_dups.empty:
                remaining_counts = df_dups["차량번호"].value_counts()
                single_after_filter = remaining_counts[remaining_counts <= 1].index.tolist()
                if single_after_filter:
                    df_dups = df_dups[~df_dups["차량번호"].isin(single_after_filter)]

        records = df_dups.fillna("").to_dict("records")
        return _project_records(engine, records, mode=mode)


def resolve_to_report_numbers(engine, mixed_list):
    final_report_numbers = set()
    with engine.connect() as conn:
        df_t = pd.read_sql_query(select(database.merge_traffic_table.c.ID, database.merge_traffic_table.c.신고번호), conn)
        df_p_source = _safe_read(conn, database.merge_parking_table)
        df_p = df_p_source[["ID", "신고번호"]] if not df_p_source.empty else pd.DataFrame(columns=["ID", "신고번호"])
        df_o = pd.read_sql_query(select(database.merge_other_table.c.ID, database.merge_other_table.c.신고번호), conn)
        df = pd.concat([df_t, df_p, df_o])
        if df.empty:
            return []

        for value in mixed_list:
            if value in df["신고번호"].values:
                final_report_numbers.add(value)
            elif value in df["ID"].values:
                matching = df[df["ID"] == value]["신고번호"].tolist()
                for report_number in matching:
                    final_report_numbers.add(report_number)

    return list(final_report_numbers)


def get_unrated_records(engine):
    with engine.connect() as conn:
        results = []
        for table_obj, category in [
            (database.merge_traffic_table, "traffic"),
            (database.merge_parking_table, "parking"),
            (database.merge_other_table, "other"),
        ]:
            try:
                df = pd.read_sql_query(select(table_obj), conn)
            except OperationalError:
                continue
            if df.empty:
                continue
            df = df[~df["만족도조사여부"].isin(["참여 완료", "참여 불가"])]
            df = df[~df["처리상태"].isin(["취하", "답변 대기", "처리중", "진행", "진행중", "검토중"])]
            if df.empty:
                continue
            df["category"] = category
            results.append(df)

        if not results:
            return []

        df_all = pd.concat(results, ignore_index=True)
        df_all = df_all.sort_values(by="신고번호", ascending=False)
        records = df_all.fillna("").to_dict("records")
        return records


def get_all_watchlist(engine):
    with engine.connect() as conn:
        df_watch = pd.read_sql_query(select(database.watchlist_table.c.신고번호), conn)
        if df_watch.empty:
            return []

        watch_ids = set(df_watch["신고번호"].tolist())
        df_t = pd.read_sql_query(select(database.merge_traffic_table), conn)
        df_p = _safe_read(conn, database.merge_parking_table)
        df_o = pd.read_sql_query(select(database.merge_other_table), conn)
        if not df_t.empty:
            df_t["category"] = "traffic"
        if not df_p.empty:
            df_p["category"] = "parking"
        if not df_o.empty:
            df_o["category"] = "other"

        df = pd.concat([df_t, df_p, df_o], ignore_index=True)
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
            conn.execute(stmt)

    return len(report_numbers)
