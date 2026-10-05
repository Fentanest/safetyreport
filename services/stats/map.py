"""지도 묶음·좌표 없는 신고 응답 조립(EO R-05)."""

import pandas as pd

from services import report_policy
from services.report_cache import cached
from services.stats.common import _MAP_COLUMNS, _MAP_MISSING_COLUMNS, _first_nonempty_value, _is_finite_number, _normalize_mode, _ratio_item, _row_to_dict, _sanitize_jsonable, _text_or_empty
from services.stats.reads import _load_map_records_frame


def normalize_pin_basis(value: str | None) -> str:
    """핀 기준 정규화: `address` 외 모두 `coords`(기본, 기존 동작)."""
    return "address" if isinstance(value, str) and value.strip().lower() == "address" else "coords"


def _clean_text(value) -> str:
    """None·NaN 을 빈 문자열로, 그 밖은 trim 한 문자열로(클러스터 라벨·주소키용)."""
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def cluster_cell_label(pairs) -> dict:
    """공간 칸 묶음 점(cluster=true)의 이름(순수 함수, 입력 불변).

    정본: `contracts/map-cluster-label-vectors.json` description.
    `pairs` 는 칸 안 신고들의 `(주소정규화, 위반장소)` 쌍(각각 None 가능).
    주소키 = trim(주소정규화), 비면 trim(위반장소).
    address_count = 빈 주소키를 뺀 종류 수.
    대표 주소키 = 신고 수가 가장 많은 주소키(동률이면 문자열 비교로 작은 것).
    address = 대표 주소키 신고들의 빈 문자열이 아닌 trim(위반장소) 중 가장 작은 값,
    없으면 대표 주소키, 주소키가 하나도 없으면 빈 문자열.
    region = address_count 가 0 이면 '주소 정보 없음', 1 이면 address,
    2 이상이면 '{address} 외 {address_count-1}곳'.
    """
    counts: dict[str, int] = {}
    displays: dict[str, list[str]] = {}
    for normalized, place in pairs:
        key = _clean_text(normalized)
        if not key:
            key = _clean_text(place)
        if not key:
            continue
        counts[key] = counts.get(key, 0) + 1
        text = _clean_text(place)
        if text:
            displays.setdefault(key, []).append(text)
    address_count = len(counts)
    if not counts:
        return {"address": "", "address_count": 0, "region": "주소 정보 없음"}
    winner = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]
    candidates = displays.get(winner, [])
    address = min(candidates) if candidates else winner
    if address_count == 1:
        region = address
    else:
        region = f"{address} 외 {address_count - 1}곳"
    return {"address": address, "address_count": address_count, "region": region}


def apply_pin_basis(frame, basis: str = "coords"):
    """표시용 effective 좌표를 적용한 사본을 돌려준다(입력 프레임은 바꾸지 않는다).

    `coords`(기본): 그대로(기존 동작과 같은 행·좌표).
    `address`: 주소키가 있는 신고는 같은 주소키 신고들의 유효 공식 좌표 중 가장 많이 나온
    (위도, 경도) 쌍에 찍는다. 동률이면 위도가 작은 쌍, 다음 경도가 작은 쌍.
    자기 좌표가 없어도 같은 주소에 유효 좌표가 있으면 찍히고, 주소키가 비면 자기 좌표 그대로,
    같은 주소에 유효 좌표가 하나도 없으면 좌표 없음. DB 저장값은 바꾸지 않는다.
    """
    result = frame.copy()
    if normalize_pin_basis(basis) != "address" or result.empty:
        return result
    if "주소키" not in result.columns or "위도" not in result.columns or "경도" not in result.columns:
        return result
    keys = result["주소키"].fillna("").astype(str).str.strip()
    valid = result["유효좌표"].fillna(False).astype(bool) if "유효좌표" in result.columns else (
        result["위도"].apply(_is_finite_number) & result["경도"].apply(_is_finite_number)
    )
    usable = valid & (keys != "")
    if usable.any():
        # 주소키마다 전체 마스크를 만들지 않고 유효 좌표 쌍의 건수를 한 번에 센다.
        sub = pd.DataFrame({
            "_key": keys[usable].to_numpy(),
            "_lat": pd.to_numeric(result.loc[usable, "위도"], errors="coerce").to_numpy(dtype="float64"),
            "_lng": pd.to_numeric(result.loc[usable, "경도"], errors="coerce").to_numpy(dtype="float64"),
        })
        counts = sub.groupby(["_key", "_lat", "_lng"], sort=False).size().reset_index(name="_n")
        counts = counts.sort_values(["_key", "_n", "_lat", "_lng"], ascending=[True, False, True, True])
        winners = counts.drop_duplicates("_key", keep="first").set_index("_key")[["_lat", "_lng"]]
        mask = keys.isin(winners.index)
        result.loc[mask, "위도"] = keys[mask].map(winners["_lat"].to_dict())
        result.loc[mask, "경도"] = keys[mask].map(winners["_lng"].to_dict())
    result["유효좌표"] = result["위도"].apply(_is_finite_number) & result["경도"].apply(_is_finite_number)
    return result


@cached
def get_report_map_stats(engine, *, year: str | None = None, category: str = "all", mode: str = "canonical", filters: dict | None = None, max_points: int | None = None, bounds: tuple | None = None, zoom: int = 7, pin_basis: str | None = None):
    category, available_years, combined_df = _load_map_records_frame(
        engine,
        year=year,
        category=category,
        mode=mode,
        column_names=_MAP_COLUMNS,
        filters=filters,
    )
    basis = normalize_pin_basis(pin_basis)
    combined_df = apply_pin_basis(combined_df, basis)

    if combined_df.empty:
        return _sanitize_jsonable({
            "points": [],
            "meta": {
                "available_years": available_years,
                "current_year": year or "all",
                "selected_category": category,
                "dedupe_mode": _normalize_mode(mode),
                "pin_basis": basis,
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
            "pin_basis": basis,
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
    labels_by_group: dict = {}
    if clustered:
        normalized_all = frame["주소정규화"] if "주소정규화" in frame.columns else pd.Series([""] * len(frame), index=frame.index)
        places_all = frame["위반장소"] if "위반장소" in frame.columns else pd.Series([""] * len(frame), index=frame.index)
        for gid, group in grouped:
            pairs = list(zip(normalized_all.loc[group.index].tolist(), places_all.loc[group.index].tolist()))
            labels_by_group[gid] = cluster_cell_label(pairs)
    for gid, row in base.iterrows():
        total = int(row.total)
        agency = sorted(agencies_by.get(gid, []), key=lambda a: (-a['count'],a['name'],a['agency_key']))
        for a in agency: a['pct'] = round(a['count'] / sum(i['count'] for i in agency) * 100, 1)
        if clustered:
            cell_label = labels_by_group[gid]
            address_text, region_text = cell_label["address"], cell_label["region"]
        else:
            address_text, region_text = str(row.address), str(row.region or row.address)
        point = dict(lat=float(row.lat), lng=float(row.lng), total=total,
                     address=address_text,
                     region=region_text,
                     status_breakdown=[_ratio_item(k,v,total) for k,v in status_by.get(gid,{}).items()],
                     disposition_breakdown=[_ratio_item(label,int(dispositions.loc[gid,key]),total) for key,label in
                         [('_fine','과태료'),('_warning','경고/범칙금'),('_reject','불수용/기타'),('_unknown','미확인')]
                         if dispositions.loc[gid,key] > 0],
                     agency_breakdown=agency,
                     category_breakdown=[_ratio_item(label,categories_by.get(gid,{}).get(key,0),total) for key,label in
                         [('traffic','교통위반'),('parking','주정차위반'),('other','기타위반')]])
        if clustered: point['cluster'] = True
        if clustered: point['address_count'] = int(labels_by_group[gid]["address_count"])
        points.append(point)
    return points


def get_report_map_missing_summary(engine, *, year: str | None = None, category: str = "all", mode: str = "canonical",
                                    filters: dict | None = None, pin_basis: str | None = None) -> dict:
    """지도 첫 화면용: 좌표 없는 신고의 주소 그룹 수·신고 수만. 목록(본문 열 포함)은 모달을 열 때
    /stats/map/missing 으로 받는다(기술일지 B-03). 판정은 get_report_map_missing_groups 와 같다."""
    category, _available_years, combined_df = _load_map_records_frame(
        engine, year=year, category=category, mode=mode, column_names=_MAP_COLUMNS, filters=filters)
    combined_df = apply_pin_basis(combined_df, normalize_pin_basis(pin_basis))
    if combined_df.empty:
        return {"group_count": 0, "report_count": 0}
    missing = combined_df[(combined_df["주소키"].str.strip() != "") & ~combined_df["유효좌표"]]
    return {"group_count": int(missing["주소키"].nunique(dropna=False)), "report_count": int(len(missing))}


def get_report_map_missing_groups(engine, *, year: str | None = None, category: str = "all", mode: str = "canonical", filters: dict | None = None, pin_basis: str | None = None):
    category, available_years, combined_df = _load_map_records_frame(
        engine,
        year=year,
        category=category,
        mode=mode,
        column_names=_MAP_MISSING_COLUMNS,
        filters=filters,
    )
    combined_df = apply_pin_basis(combined_df, normalize_pin_basis(pin_basis))

    if combined_df.empty:
        return _sanitize_jsonable({
            "groups": [],
            "meta": {
                "available_years": available_years,
                "current_year": year or "all",
                "selected_category": category,
                "dedupe_mode": _normalize_mode(mode),
                "pin_basis": normalize_pin_basis(pin_basis),
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
                "pin_basis": normalize_pin_basis(pin_basis),
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
            "pin_basis": normalize_pin_basis(pin_basis),
            "group_count": int(len(groups)),
            "report_count": int(len(missing_df)),
        },
    })
