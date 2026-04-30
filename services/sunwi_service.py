import copy
import os
import threading
from datetime import datetime

import settings.settings as settings
from core.utils import logger
from services import sunwi_fetcher


REFRESH_INTERVAL_SECONDS = 3 * 60 * 60
ALL_CSV_FILENAME = "sunwi_category_all_latest.csv"
TOP5_CSV_FILENAME = "sunwi_category_top5_latest.csv"

_cache_lock = threading.Lock()
_stop_event = threading.Event()
_worker_thread = None
_cache = {
    "available": False,
    "period": None,
    "period_label": None,
    "updated_at": None,
    "categories": [],
    "error": None,
    "failed_count": 0,
}


def _get_logger():
    return logger.LoggerFactory.get_logger()


def _log_info(message: str):
    log = _get_logger()
    if log:
        log.info(message)


def _log_warning(message: str):
    log = _get_logger()
    if log:
        log.warning(message)


def _log_error(message: str):
    log = _get_logger()
    if log:
        log.error(message)


def _log_adapter(message: str):
    _log_info(f"[sunwi] {message}")


def _results_dir() -> str:
    os.makedirs(settings.resultpath, exist_ok=True)
    return settings.resultpath


def get_all_csv_path() -> str:
    return os.path.join(_results_dir(), ALL_CSV_FILENAME)


def get_top5_csv_path() -> str:
    return os.path.join(_results_dir(), TOP5_CSV_FILENAME)


def refresh_data() -> dict:
    result = sunwi_fetcher.collect_statistics(logger_fn=_log_adapter)
    sunwi_fetcher.save_all_rows_csv(result["all_rows"], get_all_csv_path())
    sunwi_fetcher.save_top5_csv(result["top5_rows"], get_top5_csv_path())

    updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    payload = {
        "available": any(
            child["items"]
            for group in result["top5_by_category"]
            for child in group["children"]
        ),
        "period": result["period"],
        "period_label": result["period_label"],
        "updated_at": updated_at,
        "categories": result["top5_by_category"],
        "error": None,
        "failed_count": len(result["failed"]),
    }

    with _cache_lock:
        _cache.update(payload)

    if result["failed"]:
        _log_warning(f"[sunwi] 수집 완료. 일부 실패 지역 {len(result['failed'])}건이 남았습니다.")
    else:
        _log_info("[sunwi] 수집 완료.")

    return copy.deepcopy(payload)


def get_dashboard_payload() -> dict:
    with _cache_lock:
        payload = copy.deepcopy(_cache)

    payload["csv_download_url"] = "/sunwi/download/top5"
    return payload


def _worker_loop():
    _log_info("[sunwi] 백그라운드 수집 루프를 시작합니다. (3시간 주기)")
    while not _stop_event.is_set():
        try:
            refresh_data()
        except Exception as exc:
            with _cache_lock:
                _cache["error"] = str(exc)
            _log_error(f"[sunwi] 수집 실패: {exc}")

        if _stop_event.wait(REFRESH_INTERVAL_SECONDS):
            break
    _log_info("[sunwi] 백그라운드 수집 루프를 종료합니다.")


def start_background_refresh():
    global _worker_thread

    if _worker_thread and _worker_thread.is_alive():
        return

    _stop_event.clear()
    _worker_thread = threading.Thread(
        target=_worker_loop,
        name="sunwi-refresh",
        daemon=True,
    )
    _worker_thread.start()


def stop_background_refresh():
    _stop_event.set()
