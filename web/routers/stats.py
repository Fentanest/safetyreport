from fastapi import APIRouter, Request
import settings.settings as app_settings
from core.database.engine import get_engine
from core.utils import csrf
from services import data_service, geocode_service, sunwi_service
from core.utils.templating import templates
from web.routers.filters import default_dedupe_mode, normalize_dedupe_mode, normalize_map_category

router = APIRouter()
engine = get_engine()

# 통계 화면의 공통 조건(연도·법규·상세 검색). 지도(`/stats/map`)도 같은 이름으로 받는다(2026-09-28).
STATS_FILTER_KEYS = (
    "reportName", "law", "location",
    "reportDateStart", "reportDateEnd", "occurDateStart", "occurDateEnd",
    "responseDateStart", "responseDateEnd", "occurTimeStart", "occurTimeEnd",
    "agency", "agencyExact", "excludePolice", "onlyPolice",
)
_BOOL_FILTER_KEYS = {"agencyExact", "excludePolice", "onlyPolice"}


def _query_filters(request: Request) -> dict:
    """쿼리 문자열에서 통계 공통 조건만 읽는다(빈 값은 버림). 불리언은 'true'/'1'/'on' 만 참."""
    filters = {}
    for key in STATS_FILTER_KEYS:
        raw = request.query_params.get(key)
        if raw is None or raw == "":
            continue
        filters[key] = raw.strip().lower() in {"true", "1", "on"} if key in _BOOL_FILTER_KEYS else raw
    return filters


def _map_filters(request: Request) -> dict:
    filters = _query_filters(request)
    for key in ("targetAgency", "targetPerson"):
        value = (request.query_params.get(key) or "").strip()
        if value:
            filters[key] = value
    return filters

@router.get("/stats/content")
@router.get("/stats")
def view_stats(
    request: Request,
    reportName: str = None,
    law: str = None,
    location: str = None,
    reportDateStart: str = None,
    reportDateEnd: str = None,
    occurDateStart: str = None,
    occurDateEnd: str = None,
    responseDateStart: str = None,
    responseDateEnd: str = None,
    occurTimeStart: str = None,
    occurTimeEnd: str = None,
    agency: str = None,
    agencyExact: bool = False,
    excludePolice: bool = False,
    onlyPolice: bool = False,
    year: str = None,
    dedupe: str | None = None,
):
    filters = {
        'reportName': reportName,
        'law': law,
        'location': location,
        'reportDateStart': reportDateStart,
        'reportDateEnd': reportDateEnd,
        'occurDateStart': occurDateStart,
        'occurDateEnd': occurDateEnd,
        'responseDateStart': responseDateStart,
        'responseDateEnd': responseDateEnd,
        'occurTimeStart': occurTimeStart,
        'occurTimeEnd': occurTimeEnd,
        'agency': agency,
        'agencyExact': agencyExact,
        'excludePolice': excludePolice,
        'onlyPolice': onlyPolice,
        'year': year,
    }
    dedupe_mode = normalize_dedupe_mode(dedupe)
    # 표·요약 카드·차트는 한 번 읽은 같은 행에서 만든다(모바일 통계 요약과 같은 함수·조건) — statistics-spec §5, §9
    fragment = request.url.path == "/stats/content"
    if fragment:
        records, overview = data_service.get_stats_page(engine, filters, mode=dedupe_mode)
    else:
        import pandas as pd
        from services import report_stats_service as service
        empty = pd.DataFrame()
        records = service._compute_agency_stats([], empty.copy(), empty.copy(), empty.copy(), filters, dedupe_mode)
        overview = service._compute_stats_overview([], empty, empty, empty, filters, dedupe_mode)
    # 선택 항목 상세 패널·CSV 내보내기용 행 자료. 경찰/비경찰 표는 기관별·담당자별의 부분집합이라 두 목록만 내린다.
    table_rows = {
        cat: {"agency": records[cat]["by_agency"], "person": records[cat]["by_person"]}
        for cat in ("traffic", "parking", "other")
    }
    # 상세표 18개(분류 3 × 보기 6). 경찰/비경찰 보기는 기관별·담당자별의 부분집합(기관명에 '경찰').
    pane_rows = {
        cat: {
            "agency": records[cat]["by_agency"], "person": records[cat]["by_person"],
            "police_agency": records[cat]["police_by_agency"], "police_person": records[cat]["police_by_person"],
            "other_agency": records[cat]["other_by_agency"], "other_person": records[cat]["other_by_person"],
        }
        for cat in ("traffic", "parking", "other")
    }
    try:
        last_crawl_time = data_service.get_last_sync_label(engine)
    except Exception:
        last_crawl_time = "확인 불가"

    return templates.TemplateResponse(request, "stats.html", {
        "title": "통계",
        "fragment": fragment,
        "pending": not fragment,
        "last_crawl_time": last_crawl_time,
        "dedupe_mode": records.get("dedupe_mode", dedupe_mode),
        "exclude_withdraw": bool(app_settings.exclude_withdraw),
        "table_rows": table_rows,
        "pane_rows": pane_rows,
        # 전국 안전신고 현황(Sunwi): 대시보드에서 옮겼다. 개인 신고 통계와 다른 데이터셋이라 별도 구역에 둔다.
        "sunwi": sunwi_service.get_dashboard_payload(),
        "available_years": records.get("available_years", []),
        "overview": overview,
        "current_year": year or "all",
        # 2026-09-24 결정: 배너도 현재 필터를 반영한다(표 합계와 같은 값). API 의 traffic_total_fine 은 호환을 위해 그대로 둔다.
        "traffic_total_fine": records["traffic"].get("total_fine_amount", 0),
        "traffic_estimated_fine": records["traffic"].get("estimated_fine_amount", 0),
        "traffic_estimated_fine_count": records["traffic"].get("estimated_fine_count", 0),
        "records_traffic_agency":         records["traffic"]["by_agency"],
        "records_traffic_person":         records["traffic"]["by_person"],
        "records_traffic_police_agency":  records["traffic"]["police_by_agency"],
        "records_traffic_police_person":  records["traffic"]["police_by_person"],
        "records_traffic_other_agency":   records["traffic"]["other_by_agency"],
        "records_traffic_other_person":   records["traffic"]["other_by_person"],
        "records_traffic_law":            records["traffic"]["by_law"],
        "traffic_available_laws":         records["traffic"].get("available_laws", []),
        "parking_available_laws":         records["parking"].get("available_laws", []),
        "other_available_laws":           records["other"].get("available_laws", []),
        "traffic_has_empty_law":          records["traffic"].get("has_empty_law", False),
        "parking_has_empty_law":          records["parking"].get("has_empty_law", False),
        "other_has_empty_law":            records["other"].get("has_empty_law", False),
        "records_parking_agency":         records["parking"]["by_agency"],
        "records_parking_person":         records["parking"]["by_person"],
        "records_parking_police_agency":  records["parking"]["police_by_agency"],
        "records_parking_police_person":  records["parking"]["police_by_person"],
        "records_parking_other_agency":   records["parking"]["other_by_agency"],
        "records_parking_other_person":   records["parking"]["other_by_person"],
        "records_parking_law":            records["parking"]["by_law"],
        "records_other_agency":           records["other"]["by_agency"],
        "records_other_person":           records["other"]["by_person"],
        "records_other_police_agency":    records["other"]["police_by_agency"],
        "records_other_police_person":    records["other"]["police_by_person"],
        "records_other_other_agency":     records["other"]["other_by_agency"],
        "records_other_other_person":     records["other"]["other_by_person"],
        "records_other_law":              records["other"]["by_law"],
        "f": filters,
    })


@router.get("/stats/map")
def view_report_map(
    request: Request,
    year: str = None,
    category: str = "all",
    dedupe: str | None = None,
):
    # 통계 화면이 ?dedupe= 로 모드를 고른 경우 지도도 같은 모드(없으면 설정값 — 예전과 같음)
    dedupe_mode = normalize_dedupe_mode(dedupe)
    selected_category = normalize_map_category(category)
    map_error = ""
    # 통계 화면에서 넘어온 조건(법규·상세 검색·상세 패널 대상). 없으면 예전과 같은 전체 지도.
    map_filters = _map_filters(request)

    from services.report_stats_service import get_report_map_stats
    map_payload = get_report_map_stats(
        engine,
        year=year,
        category=selected_category,
        mode=dedupe_mode,
        filters=map_filters or None,
        max_points=1200,
    )
    missing_payload = data_service.get_report_map_missing_groups(
        engine,
        year=year,
        category=selected_category,
        mode=dedupe_mode,
        filters=map_filters or None,
    )
    meta = map_payload.get("meta", {})

    return templates.TemplateResponse(request, "report_map.html", {
        "title": "신고 지도",
        "map_points": map_payload.get("points", []),
        "map_meta": meta,
        "missing_map_groups": missing_payload.get("groups", []),
        "missing_map_meta": missing_payload.get("meta", {}),
        "map_error": map_error,
        "current_year": meta.get("current_year", year or "all"),
        "available_years": meta.get("available_years", []),
        "selected_category": meta.get("selected_category", selected_category),
        "dedupe_mode": meta.get("dedupe_mode", dedupe_mode),
        "map_filters": map_filters,
        # T4 커뮤니티 공유 패널의 POST(upload/run·reshare)용 CSRF 토큰 (그 밖 변경 없음)
        "csrf_token": csrf.get_or_create_token(request),
    })


@router.get("/stats/map/points")
def get_report_map_points(
    request: Request,
    year: str = None,
    category: str = "all",
    dedupe: str | None = None,
):
    """통계 화면의 작은 신고 지도용 JSON. 통계와 같은 조건을 받아 `/stats/map` 과 같은 함수로 집계한다."""
    from services.report_stats_service import get_report_map_stats
    from fastapi import HTTPException
    raw_bounds = request.query_params.get('bounds')
    try:
        import math
        bounds = tuple(float(v) for v in raw_bounds.split(',')) if raw_bounds else None
        if bounds and (len(bounds) != 4 or not all(math.isfinite(v) for v in bounds) or
                       not (-90 <= bounds[0] <= bounds[2] <= 90 and -180 <= bounds[1] <= bounds[3] <= 180)):
            raise ValueError()
        zoom = max(0, min(19, int(request.query_params.get('zoom', '7'))))
    except ValueError:
        raise HTTPException(400, '잘못된 지도 범위입니다.')
    return get_report_map_stats(
        engine,
        year=year,
        category=normalize_map_category(category),
        mode=normalize_dedupe_mode(dedupe),
        filters=_map_filters(request) or None,
        max_points=1200, bounds=bounds, zoom=zoom,
    )


@router.get("/stats/map/progress")
def get_report_map_progress():
    progress = geocode_service.idle_progress()
    return progress


@router.get("/stats/map/missing")
def get_report_map_missing(
    request: Request,
    year: str = None,
    category: str = "all",
    dedupe: str | None = None,
):
    dedupe_mode = normalize_dedupe_mode(dedupe)
    selected_category = normalize_map_category(category)
    return data_service.get_report_map_missing_groups(
        engine,
        year=year,
        category=selected_category,
        mode=dedupe_mode,
        filters=_map_filters(request) or None,
    )
