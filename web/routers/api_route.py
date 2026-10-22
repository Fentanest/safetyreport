import os

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, Query
from starlette.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.security import APIKeyHeader
from starlette.background import BackgroundTask

import settings.settings as settings
from core.database import database
from core.database.engine import get_engine
from services import crawl_control, crawl_state_store, data_service, db_editor_service, duplicate_group_service, file_service, geocode_service, rating_eligibility, rating_service, sunwi_service
from services.crawl_manager import crawl_manager
from services.ws_manager import ws_manager
from services import selfhost_compat
from services.client_report_search import IDENTITY_SEARCH_CAPABILITY
from core.utils.request_body import json_object, string_list
from core.utils.temporary_response import TemporaryFileResponse
from web.routers.filters import default_dedupe_mode, normalize_dedupe_mode

router = APIRouter(prefix="/api/v1")
engine = get_engine()

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
_api_key_query_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def _require_api_key(request: Request, api_key: str = Depends(_api_key_header)):
    valid = getattr(request.state, 'api_key_valid', None)
    if not api_key or not (valid if valid is not None else database.validate_api_key(engine, api_key)):
        raise HTTPException(status_code=401, detail="유효하지 않은 API 키입니다.")
    device_name = database.get_api_key_name(engine, api_key)
    ip = request.headers.get("X-Forwarded-For", request.client.host if request.client else "")
    ws_manager.track_api_request(api_key, device_name, ip)
    return api_key


def _require_api_key_flex(request: Request, header_key: str = Depends(_api_key_query_header)):
    api_key = header_key or request.query_params.get("api_key", "")
    valid = getattr(request.state, 'api_key_valid', None)
    if not api_key or not (valid if valid is not None else database.validate_api_key(engine, api_key)):
        raise HTTPException(status_code=401, detail="유효하지 않은 API 키입니다.")
    return api_key


@router.get("/summary")
def get_summary(request: Request, dedupe: str | None = None, _: str = Depends(_require_api_key)):
    try:
        dedupe_mode = normalize_dedupe_mode(dedupe)
        data = data_service.get_dashboard_stats(engine, mode=dedupe_mode)
        # 하위호환 추가 필드(agency_name_current·agency_key, 원문 처리기관은 그대로) — deepcopy 결과라 캐시엔 영향 없음.
        from services.agency_registry import with_current_agency_fields
        with_current_agency_fields(data.get("recent_answers") or [])
        with_current_agency_fields(data.get("watchlist") or [])
        return {"status": "success", "data": data}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


def _report_categories(request: Request, category: str):
    """요청 분류 → (실제 분류 묶음, 행에 보일 category). 5분류를 모르는 앱의 'other' 는 예전 '기타' 묶음(safety·living·other)."""
    from core.utils import taxonomy_compat
    from services import report_category

    if category not in report_category.CATEGORIES:
        raise HTTPException(status_code=400, detail="Invalid category")
    if category == "other" and not taxonomy_compat.wants_five(request):
        return report_category.expand_legacy("other"), "other"
    return (category,), None


@router.get("/reports/{report_id}/raw-detail")
def get_report_raw_detail(report_id: str, _: str = Depends(_require_api_key)):
    """상세 원본 보기(Client 모드용, 2026-10-17 명세 4절, 추가만).

    → `{id, fetched_at, source, raw_sha256, json}`(`json` 은 신고자 키 5개가
    이미 빠진 저장 원본 그대로, 객체). 없음 404 `{error: "raw_detail_missing"}`,
    손상 409 `{error: "raw_detail_corrupt"}`. ID 검증 실패(경로 이탈 시도)도
    구분해 알리지 않고 404 로 답한다.
    """
    from services import raw_detail_service

    try:
        return raw_detail_service.read(engine, report_id)
    except raw_detail_service.RawDetailMissing:
        return JSONResponse({"error": "raw_detail_missing"}, status_code=404)
    except raw_detail_service.RawDetailCorrupt:
        return JSONResponse({"error": "raw_detail_corrupt"}, status_code=409)


@router.get("/reports/{category}")
def get_reports(request: Request, category: str, dedupe: str | None = None, _: str = Depends(_require_api_key)):
    try:
        dedupe_mode = normalize_dedupe_mode(dedupe)
        if category == "duplicates":
            records = data_service.get_duplicate_records(engine, mode=dedupe_mode)
        else:
            categories, label = _report_categories(request, category)
            records = data_service.get_category_records(engine, categories, mode=dedupe_mode, exact_values=True,
                                                        label=label)
        return {"status": "success", "category": category, "count": len(records), "data": records}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get('/reports/{category}/page')
def get_reports_page(request: Request, category: str, offset: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=1000),
                     dedupe: str | None = None, _: str = Depends(_require_api_key)):
    categories, label = _report_categories(request, category)
    from services.report_query_service import get_report_page
    page = get_report_page(engine, categories, offset=offset, limit=limit, mode=normalize_dedupe_mode(dedupe),
                           label=label or category)
    return {'status':'success', **page}


@router.get('/reports/{category}/search')
def search_reports(request: Request, category: str, search_version: int = Query(...),
                   filter: str = Query('{}', max_length=32768), offset: int = Query(0, ge=0),
                   limit: int = Query(200, ge=1, le=200), dedupe: str = Query('canonical'),
                   _: str = Depends(_require_api_key)):
    import json
    from time import perf_counter
    from services.client_report_search import VERSION, validate_filter
    from services.report_query_service import get_report_page
    if search_version != VERSION or dedupe not in ('canonical', 'raw'):
        raise HTTPException(400, 'Unsupported search contract')
    try:
        filters = validate_filter(json.loads(filter))
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    categories, label = (None, None) if category == 'all' else _report_categories(request, category)
    started = perf_counter()
    page = get_report_page(engine, categories, offset=offset, limit=limit, mode=dedupe,
                           label=label, search_filter=filters)
    return {'status': 'success', **page, 'category': category, 'search_version': VERSION,
            'filter': filters, 'server_ms': (perf_counter() - started) * 1000}


@router.get("/vehicle/{vehicle_number}")
def get_vehicle_reports(vehicle_number: str, dedupe: str | None = None, _: str = Depends(_require_api_key)):
    try:
        dedupe_mode = normalize_dedupe_mode(dedupe)
        results = data_service.search_by_vehicle(engine, vehicle_number, mode=dedupe_mode)
        # 하위호환 추가 필드: 크롬 확장이 담당자를 기관 키로 묶고 현행 기관명을 보인다(원문 처리기관은 그대로).
        from services.agency_registry import with_current_agency_fields
        with_current_agency_fields(results)
        return {"status": "success", "vehicle_number": vehicle_number, "count": len(results), "data": results}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/address")
def get_address_reports(q: str, dedupe: str | None = None, _: str = Depends(_require_api_key)):
    try:
        dedupe_mode = normalize_dedupe_mode(dedupe)
        results = data_service.search_by_address(engine, q, mode=dedupe_mode)
        from services.agency_registry import with_current_agency_fields
        with_current_agency_fields(results)  # /vehicle 과 같은 하위호환 추가 필드
        return {"status": "success", "address": q, "count": len(results), "data": results}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/stats")
def get_stats(_: str = Depends(_require_api_key), year: str = None, law: str = None, dedupe: str | None = None, request: Request = None):
    try:
        from core.utils import taxonomy_compat
        filters = {}
        if year and year != "all":
            filters["year"] = year
        if law:
            filters["law"] = law
        dedupe_mode = normalize_dedupe_mode(dedupe)
        taxonomy = 5 if request is not None and taxonomy_compat.wants_five(request) else 3
        return {"status": "success", "data": data_service.get_agency_stats(engine, filters or None, mode=dedupe_mode, taxonomy=taxonomy)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))



@router.get("/stats/overview")
def get_stats_overview(_: str = Depends(_require_api_key), year: str = None, law: str = None, dedupe: str | None = None, request: Request = None):
    try:
        from core.utils import taxonomy_compat
        filters = {}
        if year and year != "all":
            filters["year"] = year
        if law:
            filters["law"] = law
        dedupe_mode = normalize_dedupe_mode(dedupe)
        taxonomy = 5 if request is not None and taxonomy_compat.wants_five(request) else 3
        return {"status": "success", "data": data_service.get_stats_overview(engine, filters or None, mode=dedupe_mode, taxonomy=taxonomy)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

@router.get("/stats/map")
def get_stats_map(
    _: str = Depends(_require_api_key),
    year: str | None = None,
    category: str = "all",
    pin_basis: str | None = None,
    request: Request = None,
):
    try:
        from core.utils import taxonomy_compat
        taxonomy = 5 if request is not None and taxonomy_compat.wants_five(request) else 3
        payload = data_service.get_report_map_stats(
            engine,
            year=year,
            category=category,
            mode=default_dedupe_mode(),
            pin_basis=pin_basis,
            taxonomy=taxonomy,
        )
        return {"status": "success", "data": payload}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get('/stats/map/points')
def get_stats_map_points(_: str = Depends(_require_api_key), year: str | None = None, category: str = 'all',
                         dedupe: str | None = None, max_points: int = Query(1200, ge=1, le=1200),
                         bounds: str | None = None, zoom: int = Query(7, ge=0, le=19), law: str | None = None,
                         pin_basis: str | None = None, request: Request = None):
    import math
    from core.utils import taxonomy_compat
    viewport = None
    if bounds:
        try:
            viewport = tuple(float(v) for v in bounds.split(','))
            south, west, north, east = viewport
            if not all(math.isfinite(v) for v in viewport) or not (-90<=south<=north<=90 and -180<=west<=east<=180):
                raise ValueError()
        except ValueError:
            raise HTTPException(status_code=400, detail='bounds must be south,west,north,east')
    taxonomy = 5 if request is not None and taxonomy_compat.wants_five(request) else 3
    payload = data_service.get_report_map_stats(engine,year=year,category=category,mode=normalize_dedupe_mode(dedupe),
             filters={'law':law} if law else None,max_points=max_points,bounds=viewport,zoom=zoom,pin_basis=pin_basis,
             taxonomy=taxonomy)
    return {'status':'success','data':payload}


@router.get("/stats/map/missing")
def get_stats_map_missing(
    _: str = Depends(_require_api_key),
    year: str | None = None,
    category: str = "all",
    pin_basis: str | None = None,
    request: Request = None,
):
    try:
        from core.utils import taxonomy_compat
        taxonomy = 5 if request is not None and taxonomy_compat.wants_five(request) else 3
        payload = data_service.get_report_map_missing_groups(
            engine,
            year=year,
            category=category,
            mode=default_dedupe_mode(),
            pin_basis=pin_basis,
            taxonomy=taxonomy,
        )
        return {"status": "success", "data": payload}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/maintenance/status")
def get_maintenance_status(_: str = Depends(_require_api_key)):
    """업데이트 뒤 한 번 훑기 작업(사진 촬영 시각·지도 좌표) 진행 — Client 앱 하단 표시줄용."""
    from services import maintenance_service

    return {"status": "success", "data": maintenance_service.status(engine)}


@router.get("/stats/map/progress")
def get_stats_map_progress(_: str = Depends(_require_api_key)):
    try:
        progress = geocode_service.idle_progress()
        return {"status": "success", "data": progress}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/sunwi/payload")
def get_sunwi_payload(_: str = Depends(_require_api_key)):
    try:
        return {"status": "success", "data": sunwi_service.get_dashboard_payload()}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/sunwi/export/{kind}")
def export_sunwi_csv(kind: str, _: str = Depends(_require_api_key)):
    normalized = kind.strip().lower()
    if normalized not in {"all", "top5"}:
        raise HTTPException(status_code=400, detail="kind must be 'all' or 'top5'")

    try:
        csv_path = sunwi_service.ensure_csv(normalized)
        return {
            "status": "success",
            "kind": normalized,
            "path": csv_path,
            "filename": os.path.basename(csv_path),
            "results_dir": sunwi_service.get_results_dir(),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/watchlist")
def get_watchlist(_: str = Depends(_require_api_key)):
    try:
        items = data_service.get_all_watchlist(engine)
        return {"status": "success", "count": len(items), "data": items}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/watchlist")
async def update_watchlist(request: Request, _: str = Depends(_require_api_key)):
    body = await json_object(request)
    report_numbers = string_list(body, "report_numbers")
    action = body.get('action', 'remove')
    if action not in ('add', 'remove'):
        raise HTTPException(status_code=400, detail='action must be add or remove')
    if not report_numbers:
        raise HTTPException(status_code=400, detail="report_numbers is required")
    try:
        updated = await run_in_threadpool(data_service.update_watchlist_status, engine, report_numbers, "Y" if action == "add" else "N")
        return {"status": "success", "updated": updated}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/duplicates/groups")
def get_duplicate_groups(status: str | None = None, _: str = Depends(_require_api_key)):
    try:
        groups = duplicate_group_service.get_duplicate_groups(engine, status=status)
        return {"status": "success", "count": len(groups), "data": groups}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/duplicates/groups/bulk-status")
async def bulk_update_duplicate_group_status_api(request: Request, _: str = Depends(_require_api_key)):
    body = await json_object(request)
    group_ids = string_list(body, "group_ids")
    duplicate_status = body.get("duplicate_status", "")
    representative_mode = body.get("representative_mode", "")
    if not isinstance(group_ids, list) or not group_ids:
        raise HTTPException(status_code=400, detail="group_ids is required")
    try:
        updated = await run_in_threadpool(
            duplicate_group_service.bulk_update_duplicate_status,
            engine,
            group_ids,
            duplicate_status,
            representative_mode=representative_mode,
        )
        return {"status": "success", "updated": updated}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/duplicates/groups/{group_id}")
async def update_duplicate_group_api(group_id: str, request: Request, _: str = Depends(_require_api_key)):
    body = await json_object(request)
    try:
        updated = await run_in_threadpool(
            duplicate_group_service.update_duplicate_group,
            engine,
            group_id,
            representative_id=body.get("representative_id"),
            duplicate_status=body.get("duplicate_status"),
            representative_mode=body.get("representative_mode"),
            note=body.get("note"),
        )
        if not updated:
            raise HTTPException(status_code=404, detail="중복군을 찾을 수 없습니다.")
        return {"status": "success"}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/editor/schema")
def get_editor_schema(_: str = Depends(_require_api_key)):
    return {"status": "success", "data": db_editor_service.get_editor_schema()}


def _editor_categories(category: str):
    """편집기는 ID 로 찾으므로 'other' 를 예전 '기타' 묶음까지 넓혀도 다른 신고가 섞이지 않는다(구앱 호환)."""
    from services import report_category

    if category not in report_category.CATEGORIES:
        return ()
    return report_category.expand_legacy(category)


@router.get("/editor/{category}/{record_id}")
def get_editor_record(category: str, record_id: str, _: str = Depends(_require_api_key)):
    record = db_editor_service.get_record(engine, _editor_categories(category), record_id)
    if not record:
        raise HTTPException(status_code=404, detail="수정 대상을 찾을 수 없습니다.")
    return {
        "status": "success",
        "category": category,
        "record_id": record_id,
        "data": {
            "record": record,
            **db_editor_service.get_editor_schema(),
            # 추가 필드(구앱은 무시): 사용자가 고친 필드와 사이트 원본 값(저장 계층 재설계 R4)
            **db_editor_service.get_edit_state(engine, _editor_categories(category), record_id),
        },
    }


@router.post("/editor/{category}/{record_id}")
async def save_editor_record(category: str, record_id: str, request: Request, _: str = Depends(_require_api_key)):
    body = await json_object(request)
    values = body.get("values") if isinstance(body.get("values"), dict) else body
    try:
        updated = await run_in_threadpool(db_editor_service.update_record, engine, _editor_categories(category), record_id,
                                          values or {})
    except db_editor_service.InvalidEditorValue as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if not updated:
        raise HTTPException(status_code=404, detail="수정 대상을 찾을 수 없습니다.")
    return {"status": "success", "message": "데이터가 저장되었습니다."}


@router.post("/rating/start")
async def api_start_batch_rating(request: Request, _: str = Depends(_require_api_key)):
    body = await json_object(request)
    report_numbers = string_list(body, "report_numbers")
    raw_score = body.get("score", 5)
    try:
        score = int(raw_score)
    except (TypeError, ValueError, OverflowError) as exc:
        raise HTTPException(status_code=400, detail="score must be between 1 and 5") from exc
    if isinstance(raw_score, bool) or (isinstance(raw_score, float) and raw_score != score):
        raise HTTPException(status_code=400, detail="score must be an integer")
    cause = body.get("cause") or ""  # 공통 사유(선택, 2026-09-25). 구앱은 보내지 않는다 → 빈 값
    if not isinstance(cause, str):
        raise HTTPException(status_code=400, detail="cause must be a string")

    if not isinstance(report_numbers, list) or not report_numbers:
        raise HTTPException(status_code=400, detail="report_numbers is required")
    if score < 1 or score > 5:
        raise HTTPException(status_code=400, detail="score must be between 1 and 5")

    normalized = [str(item).strip() for item in report_numbers if str(item).strip()]
    if not normalized:
        raise HTTPException(status_code=400, detail="유효한 신고번호가 없습니다.")

    try:
        cause_problem = rating_eligibility.cause_error(cause)
        if cause_problem:
            raise HTTPException(status_code=400, detail=cause_problem)
        final_ids = await run_in_threadpool(rating_service.start_batch_rating, engine, normalized, score, cause)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    return {
        "status": "success",
        "requested": len(normalized),
        "resolved": len(final_ids),
        "message": f"총 {len(final_ids)}건에 대해 {score}점 별점 부여를 백그라운드에서 시작합니다.",
    }


def _raise_if_community_blocked() -> None:
    """크롤 시작·큐 전: 커뮤니티 게이트(403)·초기화 필요/진행 중(409). detail 은 코드 문자열(모바일이 분기)."""
    from services import community_gate

    blocked = community_gate.crawl_block()
    if blocked:
        raise HTTPException(status_code=blocked[0], detail=blocked[1])


def _community_runtime_error(exc: RuntimeError) -> HTTPException | None:
    from services import community_gate

    code = community_gate.block_code(exc)
    if code:
        return HTTPException(status_code=409 if code in (community_gate.REBUILD_REQUIRED, community_gate.UPDATE_REQUIRED) else 403, detail=code)
    return None


@router.post("/crawl/enqueue")
async def enqueue_crawl(request: Request, _: str = Depends(_require_api_key)):
    body = await json_object(request)
    report_number = body.get("report_number")
    if not report_number:
        raise HTTPException(status_code=400, detail="report_number is required")

    await run_in_threadpool(_raise_if_community_blocked)
    try:
        result = await run_in_threadpool(crawl_control.enqueue_report, str(report_number))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        from services.community_crawl_upload import PendingUploadError
        if isinstance(exc, PendingUploadError):
            raise HTTPException(status_code=503, detail=str(exc))
        raise _community_runtime_error(exc) or HTTPException(status_code=500, detail=str(exc))

    if result["status"] == "queued":
        payload = {
            "status": "queued",
            "message": f"크롤링 완료 후 자동 실행됩니다. (대기 중: {result['queue_size']}건)",
        }
        if result.get("queued_reason"):
            payload["queued_reason"] = result["queued_reason"]
        return payload

    return {
        "status": "success",
        "message": f"신고번호 {report_number} 크롤링이 시작되었습니다.",
    }


@router.post("/crawl/notification")
async def enqueue_notification(request: Request, api_key: str = Depends(_require_api_key)):
    """Idempotent notification admission. Legacy /enqueue behavior stays unchanged."""
    body = await json_object(request)
    number, request_id = body.get("report_number"), body.get("request_id")
    if not isinstance(number, str) or not isinstance(request_id, str):
        raise HTTPException(status_code=400, detail="report_number and request_id are required")
    await run_in_threadpool(_raise_if_community_blocked)
    try:
        return await run_in_threadpool(crawl_control.enqueue_notification, number, request_id, api_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise _community_runtime_error(exc) or HTTPException(status_code=503, detail=str(exc))


@router.get("/crawl/results")
def get_crawl_results(device_id: str | None = None, _: str = Depends(_require_api_key)):
    """기기별로 아직 안 읽은 변경(결정 D-5). device_id 가 없는 구앱은 공용 'legacy' 위치를 함께 쓴다."""
    try:
        from core.storage import change_log

        changes = change_log.read_for_device(engine, device_id)
        return {"status": "success", "count": len(changes), "data": changes}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/crawl/status")
def get_crawl_status(_: str = Depends(_require_api_key)):
    from services import crawl_queue_report, crawl_run_state
    # unresolved(추가 필드, 하위호환): 대기 큐에서 처리하지 못하고 뺀 번호 — 목록 전체에 없음(not_found)·여러 신고에 걸림(ambiguous)
    return {"status": "success", "running": crawl_manager.is_crawling(),
            "pending": crawl_manager.pending_count(), "unresolved": crawl_queue_report.unresolved(),
            "last_attempt": crawl_run_state.latest()}


@router.get("/server/version")
def get_server_version(_: str = Depends(_require_api_key)):
    from core.utils.updater import _version_gt, get_current_version, get_latest_version_cached

    current = get_current_version() or "unknown"
    latest = get_latest_version_cached()
    up_to_date = (latest is None) or not _version_gt(latest, current)
    return {
        "status": "success",
        "version": current,
        "latest_version": latest,
        "up_to_date": up_to_date,
        # 5분류 지원(스키마 6). 아는 앱은 X-SR-Taxonomy: 5 를 보낸다(core/utils/taxonomy_compat.py).
        "report_taxonomy": 5,
        **selfhost_compat.metadata(),
    }


@router.get("/crawl/done/ext")
def get_crawl_done_ext(_: str = Depends(_require_api_key)):
    try:
        done = crawl_state_store.get_and_clear_crawl_done_ext()
        if done is None:
            return {"status": "success", "done": False}
        return {
            "status": "success",
            "done": True,
            "timestamp": done["timestamp"],
            "changed_count": done["changed_count"],
            "changes": done.get("changes", []),
            "duplicate_changed_count": done.get("duplicate_changed_count", 0),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/crawl/done")
def get_crawl_done(_: str = Depends(_require_api_key)):
    try:
        done = crawl_state_store.get_and_clear_crawl_done()
        if done is None:
            return {"status": "success", "done": False}
        return {
            "status": "success",
            "done": True,
            "timestamp": done["timestamp"],
            "changed_count": done["changed_count"],
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/crawl/config")
def get_crawl_config(_: str = Depends(_require_api_key)):
    settings._instance.load()
    return {
        "status": "success",
        "data": {
            # 구앱 호환으로 필드는 남긴다: 늘 api / full
            "crawl_type": settings.crawl_type,
            "crawl_mode": settings.crawl_mode,
            "max_empty_pages": settings.max_empty_pages,
        },
    }


@router.post("/crawl/start")
async def mobile_start_crawl(request: Request, _: str = Depends(_require_api_key)):
    body = await json_object(request)
    # login_mode(비회원)는 2026-09-25 제거 — 구앱이 보내도 무시하고 회원 로그인으로 진행
    # crawl_type(레거시)·max_empty_pages(최소 크롤링)는 2026-09-25 제거 — 구앱이 보내도 무시
    crawl_mode = body.get("crawl_mode", "full")
    if not isinstance(body.get('queue_list', ''), str) or not isinstance(crawl_mode, str):
        raise HTTPException(status_code=400, detail='crawl_mode and queue_list must be strings')
    queue_list = body.get('queue_list', '').strip()

    await run_in_threadpool(_raise_if_community_blocked)
    if queue_list:
        try:  # 여러 신고에 걸리는 번호는 받지 않는다(감사 R8-02 — /crawl/enqueue 와 같은 400 문장)
            await run_in_threadpool(crawl_control._refuse_ambiguous, queue_list.splitlines())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    if crawl_manager.is_crawling():
        if queue_list:
            try:
                crawl_manager.append_many_to_pending([number.strip() for number in queue_list.splitlines() if number.strip()])
            except RuntimeError as exc:  # 대기 큐 저장 실패(R3-03)
                raise HTTPException(status_code=500, detail=str(exc))
            crawl_manager.request_pending_launch()  # 실행 중이던 크롤의 완료 훅이 이미 지나갔을 수 있다(R5-02)
            payload = {
                "status": "queued",
                "message": f"크롤링 완료 후 자동 실행됩니다. (대기 중: {crawl_manager.pending_count()}건)",
            }
            try:
                from services import db_optimize as _optimize

                if _optimize.is_running():
                    _optimize.note_crawl_deferred()
                    payload["queued_reason"] = "db_optimize"
            except Exception as exc:
                from core.utils.fallback import note_fallback as _note

                _note("api_route.optimize_queued", exc)
            return payload
        raise HTTPException(status_code=409, detail="크롤링이 이미 실행 중입니다.")

    try:
        await run_in_threadpool(
            crawl_control.start_crawl,
            crawl_mode=crawl_mode,
            queue_list=queue_list,
            queue_filename="mobile_queue.txt",
            header="=== [모바일에서 시작된 크롤링] ===",
            broadcast_source="mobile_start",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except crawl_control.CrawlDeferredByOptimize as exc:
        # DB 최적화가 도는 동안은 대기열 등록으로 바꾼다(명세 3-3). 끝나면 자동 시작. 기존 필드는 그대로 두고 추가만.
        return dict(exc.queued)
    except RuntimeError as exc:
        from services.community_crawl_upload import PendingUploadError
        if isinstance(exc, PendingUploadError):
            raise HTTPException(status_code=503, detail=str(exc))
        raise _community_runtime_error(exc) or HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return {"status": "success", "message": "크롤링이 시작되었습니다."}


@router.post("/crawl/kill")
def mobile_kill_crawl(_: str = Depends(_require_api_key)):
    if not crawl_control.stop_crawl():
        raise HTTPException(status_code=409, detail="실행 중인 크롤링이 없습니다.")
    return {"status": "success", "message": "크롤링이 강제 중지되었습니다."}


@router.post("/crawl/resume")
def mobile_resume_crawl(_: str = Depends(_require_api_key)):
    """비회원(수동) 로그인 재개 신호 — 2026-09-25 비회원 모드 제거로 410. 구앱은 이 응답에 아무 안내도 띄우지 않지만,
    크롤링은 회원 로그인으로 진행되므로 재개가 필요한 상황 자체가 없다."""
    raise HTTPException(status_code=410, detail="비회원 로그인은 지원이 종료됐습니다. 크롤링은 회원 로그인으로 진행됩니다.")


@router.get("/files")
def list_files(path: str = "", _: str = Depends(_require_api_key)):
    try:
        current_path, items = file_service.list_api_entries(path)
    except PermissionError:
        raise HTTPException(status_code=403, detail="접근 불가")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="경로를 찾을 수 없습니다")
    except NotADirectoryError:
        raise HTTPException(status_code=400, detail="파일 경로는 지원하지 않습니다")
    return {"status": "success", "current_path": current_path, "data": items}


@router.get("/files/download")
def download_file(path: str = "", _: str = Depends(_require_api_key_flex)):
    try:
        target = file_service.resolve_api_file(path)
        download_path, cleanup_path = file_service.snapshot_live_log_if_needed(target)
    except PermissionError:
        raise HTTPException(status_code=403, detail="접근 불가")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다")
    except IsADirectoryError:
        raise HTTPException(status_code=400, detail="디렉토리는 다운로드할 수 없습니다")

    return TemporaryFileResponse(download_path, filename=os.path.basename(target),
                                 media_type="application/octet-stream")


@router.post("/files/download-multi")
async def download_files_archive(request: Request, _: str = Depends(_require_api_key)):
    body = await json_object(request)
    paths = string_list(body, "paths")
    if not isinstance(paths, list) or not paths:
        raise HTTPException(status_code=400, detail="paths is required")
    try:
        archive_path, filename = await run_in_threadpool(file_service.build_api_download_zip, paths)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="접근 불가") from exc
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return TemporaryFileResponse(archive_path, filename=filename, media_type="application/x-zip-compressed")


@router.post("/files/delete-multi")
async def delete_files_archive(request: Request, _: str = Depends(_require_api_key)):
    body = await json_object(request)
    paths = string_list(body, "paths")
    if not isinstance(paths, list) or not paths:
        raise HTTPException(status_code=400, detail="paths is required")
    deleted_count, errors = await run_in_threadpool(file_service.delete_api_files, paths)
    return {
        "status": "success" if not errors else "partial_success",
        "deleted_count": deleted_count,
        "errors": errors,
    }


@router.get("/app/config")
def get_app_config(_: str = Depends(_require_api_key)):
    from core.utils.updater import get_current_version
    settings._instance.load()
    return {
        "status": "success",
        "data": {
            "app_name": "나만의 안전신문고",
            "version": get_current_version(),
            "support_email": "support@example.com",
            "exclude_withdraw": settings.exclude_withdraw,
            "use_representative_records": settings.use_representative_records,
            "normalize_police": False,  # Deprecated compatibility field; registry is always active.
            "auto_export_excel": settings.config.getboolean("SETTINGS", "auto_export_excel", fallback=True),
            "auto_export_sheet": settings.config.getboolean("SETTINGS", "auto_export_sheet", fallback=False),
            # 앱이 서버 기능을 알아보는 목록. rating_cause: /rating/start 가 공통 사유(cause)를 받는다(2026-09-25).
            # community_account: /api/v1/community-auth/* (서버의 커뮤니티 계정 연결, 2026-09-25).
            # law_search_v2(2026-10-16): 법규를 개별 키로 정규화해 검색하고 검색 필터 `lawExact`(저장 키 소속 판정)를 받는다.
            # 이 신호가 없는 서버(v3.3.0 이하)에는 앱이 예전처럼 저장 원문 값을 보낸다. 기존 필드는 그대로 두고 추가만 한다.
            # raw_detail_v1(2026-10-17): `GET /api/v1/reports/{id}/raw-detail` 원본 보기. 없으면 404, 손상이면 409.
            # 이 신호가 없는 구서버에는 Client 가 원본 보기 동작을 보이지 않는다.
            "capabilities": ["rating_cause", "community_account", "report_search_v1", "law_search_v2",
                             "raw_detail_v1", IDENTITY_SEARCH_CAPABILITY],
            "report_search_version": 1,
            "law_search_version": 2,
            "raw_detail_version": 1,
            "rating_cause_max": rating_eligibility.RATING_CAUSE_MAX,
        },
    }


@router.get("/settings/db")
def download_database(_: str = Depends(_require_api_key_flex)):
    from services import db_backup

    try:
        tmp_path = db_backup.export_clean_db()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"DB 추출 실패: {exc}")

    def _cleanup(path: str):
        try:
            os.remove(path)
        except Exception:
            pass

    return FileResponse(
        tmp_path,
        filename="data.db",
        media_type="application/octet-stream",
        background=BackgroundTask(_cleanup, tmp_path),
    )


@router.post("/settings/db/upload")
async def upload_database(file: UploadFile = File(...), _: str = Depends(_require_api_key_flex)):
    """서버/모바일 DB 복원(services.db_backup.restore_uploaded_db). 임시 저장·정리는 web.db_upload 와 웹 경로가 같다."""
    from core.storage.exchange import RestoreRefused
    from services import db_backup
    from web.db_upload import refused_response, staged_db_upload

    async with staged_db_upload(file) as tmp_path:
        try:
            result = await run_in_threadpool(db_backup.restore_uploaded_db, tmp_path)
        except RestoreRefused as exc:
            return refused_response(exc)
    return {"status": "ok", "kind": result.kind, "imported": result.imported, "backup": result.backup_name}


@router.post("/settings")
async def update_settings(request: Request, _: str = Depends(_require_api_key)):
    """모바일이 바꿀 수 있는 설정만(services/settings_service.api_command, EO R-13). 모르는 키는 무시한다."""
    from services import settings_service

    body = await json_object(request)
    try:
        command = settings_service.api_command(body)
    except settings_service.SettingsInvalid as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    settings_service.apply(command)
    return {"status": "success"}
