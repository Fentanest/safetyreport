"""지도 묶음·좌표 없는 신고 응답 조립(EO R-05)."""

import pandas as pd

from services import report_policy
from services.report_cache import cached
from services.stats.common import _MAP_COLUMNS, _MAP_MISSING_COLUMNS, _first_nonempty_value, _normalize_mode, _ratio_item, _row_to_dict, _sanitize_jsonable, _text_or_empty
from services.stats.reads import _load_map_records_frame


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
    disposition = report_policy.dashboard_disposition_masks(frame)
    fine_mask, warning, reject = disposition['fines'], disposition['warnings'], disposition['rejects']
    frame['_status_label'] = report_policy.breakdown_status_series(frame)
    frame['_fine'] = fine_mask.astype(int)
    frame['_warning'] = warning.astype(int)
    frame['_reject'] = reject.astype(int)
    frame['_unknown'] = (~(fine_mask | warning | reject)).astype(int)
    dispositions = grouped[['_fine','_warning','_reject','_unknown']].sum()
    status_counts = frame.groupby(['_group','_status_label'], dropna=False).size()
    category_counts = frame.groupby(['_group','category']).size()
    agency_counts = frame.groupby(['_group','_agency_key','처리기관'], dropna=False).size() if '_agency_key' in frame else None
    status_by = {}
    for (gid, status), count in status_counts.items():
        label = str(status)
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
