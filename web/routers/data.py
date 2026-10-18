from fastapi import APIRouter, Request, Query
from typing import Optional
from core.database.engine import get_engine
from services import data_service, fine_estimate
from core.utils.templating import templates
from web.routers.filters import normalize_dedupe_mode, with_fine_estimates

engine = get_engine()
router = APIRouter(prefix="/data")

# 추정 과태료 부착(판정은 services/fine_estimate.estimate_if_unknown, 재구현 금지)은 web/routers/filters.py
# with_fine_estimates() 로 옮겨 watchlist_route.py·rating_route.py·duplicate_route.py·dashboard.py 와
# 공유한다(2026-10-12, watchlist/rating/duplicate_groups/대시보드 확장). 이 모듈의 호출부는 그대로 둔다.
# web_data.fine_estimate(아래 import)는 tests/test_data_table_fine_estimate.py 가 그대로 patch 한다 —
# with_fine_estimates() 도 같은 모듈 객체의 estimate_if_unknown 을 호출 시점에 참조하므로 patch 가 유효하다.
_with_fine_estimates = with_fine_estimates


def _build_filters(status=None, fine=None, agency=None, person=None, agencyExact=False, law=None, rating=None, ratingCause=None):
    f = {}
    if status: f['status'] = status
    if fine: f['fine'] = fine
    if agency: f['agency'] = agency
    if person: f['person'] = person
    if agencyExact: f['agencyExact'] = True
    if law: f['law'] = law
    if rating: f['rating'] = rating
    if ratingCause: f['ratingCause'] = ratingCause
    return f or None


def _filter_title(base, status=None, fine=None, agency=None, person=None, law=None, rating=None, ratingCause=None):
    parts = [base]
    if agency: parts.append(f'기관: {agency}')
    if person: parts.append(f'담당자: {person}')
    if status: parts.append(f'상태: {status}')
    if fine: parts.append(f'과태료: {fine}')
    if law: parts.append(f'위반법규: {law}')
    if rating:
        parts.append('별점: 없음' if rating == '__none__' else f'별점: {rating}점')
    if ratingCause: parts.append(f'별점사유: {ratingCause}')
    return ' / '.join(parts)


def _identity_filters(request: Request, filters: dict | None) -> dict | None:
    key = request.query_params.get("agencyKey")
    if key:
        filters = dict(filters or {}, agencyKey=key)
    for key in ('excludePolice', 'onlyPolice', 'personExact'):
        if request.query_params.get(key) == 'true':
            filters = dict(filters or {}, **{key: True})
    if filters and request.query_params.get("lawExact") == "true":
        filters["lawExact"] = True
    return filters


@router.get("/traffic")
def view_traffic(
    request: Request,
    status: Optional[str] = Query(None),
    fine: Optional[str] = Query(None),
    agency: Optional[str] = Query(None),
    person: Optional[str] = Query(None),
    agencyExact: bool = Query(False),
    law: Optional[str] = Query(None),
    rating: Optional[str] = Query(None),
    ratingCause: Optional[str] = Query(None),
    dedupe: str | None = Query(None),
):
    filters = _build_filters(status, fine, agency, person, agencyExact, law, rating, ratingCause)
    filters = _identity_filters(request, filters)
    dedupe_mode = normalize_dedupe_mode(dedupe)
    records = _with_fine_estimates(data_service.get_traffic_records(engine, filters, mode=dedupe_mode))
    title = _filter_title("교통위반 전체 보기", status, fine, agency, person, law, rating, ratingCause)
    return templates.TemplateResponse(request, "data_table.html", {
        "title": title,
        "records": records, "table_id": "trafficTable",
        "order_col": '[ 4, "desc" ]',
    })


@router.get("/parking")
def view_parking(
    request: Request,
    status: Optional[str] = Query(None),
    fine: Optional[str] = Query(None),
    agency: Optional[str] = Query(None),
    person: Optional[str] = Query(None),
    agencyExact: bool = Query(False),
    law: Optional[str] = Query(None),
    rating: Optional[str] = Query(None),
    ratingCause: Optional[str] = Query(None),
    dedupe: str | None = Query(None),
):
    filters = _build_filters(status, fine, agency, person, agencyExact, law, rating, ratingCause)
    filters = _identity_filters(request, filters)
    dedupe_mode = normalize_dedupe_mode(dedupe)
    records = _with_fine_estimates(data_service.get_parking_records(engine, filters, mode=dedupe_mode))
    title = _filter_title("주정차위반 내역", status, fine, agency, person, law, rating, ratingCause)
    return templates.TemplateResponse(request, "data_table.html", {
        "title": title,
        "records": records, "table_id": "parkingTable",
        "order_col": '[ 4, "desc" ]',
    })


@router.get("/safety")
def view_safety(
    request: Request,
    status: Optional[str] = Query(None),
    fine: Optional[str] = Query(None),
    agency: Optional[str] = Query(None),
    person: Optional[str] = Query(None),
    agencyExact: bool = Query(False),
    law: Optional[str] = Query(None),
    rating: Optional[str] = Query(None),
    ratingCause: Optional[str] = Query(None),
    dedupe: str | None = Query(None),
):
    filters = _build_filters(status, fine, agency, person, agencyExact, law, rating, ratingCause)
    filters = _identity_filters(request, filters)
    dedupe_mode = normalize_dedupe_mode(dedupe)
    records = _with_fine_estimates(data_service.get_safety_records(engine, filters, mode=dedupe_mode))
    title = _filter_title("안전신고 조회", status, fine, agency, person, law, rating, ratingCause)
    return templates.TemplateResponse(request, "data_table.html", {
        "title": title,
        "records": records, "table_id": "safetyTable",
        "order_col": '[ 4, "desc" ]',
    })


@router.get("/living")
def view_living(
    request: Request,
    status: Optional[str] = Query(None),
    fine: Optional[str] = Query(None),
    agency: Optional[str] = Query(None),
    person: Optional[str] = Query(None),
    agencyExact: bool = Query(False),
    law: Optional[str] = Query(None),
    rating: Optional[str] = Query(None),
    ratingCause: Optional[str] = Query(None),
    dedupe: str | None = Query(None),
):
    filters = _build_filters(status, fine, agency, person, agencyExact, law, rating, ratingCause)
    filters = _identity_filters(request, filters)
    dedupe_mode = normalize_dedupe_mode(dedupe)
    records = _with_fine_estimates(data_service.get_living_records(engine, filters, mode=dedupe_mode))
    title = _filter_title("생활불편 조회", status, fine, agency, person, law, rating, ratingCause)
    return templates.TemplateResponse(request, "data_table.html", {
        "title": title,
        "records": records, "table_id": "livingTable",
        "order_col": '[ 4, "desc" ]',
    })


@router.get("/other")
def view_other(
    request: Request,
    status: Optional[str] = Query(None),
    fine: Optional[str] = Query(None),
    agency: Optional[str] = Query(None),
    person: Optional[str] = Query(None),
    agencyExact: bool = Query(False),
    law: Optional[str] = Query(None),
    rating: Optional[str] = Query(None),
    ratingCause: Optional[str] = Query(None),
    dedupe: str | None = Query(None),
):
    filters = _build_filters(status, fine, agency, person, agencyExact, law, rating, ratingCause)
    filters = _identity_filters(request, filters)
    dedupe_mode = normalize_dedupe_mode(dedupe)
    records = _with_fine_estimates(data_service.get_other_records(engine, filters, mode=dedupe_mode))
    title = _filter_title("기타 조회", status, fine, agency, person, law, rating, ratingCause)
    return templates.TemplateResponse(request, "data_table.html", {
        "title": title,
        "records": records, "table_id": "otherTable",
        "order_col": '[ 4, "desc" ]',
    })


@router.get("/all")
def view_all(
    request: Request,
    status: Optional[str] = Query(None),
    fine: Optional[str] = Query(None),
    agency: Optional[str] = Query(None),
    person: Optional[str] = Query(None),
    agencyExact: bool = Query(False),
    law: Optional[str] = Query(None),
    rating: Optional[str] = Query(None),
    ratingCause: Optional[str] = Query(None),
    dedupe: str | None = Query(None),
):
    filters = _build_filters(status, fine, agency, person, agencyExact, law, rating, ratingCause)
    filters = _identity_filters(request, filters)
    dedupe_mode = normalize_dedupe_mode(dedupe)
    records = _with_fine_estimates(data_service.get_all_records(engine, filters, mode=dedupe_mode))
    title = _filter_title("전체 신고 조회", status, fine, agency, person, law, rating, ratingCause)
    return templates.TemplateResponse(request, "data_table.html", {
        "title": title,
        "records": records, "table_id": "allTable",
        "order_col": '[ 4, "desc" ]',
    })


@router.get("/duplicates")
def view_duplicates(request: Request, dedupe: str | None = Query(None)):
    dedupe_mode = normalize_dedupe_mode(dedupe)
    records = _with_fine_estimates(data_service.get_duplicate_records(engine, mode=dedupe_mode))
    return templates.TemplateResponse(request, "data_table.html", {
        "title": "중복 차량 (2건 이상) 보기",
        "records": records, "table_id": "duplicateTable",
        "order_col": '[ 6, "asc" ]',
    })
