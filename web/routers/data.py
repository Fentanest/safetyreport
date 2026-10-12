from fastapi import APIRouter, Request, Query
from typing import Optional
from core.database.engine import get_engine
from services import data_service, fine_estimate
from core.utils.templating import templates
from web.routers.filters import normalize_dedupe_mode

engine = get_engine()
router = APIRouter(prefix="/data")

# 목록 화면 전용 표시값(docs/design/statistics-spec.md §4-1·§4-2, PROJECT_RULES §3-2): 추정은 DB 에 저장하지
# 않고 조회마다 계산한다. 판정은 services/fine_estimate.estimate_if_unknown 을 그대로 쓴다(재구현 금지) —
# 로컬 통계(services/stats/metrics.py)·공유 payload(services/community_capture.py)와 같은 입력 열
# (fine_estimate.FINE_ESTIMATE_FIELDS)만 record 에서 골라 넘긴다. 이 라우터(웹 /data 화면)에서만 붙이고
# 모바일 API(web/routers/api_route.py, exact_values=True)는 거치지 않으므로 계약에 영향이 없다.
_FINE_ESTIMATE_RECORD_FIELDS = fine_estimate.FINE_ESTIMATE_FIELDS


def _with_fine_estimates(records: list[dict]) -> list[dict]:
    for record in records:
        fields = {key: record.get(key) for key in _FINE_ESTIMATE_RECORD_FIELDS}
        result = fine_estimate.estimate_if_unknown(record.get("범칙금_과태료"), fields)
        record["fine_estimate_amount"] = result["amount"] if result else None
        record["fine_estimate_basis"] = result["basis"] if result else None
    return records


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
    for key in ('excludePolice', 'onlyPolice'):
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
