from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
import re
import threading

import requests
from sqlalchemy import or_, select, update, func
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import OperationalError

import settings.settings as app_settings
from core.database import models
from core.utils import logger


KAKAO_ADDRESS_URL = "https://dapi.kakao.com/v2/local/search/address.json"
GEO_COLUMN_NAMES = ("주소정규화", "행정구역", "위도", "경도", "지오코딩상태")
REPORT_TABLE_PAIRS = [
    (models.detail_traffic_table, models.merge_traffic_table, "traffic"),
    (models.detail_parking_table, models.merge_parking_table, "parking"),
    (models.detail_other_table, models.merge_other_table, "other"),
]

_BACKFILL_STATE_KEY = "map_backfill_state"
_BACKFILL_LEASE_TTL_MS = 300_000
_BACKFILL_LOCK_PATH = os.path.join(tempfile.gettempdir(), "safetyreport-geocode-backfill.lock")
try:
    import fcntl  # type: ignore
except Exception:  # pragma: no cover - Windows 등에서는 존재하지 않을 수 있음
    fcntl = None

_BACKFILL_LOCK = threading.Lock()
_BACKGROUND_THREAD = None
_PROGRESS_LOCK = threading.Lock()


def _initial_progress_state() -> dict:
    return {
        "state": "idle",
        "running": False,
        "total": 0,
        "processed": 0,
        "updated": 0,
        "not_found": 0,
        "remaining_missing": 0,
        "progress_pct": 0.0,
        "error_message": "",
        "started_at": 0,
        "finished_at": 0,
        "heartbeat_at": 0,
        "lease_owner": "",
        "has_saved_coordinates": False,
    }


_PROGRESS_STATE = _initial_progress_state()


class GeocodeConfigurationError(RuntimeError):
    pass


class GeocodeProviderError(RuntimeError):
    pass


def _current_epoch_millis() -> int:
    return int(time.time() * 1000)


def _sync_progress_percent(state: dict) -> dict:
    total = max(int(state.get("total") or 0), 0)
    processed = max(int(state.get("processed") or 0), 0)
    if total > 0:
        state["progress_pct"] = round(min(processed, total) / total * 100, 1)
    elif state.get("state") == "completed":
        state["progress_pct"] = 100.0
    else:
        state["progress_pct"] = 0.0
    return state


def _normalize_progress_state(state: dict) -> dict:
    base = _initial_progress_state()
    merged = base | state
    try:
        merged["running"] = bool(merged.get("running"))
        merged["total"] = max(int(merged.get("total") or 0), 0)
        merged["processed"] = max(int(merged.get("processed") or 0), 0)
        merged["updated"] = max(int(merged.get("updated") or 0), 0)
        merged["not_found"] = max(int(merged.get("not_found") or 0), 0)
        merged["remaining_missing"] = max(int(merged.get("remaining_missing") or 0), 0)
        merged["started_at"] = max(int(merged.get("started_at") or 0), 0)
        merged["finished_at"] = max(int(merged.get("finished_at") or 0), 0)
        merged["heartbeat_at"] = max(int(merged.get("heartbeat_at") or 0), 0)
    except (TypeError, ValueError):
        merged = base
    merged["has_saved_coordinates"] = bool(merged.get("has_saved_coordinates"))
    merged["state"] = str(merged.get("state") or "idle").strip() or "idle"
    merged["error_message"] = str(merged.get("error_message") or "")
    merged["lease_owner"] = str(merged.get("lease_owner") or "")
    return _sync_progress_percent(merged)


def _read_persisted_state(engine) -> dict:
    if engine is None:
        return _initial_progress_state()
    try:
        with engine.connect() as conn:
            row = conn.execute(
                select(models.sync_meta_table.c.value).where(models.sync_meta_table.c.key == _BACKFILL_STATE_KEY)
            ).fetchone()
        if not row or not row[0]:
            return _initial_progress_state()
        loaded = json.loads(row[0])
        if not isinstance(loaded, dict):
            return _initial_progress_state()
        return _normalize_progress_state(loaded)
    except (OperationalError, json.JSONDecodeError, TypeError, ValueError):
        return _initial_progress_state()


def _persist_progress_state(engine, state: dict) -> None:
    if engine is None:
        return
    try:
        payload = json.dumps(_normalize_progress_state(state), ensure_ascii=False, separators=(",", ":"))
        stmt = sqlite_insert(models.sync_meta_table).values(
            key=_BACKFILL_STATE_KEY,
            value=payload,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["key"],
            set_={"value": payload},
        )
        with engine.begin() as conn:
            conn.execute(stmt)
    except (OperationalError, TypeError, ValueError):
        logger.LoggerFactory.logbot.warning("[geocode] 백필 상태 persist 실패")


def _lock_file_is_stale(path: str, *, now_ms: int) -> bool:
    try:
        stat = os.stat(path)
    except FileNotFoundError:
        return False
    except OSError:
        return False
    touched_ms = max(int(stat.st_mtime * 1000), int(stat.st_ctime * 1000))
    return now_ms > (touched_ms + _BACKFILL_LEASE_TTL_MS)


def _try_acquire_backfill_start_lock(timeout_ms: int = 2500):
    deadline = _current_epoch_millis() + timeout_ms
    if fcntl is None:
        while True:
            try:
                fd = os.open(_BACKFILL_LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o666)
                try:
                    payload = json.dumps({
                        "pid": os.getpid(),
                        "created_at": _current_epoch_millis(),
                    }, ensure_ascii=False).encode("utf-8")
                    os.write(fd, payload)
                except OSError:
                    pass
                return fd
            except FileExistsError:
                now_ms = _current_epoch_millis()
                if _lock_file_is_stale(_BACKFILL_LOCK_PATH, now_ms=now_ms):
                    try:
                        os.unlink(_BACKFILL_LOCK_PATH)
                        continue
                    except FileNotFoundError:
                        continue
                    except OSError:
                        pass
                if now_ms >= deadline:
                    return None
                time.sleep(0.05)
                continue
            except OSError:
                return None

    fd = os.open(_BACKFILL_LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o666)
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BlockingIOError:
            if _current_epoch_millis() >= deadline:
                os.close(fd)
                return None
            time.sleep(0.05)
            continue
        except OSError:
            os.close(fd)
            return None


def _release_backfill_start_lock(fd) -> None:
    if fd is None:
        return
    if fcntl is None:
        try:
            os.close(fd)
        finally:
            try:
                os.unlink(_BACKFILL_LOCK_PATH)
            except FileNotFoundError:
                pass
            except OSError:
                pass
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _active_lease_is_valid(state: dict, *, now_ms: int) -> bool:
    if not bool(state.get("running")) or str(state.get("state") or "") != "running":
        return False
    heartbeat_at = int(state.get("heartbeat_at") or state.get("started_at") or 0)
    return now_ms <= (heartbeat_at + _BACKFILL_LEASE_TTL_MS)


def _set_progress_state(*, engine=None, heartbeat: bool = False, **updates) -> dict:
    with _PROGRESS_LOCK:
        _PROGRESS_STATE.update(updates)
        if heartbeat:
            _PROGRESS_STATE["heartbeat_at"] = _current_epoch_millis()
        _sync_progress_percent(_PROGRESS_STATE)
        state = _normalize_progress_state(_PROGRESS_STATE)
        _PROGRESS_STATE.update(state)
        _persist_progress_state(engine, _PROGRESS_STATE)
        return dict(_PROGRESS_STATE)


def get_backfill_progress(engine=None) -> dict:
    if engine is not None:
        state = _read_persisted_state(engine)
        if state.get("running") and not _active_lease_is_valid(state, now_ms=_current_epoch_millis()):
            logger.LoggerFactory.logbot.warning("[geocode] 진행 상태 lease 만료로 간주하여 비동기 백필을 중단 처리합니다.")
            state = _set_progress_state(
                engine=engine,
                state="error",
                running=False,
                heartbeat=False,
                total=max(int(state.get("total") or 0), 0),
                processed=max(int(state.get("processed") or 0), 0),
                updated=max(int(state.get("updated") or 0), 0),
                not_found=max(int(state.get("not_found") or 0), 0),
                remaining_missing=max(int(state.get("remaining_missing") or 0), 0),
                error_message="지오코딩 백필 진행 상태가 만료되어 중단되었습니다. 새로고침 후 재시작 가능합니다.",
                started_at=max(int(state.get("started_at") or 0), 0),
                finished_at=_current_epoch_millis(),
                lease_owner="",
            )
    else:
        with _PROGRESS_LOCK:
            state = _normalize_progress_state(_PROGRESS_STATE)
    return dict(state)


def normalize_address(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return re.sub(r"\s+", " ", text)


def get_kakao_rest_api_key() -> str:
    return app_settings.config.get("MAP", "kakao_rest_api_key", fallback="").strip()


def has_kakao_rest_api_key() -> bool:
    return bool(get_kakao_rest_api_key())


def _pending_row_conditions(detail_table):
    return [
        detail_table.c["위반장소"].is_not(None),
        detail_table.c["위반장소"] != "",
        or_(detail_table.c["위도"].is_(None), detail_table.c["경도"].is_(None)),
        or_(detail_table.c["지오코딩상태"].is_(None), detail_table.c["지오코딩상태"] != "not_found"),
    ]


def _pending_address_key_expr(detail_table):
    return func.trim(
        func.coalesce(
            func.nullif(detail_table.c["주소정규화"], ""),
            detail_table.c["위반장소"],
        )
    )


def count_saved_coordinate_records(engine) -> int:
    with engine.connect() as conn:
        value = conn.execute(
            select(func.count())
            .select_from(models.geocode_cache_table)
            .where(models.geocode_cache_table.c["상태"] == "ok")
        ).scalar()
    return int(value or 0)


def count_cache_backfillable_reports(engine) -> int:
    total = 0
    with engine.connect() as conn:
        for detail_table, _, _ in REPORT_TABLE_PAIRS:
            total += conn.execute(
                select(func.count())
                .select_from(
                    detail_table.join(
                        models.geocode_cache_table,
                        models.geocode_cache_table.c["주소정규화"] == _pending_address_key_expr(detail_table),
                    )
                )
                .where(*_pending_row_conditions(detail_table))
                .where(models.geocode_cache_table.c["상태"].in_(["ok", "not_found"]))
            ).scalar() or 0
    return int(total)


def _missing_api_key_notice(engine) -> tuple[str, str, bool]:
    has_saved_coordinates = count_saved_coordinate_records(engine) > 0
    if has_saved_coordinates:
        return (
            "config_warning",
            "저장된 좌표 데이터는 계속 지도에 반영됩니다. 다만 DB에 없는 새 주소는 카카오 REST API 키가 없으면 변환할 수 없습니다.",
            True,
        )
    return (
        "config_required",
        "카카오 REST API 키를 확인해주세요. 앱 설정의 외부 연동 키 설정에서 등록할 수 있습니다.",
        False,
    )


def _to_float_or_none(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_pending_geo_payload(address: str | None, *, status: str = "pending") -> dict:
    normalized = normalize_address(address)
    resolved_status = status if normalized else ""
    return {
        "주소정규화": normalized,
        "행정구역": "",
        "위도": None,
        "경도": None,
        "지오코딩상태": resolved_status,
    }


def extract_geo_payload(source, *, fallback_address: str = "") -> dict:
    source = source or {}
    normalized = normalize_address(source.get("주소정규화") or fallback_address or source.get("위반장소"))
    lat = _to_float_or_none(source.get("위도"))
    lng = _to_float_or_none(source.get("경도"))
    status = str(source.get("지오코딩상태") or "").strip()
    if not status and lat is not None and lng is not None:
        status = "ok"
    return {
        "주소정규화": normalized,
        "행정구역": str(source.get("행정구역") or "").strip(),
        "위도": lat,
        "경도": lng,
        "지오코딩상태": status,
    }


def _cache_row_to_payload(row) -> dict:
    return {
        "주소정규화": normalize_address(row.get("주소정규화")),
        "행정구역": str(row.get("행정구역") or "").strip(),
        "위도": _to_float_or_none(row.get("위도")),
        "경도": _to_float_or_none(row.get("경도")),
        "지오코딩상태": str(row.get("상태") or "").strip(),
    }


def _extract_region_label(document: dict) -> str:
    for key in ("road_address", "address"):
        address_info = document.get(key) or {}
        parts = [
            str(address_info.get("region_1depth_name") or "").strip(),
            str(address_info.get("region_2depth_name") or "").strip(),
            str(address_info.get("region_3depth_name") or address_info.get("region_3depth_h_name") or "").strip(),
        ]
        label = " ".join(part for part in parts if part)
        if label:
            return label
    return normalize_address(document.get("address_name"))


def _persist_cache_record(conn, normalized_address: str, original_address: str, payload: dict, *, source: str, error_message: str = ""):
    cache_payload = {
        "주소정규화": normalized_address,
        "원본주소": normalize_address(original_address),
        "행정구역": payload.get("행정구역") or "",
        "위도": payload.get("위도"),
        "경도": payload.get("경도"),
        "상태": payload.get("지오코딩상태") or "",
        "source": source,
        "error_message": error_message or "",
        "updated_at": _current_epoch_millis(),
    }
    stmt = sqlite_insert(models.geocode_cache_table).values(**cache_payload)
    stmt = stmt.on_conflict_do_update(
        index_elements=["주소정규화"],
        set_={
            "원본주소": cache_payload["원본주소"],
            "행정구역": cache_payload["행정구역"],
            "위도": cache_payload["위도"],
            "경도": cache_payload["경도"],
            "상태": cache_payload["상태"],
            "source": cache_payload["source"],
            "error_message": cache_payload["error_message"],
            "updated_at": cache_payload["updated_at"],
        },
    )
    conn.execute(stmt)


def resolve_address(engine, address: str) -> dict:
    normalized = normalize_address(address)
    if not normalized:
        return build_pending_geo_payload("", status="")

    with engine.connect() as conn:
        cached_row = conn.execute(
            select(models.geocode_cache_table).where(models.geocode_cache_table.c["주소정규화"] == normalized)
        ).mappings().first()
    if cached_row and str(cached_row.get("상태") or "").strip() in {"ok", "not_found"}:
        return _cache_row_to_payload(cached_row)

    api_key = get_kakao_rest_api_key()
    if not api_key:
        raise GeocodeConfigurationError("카카오 REST API 키가 비어 있습니다.")

    try:
        response = requests.get(
            KAKAO_ADDRESS_URL,
            headers={"Authorization": f"KakaoAK {api_key}"},
            params={"query": normalized},
            timeout=10,
        )
    except requests.RequestException as exc:
        raise GeocodeProviderError(f"카카오 주소 변환 요청 실패: {exc}") from exc

    if response.status_code != 200:
        message = ""
        try:
            message = (response.json() or {}).get("message", "")
        except ValueError:
            message = response.text.strip()
        raise GeocodeProviderError(f"HTTP {response.status_code} {message}".strip())

    try:
        payload = response.json() or {}
    except ValueError as exc:
        raise GeocodeProviderError("카카오 주소 변환 응답이 JSON 형식이 아닙니다.") from exc

    documents = payload.get("documents") or []
    if not documents:
        not_found_payload = {
            "주소정규화": normalized,
            "행정구역": "",
            "위도": None,
            "경도": None,
            "지오코딩상태": "not_found",
        }
        with engine.begin() as conn:
            _persist_cache_record(conn, normalized, normalized, not_found_payload, source="kakao", error_message="NOT_FOUND")
        return not_found_payload

    document = documents[0] or {}
    address_info = document.get("address") or {}
    road_info = document.get("road_address") or {}
    x_value = document.get("x") or address_info.get("x") or road_info.get("x")
    y_value = document.get("y") or address_info.get("y") or road_info.get("y")
    lat = _to_float_or_none(y_value)
    lng = _to_float_or_none(x_value)
    if lat is None or lng is None:
        raise GeocodeProviderError("카카오 응답에 좌표 값이 없습니다.")

    success_payload = {
        "주소정규화": normalized,
        "행정구역": _extract_region_label(document),
        "위도": lat,
        "경도": lng,
        "지오코딩상태": "ok",
    }
    with engine.begin() as conn:
        _persist_cache_record(conn, normalized, normalized, success_payload, source="kakao")
    return success_payload


def prepare_geo_payload(engine, address: str, *, existing_record=None) -> dict:
    normalized = normalize_address(address)
    existing_payload = extract_geo_payload(existing_record, fallback_address=normalized)
    existing_address = normalize_address((existing_record or {}).get("주소정규화") or (existing_record or {}).get("위반장소"))
    same_address = bool(existing_address) and existing_address == normalized

    if not normalized:
        return build_pending_geo_payload("", status="")
    if same_address and existing_payload.get("지오코딩상태") in {"ok", "not_found"}:
        return existing_payload

    try:
        return resolve_address(engine, normalized)
    except GeocodeConfigurationError as exc:
        logger.LoggerFactory.logbot.info(f"[geocode] 설정 누락으로 지오코딩 보류: {exc}")
        if same_address and existing_payload.get("위도") is not None and existing_payload.get("경도") is not None:
            return existing_payload
        return build_pending_geo_payload(normalized, status="pending")
    except GeocodeProviderError as exc:
        logger.LoggerFactory.logbot.warning(f"[geocode] 지오코딩 실패({normalized}): {exc}")
        if same_address and existing_payload.get("위도") is not None and existing_payload.get("경도") is not None:
            return existing_payload
        return build_pending_geo_payload(normalized, status="error")


def _pending_rows_query(detail_table, limit: int):
    return (
        select(detail_table.c.ID, detail_table.c["위반장소"])
        .where(*_pending_row_conditions(detail_table))
        .order_by(detail_table.c.ID.desc())
        .limit(limit)
    )


def _cache_backfillable_rows_query(detail_table, limit: int):
    return (
        select(detail_table.c.ID, detail_table.c["위반장소"])
        .select_from(
            detail_table.join(
                models.geocode_cache_table,
                models.geocode_cache_table.c["주소정규화"] == _pending_address_key_expr(detail_table),
            )
        )
        .where(*_pending_row_conditions(detail_table))
        .where(models.geocode_cache_table.c["상태"].in_(["ok", "not_found"]))
        .order_by(detail_table.c.ID.desc())
        .limit(limit)
    )


def count_pending_reports(engine) -> int:
    total = 0
    with engine.connect() as conn:
        for detail_table, _, _ in REPORT_TABLE_PAIRS:
            total += conn.execute(
                select(func.count())
                .select_from(detail_table)
                .where(*_pending_row_conditions(detail_table))
            ).scalar() or 0
    return int(total)


def _apply_geo_payload(conn, detail_table, merge_table, report_id: str, payload: dict):
    values = {
        "주소정규화": payload.get("주소정규화") or "",
        "행정구역": payload.get("행정구역") or "",
        "위도": payload.get("위도"),
        "경도": payload.get("경도"),
        "지오코딩상태": payload.get("지오코딩상태") or "",
    }
    conn.execute(update(detail_table).where(detail_table.c.ID == report_id).values(**values))
    conn.execute(update(merge_table).where(merge_table.c.ID == report_id).values(**values))


def backfill_missing_report_coordinates(engine, *, limit: int = 150) -> dict:
    scanned = 0
    updated = 0
    not_found = 0
    error_message = ""
    error_state = "error"
    has_saved_coordinates = count_saved_coordinate_records(engine) > 0
    api_key_available = has_kakao_rest_api_key()

    with _BACKFILL_LOCK:
        pending_rows = []
        with engine.connect() as conn:
            for detail_table, merge_table, category in REPORT_TABLE_PAIRS:
                remaining = limit - len(pending_rows)
                if remaining <= 0:
                    break
                query = (
                    _pending_rows_query(detail_table, remaining)
                    if api_key_available
                    else _cache_backfillable_rows_query(detail_table, remaining)
                )
                rows = conn.execute(query).mappings().all()
                for row in rows:
                    pending_rows.append({
                        "ID": str(row["ID"]),
                        "위반장소": str(row.get("위반장소") or "").strip(),
                        "detail_table": detail_table,
                        "merge_table": merge_table,
                        "category": category,
                    })

        if not pending_rows:
            remaining_missing = count_pending_reports(engine)
            if not api_key_available and remaining_missing > 0 and count_cache_backfillable_reports(engine) <= 0:
                error_state, error_message, has_saved_coordinates = _missing_api_key_notice(engine)
            return {
                "scanned": 0,
                "updated": 0,
                "not_found": 0,
                "error_message": error_message,
                "error_state": error_state,
                "has_saved_coordinates": has_saved_coordinates,
                "remaining_missing": remaining_missing,
            }

        for row in pending_rows:
            scanned += 1
            try:
                payload = resolve_address(engine, row["위반장소"])
            except GeocodeConfigurationError:
                error_state, error_message, has_saved_coordinates = _missing_api_key_notice(engine)
                break
            except GeocodeProviderError as exc:
                logger.LoggerFactory.logbot.warning(f"[geocode] 백필 중 API 오류: {exc}")
                error_message = "카카오 REST API 응답을 확인해주세요. 앱 설정의 REST API 키가 유효한지 확인하세요."
                error_state = "error"
                break

            with engine.begin() as conn:
                _apply_geo_payload(conn, row["detail_table"], row["merge_table"], row["ID"], payload)

            if payload.get("지오코딩상태") == "ok":
                updated += 1
            elif payload.get("지오코딩상태") == "not_found":
                not_found += 1

        remaining_missing = count_pending_reports(engine)
        if not api_key_available and remaining_missing > 0 and count_cache_backfillable_reports(engine) <= 0:
            error_state, error_message, has_saved_coordinates = _missing_api_key_notice(engine)
        return {
            "scanned": scanned,
            "updated": updated,
            "not_found": not_found,
            "error_message": error_message,
            "error_state": error_state,
            "has_saved_coordinates": has_saved_coordinates,
            "remaining_missing": remaining_missing,
        }


def ensure_map_backfill_started(engine, *, batch_size: int = 120) -> dict:
    global _BACKGROUND_THREAD

    now_ms = _current_epoch_millis()
    pending = count_pending_reports(engine)
    has_saved_coordinates = count_saved_coordinate_records(engine) > 0
    if pending > 0 and not has_kakao_rest_api_key() and count_cache_backfillable_reports(engine) <= 0:
        state, message, has_saved_coordinates = _missing_api_key_notice(engine)
        logger.LoggerFactory.logbot.info("[geocode] 카카오 REST API 키가 없어 신규 지도 지오코딩 백필을 시작하지 않습니다.")
        return _set_progress_state(
            engine=engine,
            heartbeat=False,
            state=state,
            running=False,
            total=pending,
            processed=0,
            updated=0,
            not_found=0,
            remaining_missing=pending,
            error_message=message,
            started_at=0,
            finished_at=0,
            lease_owner="",
            has_saved_coordinates=has_saved_coordinates,
        )

    if _BACKGROUND_THREAD and _BACKGROUND_THREAD.is_alive():
        return get_backfill_progress(engine)

    lock_fd = _try_acquire_backfill_start_lock(timeout_ms=1200)
    if lock_fd is None:
        return get_backfill_progress(engine)

    try:
        current = get_backfill_progress(engine)
        if _active_lease_is_valid(current, now_ms=now_ms):
            return current

        if pending <= 0:
            logger.LoggerFactory.logbot.debug("[geocode] 지도 지오코딩 백필 대상이 없습니다.")
            return _set_progress_state(
                engine=engine,
                state="completed",
                running=False,
                total=0,
                processed=0,
                updated=0,
                not_found=0,
                remaining_missing=0,
                error_message="",
                started_at=0,
                finished_at=_current_epoch_millis(),
                heartbeat=False,
                lease_owner="",
                has_saved_coordinates=has_saved_coordinates,
            )

        lease_owner = uuid.uuid4().hex
        started_at = _current_epoch_millis()
        _set_progress_state(
            engine=engine,
            state="running",
            running=True,
            total=pending,
            processed=0,
            updated=0,
            not_found=0,
            remaining_missing=pending,
            error_message="",
            started_at=started_at,
            finished_at=0,
            heartbeat=True,
            lease_owner=lease_owner,
            has_saved_coordinates=has_saved_coordinates,
        )

        def _runner():
            logger.LoggerFactory.logbot.info(f"[geocode] 지도 좌표 백필 시작: 남은 대상 {pending}건")
            processed = 0
            total_updated = 0
            total_not_found = 0

            while True:
                lock_fd = _try_acquire_backfill_start_lock(timeout_ms=1200)
                if lock_fd is None:
                    time.sleep(0.3)
                    continue

                try:
                    state = get_backfill_progress(engine)
                    if str(state.get("lease_owner") or "") != lease_owner:
                        break

                    result = backfill_missing_report_coordinates(engine, limit=batch_size)
                    processed += int(result["scanned"] or 0)
                    total_updated += result["updated"]
                    total_not_found += result["not_found"]
                    remaining_missing = int(result["remaining_missing"] or 0)

                    _set_progress_state(
                        engine=engine,
                        state="running",
                        running=True,
                        total=pending,
                        processed=processed,
                        updated=total_updated,
                        not_found=total_not_found,
                        remaining_missing=remaining_missing,
                        error_message="",
                        started_at=started_at,
                        finished_at=0,
                        heartbeat=True,
                        lease_owner=lease_owner,
                        has_saved_coordinates=has_saved_coordinates or total_updated > 0,
                    )

                    if result["error_message"]:
                        _set_progress_state(
                            engine=engine,
                            state=result.get("error_state") or "error",
                            running=False,
                            total=pending,
                            processed=processed,
                            updated=total_updated,
                            not_found=total_not_found,
                            remaining_missing=remaining_missing,
                            error_message=result["error_message"],
                            started_at=started_at,
                            finished_at=_current_epoch_millis(),
                            heartbeat=False,
                            lease_owner="",
                            has_saved_coordinates=bool(result.get("has_saved_coordinates")) or has_saved_coordinates or total_updated > 0,
                        )
                        logger.LoggerFactory.logbot.warning(f"[geocode] 지도 백필 중단: {result['error_message']}")
                        break
                    if remaining_missing <= 0:
                        _set_progress_state(
                            engine=engine,
                            state="completed",
                            running=False,
                            total=pending,
                            processed=processed,
                            updated=total_updated,
                            not_found=total_not_found,
                            remaining_missing=0,
                            error_message="",
                            started_at=started_at,
                            finished_at=_current_epoch_millis(),
                            heartbeat=False,
                            lease_owner="",
                            has_saved_coordinates=has_saved_coordinates or total_updated > 0,
                        )
                        break
                    if result["scanned"] == 0:
                        _set_progress_state(
                            engine=engine,
                            state="completed",
                            running=False,
                            total=pending,
                            processed=processed,
                            updated=total_updated,
                            not_found=total_not_found,
                            remaining_missing=remaining_missing,
                            error_message="",
                            started_at=started_at,
                            finished_at=_current_epoch_millis(),
                            heartbeat=False,
                            lease_owner="",
                            has_saved_coordinates=has_saved_coordinates or total_updated > 0,
                        )
                        break
                finally:
                    _release_backfill_start_lock(lock_fd)

                time.sleep(0.15)

            logger.LoggerFactory.logbot.info(
                f"[geocode] 지도 좌표 백필 종료: 성공 {total_updated}건, 주소 미발견 {total_not_found}건"
            )

        _BACKGROUND_THREAD = threading.Thread(
            target=_runner,
            name="mysafety-geocode-backfill",
            daemon=True,
        )
        _BACKGROUND_THREAD.start()
        return get_backfill_progress(engine)
    finally:
        _release_backfill_start_lock(lock_fd)
