import os
import tempfile

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from starlette.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.security import APIKeyHeader
from starlette.background import BackgroundTask

import settings.settings as settings
from core.database import database
from core.database.engine import get_engine
from services import crawl_control, crawl_state_store, data_service, db_editor_service, duplicate_group_service, file_service, geocode_service, rating_eligibility, rating_service, sunwi_service
from services.crawl_manager import crawl_manager
from services.ws_manager import ws_manager
from web.routers.filters import default_dedupe_mode, normalize_dedupe_mode

router = APIRouter(prefix="/api/v1")
engine = get_engine()

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
_api_key_query_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def _require_api_key(request: Request, api_key: str = Depends(_api_key_header)):
    if not api_key or not database.validate_api_key(engine, api_key):
        raise HTTPException(status_code=401, detail="유효하지 않은 API 키입니다.")
    device_name = database.get_api_key_name(engine, api_key)
    ip = request.headers.get("X-Forwarded-For", request.client.host if request.client else "")
    ws_manager.track_api_request(api_key, device_name, ip)
    return api_key


def _require_api_key_flex(request: Request, header_key: str = Depends(_api_key_query_header)):
    api_key = header_key or request.query_params.get("api_key", "")
    if not api_key or not database.validate_api_key(engine, api_key):
        raise HTTPException(status_code=401, detail="유효하지 않은 API 키입니다.")
    return api_key


@router.get("/summary")
def get_summary(request: Request, dedupe: str | None = None, _: str = Depends(_require_api_key)):
    try:
        dedupe_mode = normalize_dedupe_mode(dedupe)
        return {"status": "success", "data": data_service.get_dashboard_stats(engine, mode=dedupe_mode)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/reports/{category}")
def get_reports(category: str, dedupe: str | None = None, _: str = Depends(_require_api_key)):
    try:
        dedupe_mode = normalize_dedupe_mode(dedupe)
        if category == "traffic":
            records = data_service.get_traffic_records(engine, mode=dedupe_mode, exact_values=True)
        elif category == "parking":
            records = data_service.get_parking_records(engine, mode=dedupe_mode, exact_values=True)
        elif category == "other":
            records = data_service.get_other_records(engine, mode=dedupe_mode, exact_values=True)
        elif category == "duplicates":
            records = data_service.get_duplicate_records(engine, mode=dedupe_mode)
        else:
            raise HTTPException(status_code=400, detail="Invalid category")
        return {"status": "success", "category": category, "count": len(records), "data": records}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/vehicle/{vehicle_number}")
def get_vehicle_reports(vehicle_number: str, dedupe: str | None = None, _: str = Depends(_require_api_key)):
    try:
        dedupe_mode = normalize_dedupe_mode(dedupe)
        results = data_service.search_by_vehicle(engine, vehicle_number, mode=dedupe_mode)
        return {"status": "success", "vehicle_number": vehicle_number, "count": len(results), "data": results}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/address")
def get_address_reports(q: str, dedupe: str | None = None, _: str = Depends(_require_api_key)):
    try:
        dedupe_mode = normalize_dedupe_mode(dedupe)
        results = data_service.search_by_address(engine, q, mode=dedupe_mode)
        return {"status": "success", "address": q, "count": len(results), "data": results}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/stats")
def get_stats(_: str = Depends(_require_api_key), year: str = None, law: str = None, dedupe: str | None = None):
    try:
        filters = {}
        if year and year != "all":
            filters["year"] = year
        if law:
            filters["law"] = law
        dedupe_mode = normalize_dedupe_mode(dedupe)
        return {"status": "success", "data": data_service.get_agency_stats(engine, filters or None, mode=dedupe_mode)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))



@router.get("/stats/overview")
def get_stats_overview(_: str = Depends(_require_api_key), year: str = None, law: str = None, dedupe: str | None = None):
    try:
        filters = {}
        if year and year != "all":
            filters["year"] = year
        if law:
            filters["law"] = law
        dedupe_mode = normalize_dedupe_mode(dedupe)
        return {"status": "success", "data": data_service.get_stats_overview(engine, filters or None, mode=dedupe_mode)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

@router.get("/stats/map")
def get_stats_map(
    _: str = Depends(_require_api_key),
    year: str | None = None,
    category: str = "all",
):
    try:
        geocode_service.ensure_map_backfill_started(engine, batch_size=120)
        payload = data_service.get_report_map_stats(
            engine,
            year=year,
            category=category,
            mode=default_dedupe_mode(),
        )
        return {"status": "success", "data": payload}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/stats/map/missing")
def get_stats_map_missing(
    _: str = Depends(_require_api_key),
    year: str | None = None,
    category: str = "all",
):
    try:
        payload = data_service.get_report_map_missing_groups(
            engine,
            year=year,
            category=category,
            mode=default_dedupe_mode(),
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
        progress = geocode_service.get_backfill_progress(engine)
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
    body = await request.json()
    report_numbers = body.get("report_numbers", [])
    action = body.get("action", "remove")
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


@router.post("/duplicates/groups/{group_id}")
async def update_duplicate_group_api(group_id: str, request: Request, _: str = Depends(_require_api_key)):
    body = await request.json()
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


@router.post("/duplicates/groups/bulk-status")
async def bulk_update_duplicate_group_status_api(request: Request, _: str = Depends(_require_api_key)):
    body = await request.json()
    group_ids = body.get("group_ids", [])
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


@router.get("/editor/schema")
def get_editor_schema(_: str = Depends(_require_api_key)):
    return {"status": "success", "data": db_editor_service.get_editor_schema()}


@router.get("/editor/{category}/{record_id}")
def get_editor_record(category: str, record_id: str, _: str = Depends(_require_api_key)):
    record = db_editor_service.get_record(engine, category, record_id)
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
            **db_editor_service.get_edit_state(engine, category, record_id),
        },
    }


@router.post("/editor/{category}/{record_id}")
async def save_editor_record(category: str, record_id: str, request: Request, _: str = Depends(_require_api_key)):
    body = await request.json()
    values = body.get("values") if isinstance(body.get("values"), dict) else body
    updated = await run_in_threadpool(db_editor_service.update_record, engine, category, record_id, values or {})
    if not updated:
        raise HTTPException(status_code=404, detail="수정 대상을 찾을 수 없습니다.")
    return {"status": "success", "message": "데이터가 저장되었습니다."}


@router.post("/rating/start")
async def api_start_batch_rating(request: Request, _: str = Depends(_require_api_key)):
    body = await request.json()
    report_numbers = body.get("report_numbers", [])
    score = int(body.get("score", 5))
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
        return HTTPException(status_code=409 if code == community_gate.REBUILD_REQUIRED else 403, detail=code)
    return None


@router.post("/crawl/enqueue")
async def enqueue_crawl(request: Request, _: str = Depends(_require_api_key)):
    body = await request.json()
    report_number = body.get("report_number")
    if not report_number:
        raise HTTPException(status_code=400, detail="report_number is required")

    await run_in_threadpool(_raise_if_community_blocked)
    try:
        result = await run_in_threadpool(crawl_control.enqueue_report, str(report_number))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise _community_runtime_error(exc) or HTTPException(status_code=500, detail=str(exc))

    if result["status"] == "queued":
        return {
            "status": "queued",
            "message": f"크롤링 완료 후 자동 실행됩니다. (대기 중: {result['queue_size']}건)",
        }

    return {
        "status": "success",
        "message": f"신고번호 {report_number} 크롤링이 시작되었습니다.",
    }


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
    from services import crawl_queue_report
    # unresolved(추가 필드, 하위호환): 대기 큐에서 처리하지 못하고 뺀 번호 — 목록 전체에 없음(not_found)·여러 신고에 걸림(ambiguous)
    return {"status": "success", "running": crawl_manager.is_crawling(),
            "pending": crawl_manager.pending_count(), "unresolved": crawl_queue_report.unresolved()}


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
    body = await request.json()
    # login_mode(비회원)는 2026-09-25 제거 — 구앱이 보내도 무시하고 회원 로그인으로 진행
    # crawl_type(레거시)·max_empty_pages(최소 크롤링)는 2026-09-25 제거 — 구앱이 보내도 무시
    crawl_mode = body.get("crawl_mode", "full")
    queue_list = body.get("queue_list", "").strip()

    await run_in_threadpool(_raise_if_community_blocked)
    if queue_list:
        try:  # 여러 신고에 걸리는 번호는 받지 않는다(감사 R8-02 — /crawl/enqueue 와 같은 400 문장)
            await run_in_threadpool(crawl_control._refuse_ambiguous, queue_list.splitlines())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    if crawl_manager.is_crawling():
        if queue_list:
            try:
                for report_number in queue_list.splitlines():
                    report_number = report_number.strip()
                    if report_number:
                        crawl_manager.append_to_pending(report_number)
            except RuntimeError as exc:  # 대기 큐 저장 실패(R3-03)
                raise HTTPException(status_code=500, detail=str(exc))
            crawl_manager.request_pending_launch()  # 실행 중이던 크롤의 완료 훅이 이미 지나갔을 수 있다(R5-02)
            return {
                "status": "queued",
                "message": f"크롤링 완료 후 자동 실행됩니다. (대기 중: {crawl_manager.pending_count()}건)",
            }
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
    except RuntimeError as exc:
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

    def _cleanup(snapshot_path: str):
        try:
            os.remove(snapshot_path)
        except Exception:
            pass

    return FileResponse(
        download_path,
        filename=os.path.basename(target),
        media_type="application/octet-stream",
        background=BackgroundTask(_cleanup, cleanup_path) if cleanup_path else None,
    )


@router.post("/files/download-multi")
async def download_files_archive(request: Request, _: str = Depends(_require_api_key)):
    body = await request.json()
    paths = body.get("paths", [])
    if not isinstance(paths, list) or not paths:
        raise HTTPException(status_code=400, detail="paths is required")
    zip_buffer, filename = await run_in_threadpool(file_service.build_api_download_zip, paths)
    return StreamingResponse(
        zip_buffer,
        media_type="application/x-zip-compressed",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.post("/files/delete-multi")
async def delete_files_archive(request: Request, _: str = Depends(_require_api_key)):
    body = await request.json()
    paths = body.get("paths", [])
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
    settings._instance.load()
    return {
        "status": "success",
        "data": {
            "app_name": "나만의 안전신문고",
            "version": "1.0.0",
            "support_email": "support@example.com",
            "exclude_withdraw": settings.exclude_withdraw,
            "use_representative_records": settings.use_representative_records,
            "normalize_police": settings.normalize_police,
            "auto_export_excel": settings.config.getboolean("SETTINGS", "auto_export_excel", fallback=True),
            "auto_export_sheet": settings.config.getboolean("SETTINGS", "auto_export_sheet", fallback=False),
            # 앱이 서버 기능을 알아보는 목록. rating_cause: /rating/start 가 공통 사유(cause)를 받는다(2026-09-25).
            # community_account: /api/v1/community-auth/* (서버의 커뮤니티 계정 연결, 2026-09-25).
            "capabilities": ["rating_cause", "community_account"],
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
    from services import db_backup

    if not file.filename or not file.filename.lower().endswith(".db"):
        raise HTTPException(status_code=400, detail=".db 파일만 업로드 가능합니다.")

    fd, tmp_path = tempfile.mkstemp(suffix=".db", prefix="safetyreport_upload_")
    try:
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)

        from core.storage.exchange import RestoreRefused
        kind = db_backup.detect_db_kind(tmp_path)
        try:
            if kind == "server":
                backup, count = await run_in_threadpool(db_backup.restore_from_server_db, tmp_path)
            elif kind == "mobile":
                backup, count = await run_in_threadpool(db_backup.restore_from_mobile_db, tmp_path)
        except RestoreRefused as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        if kind not in ("server", "mobile"):
            raise HTTPException(
                status_code=400,
                detail="알 수 없는 DB 형식 — 서버(mysafety*) 또는 모바일(reports+sync_meta) DB만 허용됩니다.",
            )

        return {
            "status": "ok",
            "kind": kind,
            "imported": count,
            "backup": os.path.basename(backup) if backup else "",
        }
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass


@router.post("/settings")
async def update_settings(request: Request, _: str = Depends(_require_api_key)):
    body = await request.json()
    if "normalize_police" in body:
        settings._instance.update_config("SETTINGS", "normalize_police", body["normalize_police"])
    if "exclude_withdraw" in body:
        settings._instance.update_config("SETTINGS", "exclude_withdraw", body["exclude_withdraw"])
    if "use_representative_records" in body:
        settings._instance.update_config("SETTINGS", "use_representative_records", body["use_representative_records"])
    if "auto_export_excel" in body:
        settings._instance.update_config("SETTINGS", "auto_export_excel", body["auto_export_excel"])
    if "auto_export_sheet" in body:
        settings._instance.update_config("SETTINGS", "auto_export_sheet", body["auto_export_sheet"])
    settings._instance.save()
    return {"status": "success"}
