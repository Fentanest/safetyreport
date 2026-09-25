"""업데이트 뒤 한 번 훑는 유지보수 작업과 진행 상태 (2026-09-25).

- 사진 촬영 시각: 신고일 6개월 이내(첨부 URL 만료 전) 주정차 신고 중 아직 못 읽은 것의 첨부 사진 앞부분을 받아 EXIF 촬영 시각을 채운다.
  추정 과태료(2시간 초과·밤샘주차 판정)에 쓰인다. 추정 과태료·처분 분류 자체는 통계를 볼 때 계산하므로 따로 훑을 필요가 없다.
- 지도 좌표 채우기는 geocode_service 가 따로 돌린다. 여기서는 진행 상태만 함께 보여 준다.

안전신문고 서버에 부담이 가지 않게 한 건씩 간격을 두고, 크롤링 중에는 기다렸다가 이어 간다. 진행 상태는 메모리에만 둔다
(서버를 다시 켜면 남은 것부터 다시 센다 — 채운 신고는 대상에서 빠지므로 처음부터 다시 받지 않는다).
"""
from __future__ import annotations

import threading
import time
from datetime import datetime

from core.utils import logger

PHOTO_JOB = "photo_capture_time"
_REQUEST_INTERVAL_SECONDS = 0.4
_CRAWL_WAIT_SECONDS = 5.0

_lock = threading.Lock()
_thread: threading.Thread | None = None
_state: dict = {
    "key": PHOTO_JOB,
    "label": "주정차 사진 촬영 시각 읽기",
    "state": "idle",  # idle | running | paused | completed | error
    "total": 0,
    "done": 0,
    "filled": 0,
    "failed": 0,
    "current": "",
    "message": "",
    "finished_at": "",
}


def _update(**changes) -> None:
    with _lock:
        _state.update(changes)


def photo_job_state() -> dict:
    with _lock:
        return dict(_state)


def _is_crawling() -> bool:
    try:
        from services.crawl_manager import crawl_manager

        return bool(crawl_manager.is_crawling())
    except Exception:
        return False


def _run_photo_job(engine, rows, fetch, interval: float, crawling) -> None:
    from services import photo_capture_time

    filled = failed = 0
    for index, (record_id, photos, report_number) in enumerate(rows, start=1):
        while crawling():
            _update(state="paused", message="크롤링이 끝나면 이어서 합니다")
            time.sleep(_CRAWL_WAIT_SECONDS)
        _update(state="running", current=report_number or record_id, message="")
        try:
            ok = photo_capture_time.fill_one(engine, record_id, photos, fetch=fetch)
        except Exception as exc:  # 한 건의 실패가 전체를 멈추지 않게
            logger.LoggerFactory.logbot.warning(f"[maintenance] 사진 촬영 시각 {record_id} 실패: {exc}")
            ok = False
        filled += int(ok)
        failed += int(not ok)
        _update(done=index, filled=filled, failed=failed)
        if interval:
            time.sleep(interval)
    _update(state="completed", current="", finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            message=f"{filled}건 채움" + (f", {failed}건은 다음에 다시" if failed else ""))
    logger.LoggerFactory.logbot.info(f"[maintenance] 사진 촬영 시각 한 번 훑기 끝: 채움 {filled}, 실패 {failed}")


def start_photo_backfill(engine, *, fetch=None, interval: float = _REQUEST_INTERVAL_SECONDS, crawling=_is_crawling,
                         wait: bool = False) -> dict:
    """대상이 있으면 백그라운드로 시작한다(이미 돌고 있으면 그대로). 반환: 현재 상태."""
    global _thread
    from services import photo_capture_time

    with _lock:
        if _thread is not None and _thread.is_alive():
            return dict(_state)
    rows = photo_capture_time.pending_photo_rows(engine)
    if not rows:
        _update(state="idle", total=0, done=0, filled=0, failed=0, current="", message="")
        return photo_job_state()
    _update(state="running", total=len(rows), done=0, filled=0, failed=0, current="", message="", finished_at="")
    logger.LoggerFactory.logbot.info(f"[maintenance] 사진 촬영 시각 한 번 훑기 시작: {len(rows)}건")
    thread = threading.Thread(target=_run_photo_job, args=(engine, rows, fetch, interval, crawling),
                              name="maintenance-photo", daemon=True)
    with _lock:
        _thread = thread
    thread.start()
    if wait:
        thread.join()
    return photo_job_state()


def status(engine) -> dict:
    """하단 진행 표시줄용: 돌고 있거나 막 끝난 작업 목록. active 가 False 면 표시줄을 숨긴다."""
    from services import geocode_service

    jobs = []
    photo = photo_job_state()
    if photo["state"] != "idle":
        jobs.append(photo)
    try:
        geo = geocode_service.get_backfill_progress(engine)
    except Exception:
        geo = {}
    geo_state = str(geo.get("state") or "")
    if geo_state in ("running", "queued"):
        jobs.append({
            "key": "geocode",
            "label": "지도 좌표 채우기",
            "state": "running" if geo_state == "running" else "paused",
            "total": int(geo.get("total") or 0),
            "done": int(geo.get("processed") or 0),
            "current": "",
            "message": "크롤링이 끝나면 이어서 합니다" if geo_state == "queued" else "",
        })
    active = any(job["state"] in ("running", "paused") for job in jobs)
    return {"active": active, "jobs": jobs}
