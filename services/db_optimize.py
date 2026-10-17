"""DB 최적화 작업: 상세 원본 기반 재파싱 (2026-10-17 명세 3절).

파서 버전이 오르면 기동 때 한 번, 신고 ID 순으로 훑는다. 원본 표에 행이 있고 sha 가 맞으면
네트워크 없이 저장된 원본의 `result` 로 다시 파싱하고, 없으면 상세 API 로 다시 받아
원본 표·파일에 저장(`source='backfill'`)한 뒤 파싱한다. 저장은 `reports_repo.reparse_one`
(파서가 만드는 열만 갱신, 목록 열·사용자 override 유지, 바뀐 신고만 공유 `completed_observation`)이다.

취소 수단 없음. 서버 종료 뒤에는 메타 커서부터 이어서 한다. 크롤과는 `is_running()` 으로
순서를 맞춘다(최적화가 도는 동안 크롤 시작 요청은 대기열 뒤에 붙는다).
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime

from sqlalchemy import select

from core.database import models
from core.utils import logger
from core.utils.fallback import note_fallback
from services.community_capture import PARSER_VERSION

#: DB 메타(`mysafety_sync_meta`) 키. {"target", "cursor", "done", "total", 통계...}
META_KEY = "detail_raw_optimize"
#: 대기열에 붙은 크롤 요청 때 크롤 창 로그에 남기는 한 줄(명세 3-3 고정 문구).
DEFER_LOG_LINE = "DB 최적화 중입니다. 최적화가 끝난 뒤 크롤링을 시작합니다."
#: 원본 표에 없던 신고를 다시 받아올 때 원본 표 `source` 열 값(명세 3-2).
BACKFILL_SOURCE = "backfill"
#: 공유 캡처 trigger. 바뀐 신고만 새 completed_observation 이 나간다.
REPARSE_TRIGGER = "reparse"

#: 크롤과 같은 요청 간격(`crawldetail_api.crawl_details` 의 건 사이 sleep).
REQUEST_INTERVAL_SECONDS = 0.3
#: 일시 오류(retryable) 재시도 횟수·간격. 크롤러 안의 `request_with_retry`(설정 재시도 횟수)가
#: 1차 전송 재시도를 맡고, 여기서는 envelope 단위 실패를 이 횟수만큼 다시 받는다.
FETCH_RETRY_ATTEMPTS = 3
#: 로그인 필요·크롤 실행 중·초기화 대기 때 다음 확인까지 쉬는 시간(초).
WAIT_SECONDS = 5.0
LOGIN_RETRY_SECONDS = 60.0

LOG_KEEP = 50

_lock = threading.RLock()
_thread: threading.Thread | None = None
_stop = threading.Event()
_state: dict = {
    "key": "db_optimize",
    "label": "DB 최적화 중입니다…",
    "state": "idle",  # idle | running | paused | completed | error
    "total": 0,
    "done": 0,
    "stage": "",  # running | waiting_crawl | waiting_rebuild | login_required | done | error + free text
    "message": "",
    "log": [],
    "finished_at": "",
}


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _update(**changes) -> None:
    with _lock:
        _state.update(changes)


def append_log(message: str) -> None:
    """단계 로그에 시각+문구 한 줄을 남긴다(최근 LOG_KEEP 줄만). 스키마 업그레이드 단계도 여기로 기록한다."""
    with _lock:
        lines = list(_state.get("log") or [])
        lines.append(f"{_now_text()} {message}")
        _state["log"] = lines[-LOG_KEEP:]


def record_migration_event(message: str) -> None:
    """`database.migrate_schema` 가 부르는 기록 지점. 최적화 작업 전에도 로그가 남는다."""
    append_log(message)


def job_for_status() -> dict | None:
    """`maintenance_service.status()` 가 그대로 싣는 작업 dict. 할 일 없으면 None."""
    with _lock:
        state = dict(_state)
        state["log"] = list(state.get("log") or [])
    if state["state"] == "idle" and not state["log"]:
        return None
    return state


def is_running() -> bool:
    """최적화 작업이 돌고 있으면 True — 크롤 시작 요청은 대기열 뒤에 붙는다."""
    with _lock:
        return _state.get("state") in ("running", "paused")


# ── 메타 ─────────────────────────────────────────────────────────────────────

def read_meta(engine) -> dict | None:
    try:
        with engine.connect() as conn:
            value = conn.execute(select(models.sync_meta_table.c.value).where(
                models.sync_meta_table.c.key == META_KEY)).scalar()
    except Exception as exc:
        note_fallback("db_optimize.read_meta", exc)
        return None
    if not value:
        return None
    try:
        data = json.loads(value)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def write_meta(engine, payload: dict) -> None:
    table = models.sync_meta_table
    with engine.begin() as conn:
        conn.execute(table.delete().where(table.c.key == META_KEY))
        conn.execute(table.insert().values(key=META_KEY, value=json.dumps(payload, ensure_ascii=False)))


def needs_run(engine) -> bool:
    """`target != 현재 파서 버전` 이거나 `done` 이 아니면 시작/이어서 한다."""
    meta = read_meta(engine)
    if meta is None:
        return True
    if meta.get("target") != PARSER_VERSION:
        return True
    return not meta.get("done")


# ── 크롤 창 로그 ─────────────────────────────────────────────────────────────

def note_crawl_deferred() -> None:
    """최적화 때문에 크롤 시작을 뒤로 미뤘다는 한 줄을 크롤 창 로그에 남긴다."""
    from services.crawl_log_service import get_current_crawl_log_path

    try:
        path = get_current_crawl_log_path()
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(DEFER_LOG_LINE + "\n")
    except Exception as exc:
        note_fallback("db_optimize.defer_log", exc)


# ── 대기 사유 확인(주입 가능) ────────────────────────────────────────────────

def _is_crawling() -> bool:
    try:
        from services.crawl_manager import crawl_manager

        return bool(crawl_manager.is_crawling())
    except Exception as exc:
        note_fallback("db_optimize.is_crawling", exc)
        return False


def _rebuild_pending() -> bool:
    """초기화(rebuild)가 필요하거나 돌고 있으면 True — 최적화는 초기화가 끝난 뒤 돈다."""
    try:
        from services import community_rebuild as rebuild

        if rebuild.blocking_state() is not None:
            return True
        try:
            return bool(rebuild.required())
        except Exception as exc:
            note_fallback("db_optimize.rebuild_required", exc)
            return False
    except Exception as exc:
        note_fallback("db_optimize.rebuild_pending", exc)
        return False


def wait_for_turn(*, is_crawling=None, rebuild_pending=None,
                  wait_seconds: float = WAIT_SECONDS, stop=None) -> bool:
    """크롤이 끝날 때까지·초기화가 끝날 때까지 기다린다. 중단되면 False."""
    crawling = is_crawling or _is_crawling
    pending = rebuild_pending or _rebuild_pending
    stop_event = stop or _stop
    first = True
    while True:
        if stop_event.is_set():
            return False
        if pending():
            _update(state="paused", stage="waiting_rebuild", message="초기화가 끝나면 이어서 합니다")
            if first:
                append_log("초기화가 끝나면 시작합니다")
                first = False
        elif crawling():
            _update(state="paused", stage="waiting_crawl", message="크롤링이 끝나면 이어서 합니다")
            if first:
                append_log("크롤링이 끝나면 시작합니다")
                first = False
        else:
            return True
        stop_event.wait(wait_seconds)


# ── 신고 처리 ────────────────────────────────────────────────────────────────

def _no_satisfaction(*args):
    return None, None


def _parse_result(report_id: str, result: dict):
    """저장 원본·재조회 응답의 `result` 를 파싱한다. 실패하면 로그를 남기고 None(그 신고는 건너뜀)."""
    from services import parser as doc_parser

    try:
        return doc_parser.parse_json_details(result)
    except Exception as exc:
        note_fallback("db_optimize.parse", exc)
        append_log(f"{report_id} 건너뜀(parse_failed: {type(exc).__name__})")
        logger.LoggerFactory.logbot.warning(f"[db_optimize] {report_id} 파싱 실패: {type(exc).__name__}")
        return None


def _build_rec(report_id: str, details: dict):
    """파싱 결과 → 저장용 `CrawledDetail`. 만족도 조회(네트워크)는 하지 않는다."""
    from core.crawler import detail_pipeline
    from core.storage import reports_repo

    tup = detail_pipeline.build_detail_result(
        report_id, details, progress_status=details.get("progress_status", ""),
        satisfaction_client=None, satisfaction_fetcher=_no_satisfaction,
        raw_envelope=None, raw_source=BACKFILL_SOURCE)
    return reports_repo.CrawledDetail.from_legacy_tuple(tup)


def _stored_result(engine, report_id: str):
    """원본 표 행 검증 → (sanitized envelope, 'ok' | 'missing' | 'corrupt')."""
    from core.utils import raw_detail as _raw

    with engine.connect() as conn:
        row = conn.execute(select(models.report_detail_raw_table).where(
            models.report_detail_raw_table.c.ID == report_id)).mappings().first()
    if row is None:
        return None, "missing"
    try:
        payload = _raw.verify(row["raw_zlib"], row["raw_sha256"])
    except ValueError as exc:
        note_fallback("db_optimize.raw_corrupt", exc)
        logger.LoggerFactory.logbot.warning(f"[db_optimize] {report_id} 원본 손상 — 다시 받습니다")
        return None, "corrupt"
    try:
        envelope = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        note_fallback("db_optimize.raw_decode", exc)
        return None, "corrupt"
    if not isinstance(envelope, dict):
        return None, "corrupt"
    return envelope, "ok"


def _classify_envelope_error(note: str) -> str:
    from core.crawler import crawldetail_api

    return crawldetail_api.classify_fetch_error(note)


def _fetch_envelope(session, report_id: str, *, fetch_fn=None, attempts: int = FETCH_RETRY_ATTEMPTS,
                    interval: float = REQUEST_INTERVAL_SECONDS, sleep=None):
    """상세 API 로 envelope 을 다시 받는다. (envelope dict | None, outcome, note)."""
    from core.crawler import crawldetail_api

    do_sleep = sleep or time.sleep
    fetch = fetch_fn or (lambda link: crawldetail_api._fetch_detail(session, link)[0])
    last_note = ""
    for _ in range(max(1, attempts)):
        try:
            data = fetch(report_id)
        except Exception as exc:
            last_note = f"network: {exc}"
            outcome = "retryable"
        else:
            if isinstance(data, dict) and "error" not in data and "result" in data:
                return data, "ok", ""
            last_note = str(data.get("error", "No result key")) if isinstance(data, dict) else "bad envelope"
            outcome = _classify_envelope_error(last_note)
        if outcome == "retryable":
            if interval:
                do_sleep(interval)
            continue
        return None, outcome, last_note
    return None, "retryable", last_note


def process_all(engine, *, session_factory=None, fetch_fn=None,
                is_crawling=None, rebuild_pending=None,
                interval: float = REQUEST_INTERVAL_SECONDS,
                wait_seconds: float = WAIT_SECONDS,
                login_retry_seconds: float = LOGIN_RETRY_SECONDS,
                sleep=None, stop=None) -> dict:
    """신고 ID 순으로 전부 처리한다(동기 — 시험·백그라운드 스레드가 함께 쓴다)."""
    from core.utils import raw_detail as _raw
    from services import parser as doc_parser

    do_sleep = sleep or time.sleep
    stop_event = stop or _stop
    make_session = session_factory
    if make_session is None:
        from core.crawler import direct_login

        def make_session():
            return direct_login.make_authorized_session()[0]

    with engine.connect() as conn:
        ids = conn.execute(select(models.report_table.c.ID).order_by(models.report_table.c.ID)).scalars().all()
    ids = [str(i) for i in ids]
    total = len(ids)
    meta = read_meta(engine) or {}
    cursor = meta.get("cursor") or ""
    # stats 는 중단·재개에 걸쳐 쌓이는 작업 전체 통계(메타에 저장), run 은 이번 실행분(반환값)이다.
    stats = dict(meta.get("stats") or {})
    stats.setdefault("reused", 0)
    stats.setdefault("refetched", 0)
    stats.setdefault("changed", 0)
    stats.setdefault("events", 0)
    stats.setdefault("skipped", {})
    run = {"reused": 0, "refetched": 0, "changed": 0, "events": 0, "skipped": {}}

    def _tally(*, reused=False, refetched=False, changed=False, event=False, skipped=None):
        for target in (stats, run):
            if reused:
                target["reused"] = target.get("reused", 0) + 1
            if refetched:
                target["refetched"] = target.get("refetched", 0) + 1
            if changed:
                target["changed"] = target.get("changed", 0) + 1
            if event:
                target["events"] = target.get("events", 0) + 1
            if skipped:
                missing = dict(target.get("skipped") or {})
                missing[skipped] = missing.get(skipped, 0) + 1
                target["skipped"] = missing
    start_index = ids.index(cursor) + 1 if cursor in ids else 0
    done_count = start_index
    _update(total=total, done=done_count, state="running", stage="running", message="")

    session = None
    session_failed = False

    def _default_fetch(link):
        # 크롤 루프와 같은 세션 갱신(`crawl_details` 의 nonlocal session).
        nonlocal session
        from core.crawler import crawldetail_api

        data, session = crawldetail_api._fetch_detail(session, link)
        return data

    def _ensure_session():
        nonlocal session, session_failed
        if session is not None:
            return True
        try:
            session = make_session()
            if session_failed:
                append_log("로그인 확인 — 이어서 합니다")
                session_failed = False
            return True
        except Exception as exc:
            session_failed = True
            _update(state="paused", stage="login_required", message="로그인 뒤 이어서 합니다")
            append_log(f"로그인 필요 — 로그인 뒤 이어서 합니다({type(exc).__name__})")
            logger.LoggerFactory.logbot.warning(f"[db_optimize] 세션을 만들지 못해 멈춤: {type(exc).__name__}")
            return False

    def _wait_login():
        while not stop_event.is_set():
            stop_event.wait(login_retry_seconds)
            if stop_event.is_set():
                return False
            if _ensure_session():
                _update(state="running", stage="running", message="")
                return True
        return False

    index = start_index
    while index < len(ids):
        if stop_event.is_set():
            break
        if not wait_for_turn(is_crawling=is_crawling, rebuild_pending=rebuild_pending,
                             wait_seconds=wait_seconds, stop=stop_event):
            break
        if _state.get("stage") in ("waiting_crawl", "waiting_rebuild"):
            _update(state="running", stage="running", message="")
        report_id = ids[index]

        envelope, raw_state = _stored_result(engine, report_id)
        fetch_note = ""
        if raw_state == "ok":
            result = envelope.get("result") if isinstance(envelope, dict) else None
            if not isinstance(result, dict):
                _tally(skipped="result_not_object")
                append_log(f"{report_id} 건너뜀(result 없음)")
            else:
                details = _parse_result(report_id, result)
                if details is None:
                    _tally(skipped="parse_failed")
                else:
                    rec = _build_rec(report_id, details)
                    _save_reparsed(engine, rec, report_id, reused=True, tally=_tally)
                    # 원본 파일이 없으면 만든다
                    _ensure_raw_file(report_id, envelope)
        else:
            if not _ensure_session():
                if not _wait_login():
                    break
                continue  # 같은 신고부터 다시
            data, outcome, note = _fetch_envelope(
                session, report_id, fetch_fn=fetch_fn or _default_fetch,
                interval=interval, sleep=do_sleep)
            if interval and outcome == "ok":
                do_sleep(interval)
            if outcome == "auth":
                session = None
                fetch_note = note
                if not _ensure_session():
                    if not _wait_login():
                        break
                continue  # 같은 신고부터 다시
            if outcome != "ok":
                reason = "not_found" if outcome == "permanent" else "fetch_failed"
                _tally(skipped=reason)
                detail = note or fetch_note
                append_log(f"{report_id} 건너뜀({reason}{': ' + detail if detail else ''})")
            else:
                canonical = _raw.canonical_bytes(data)
                details = _parse_result(report_id, data["result"])
                if details is None:
                    _tally(skipped="parse_failed")
                else:
                    rec = _build_rec(report_id, details)
                    rec.raw_canonical = canonical
                    rec.raw_sha256 = _raw.sha256_hex(canonical)
                    rec.raw_size = len(canonical)
                    rec.raw_source = BACKFILL_SOURCE
                    _save_reparsed(engine, rec, report_id, reused=False, tally=_tally)
                    _tally(refetched=True)

        index += 1
        done_count = index
        _update(done=done_count)
        write_meta(engine, {"target": PARSER_VERSION, "cursor": ids[index - 1], "done": False,
                            "total": total, "stats": stats})

    finished = index >= len(ids)
    if finished and not stop_event.is_set():
        write_meta(engine, {"target": PARSER_VERSION, "cursor": ids[-1] if ids else "",
                            "done": True, "total": total, "stats": stats})
        changed = stats.get("changed", 0)
        events = stats.get("events", 0)
        skipped_total = sum(stats.get("skipped", {}).values())
        append_log(f"완료(재파싱 변경 {changed}건, 공유 재전송 {events}건"
                   + (f", 건너뜀 {skipped_total}건" if skipped_total else "") + ")")
        _update(state="completed", stage="done", done=total,
                finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                message=f"재파싱 변경 {changed}건" + (f", 공유 재전송 {events}건" if events else ""))
        logger.LoggerFactory.logbot.info(
            f"[db_optimize] 완료: 재파싱 변경 {changed}건, 공유 재전송 {events}건, 건너뜀 {skipped_total}건")
        # 중복군 재생성(바뀐 게 있을 때만) — save_crawled 와 같은 규칙
        if changed:
            try:
                from core.database import database

                database._refresh_duplicate_groups(engine)
            except Exception as exc:
                note_fallback("db_optimize.refresh_duplicates", exc)
        _after_finish(engine)
    return {"total": total, "done": done_count, "finished": finished, "stats": run}


def _save_reparsed(engine, rec, report_id: str, *, reused: bool, tally) -> None:
    """`reparse_one` 한 건 + 통계 집계. 저장 실패·캡처 실패는 로그만(작업은 계속)."""
    from core.storage import reports_repo
    from services import community_capture as _cap

    before_revision = _latest_revision(report_id)
    try:
        change = reports_repo.reparse_one(engine, rec, trigger=REPARSE_TRIGGER)
    except reports_repo._ReparseSkipped as exc:
        reason = str(exc) or "skipped"
        tally(skipped=reason)
        append_log(f"{report_id} 건너뜀({reason})")
        return
    except _cap.CaptureStoreUnavailable:
        raise
    except Exception as exc:
        tally(skipped="save_failed")
        append_log(f"{report_id} 저장 실패({type(exc).__name__})")
        logger.LoggerFactory.logbot.warning(f"[db_optimize] {report_id} 저장 실패: {type(exc).__name__}")
        return
    if reused:
        tally(reused=True)
    if change is not None:
        tally(changed=True)
    # 바뀐 신고 중 실제로 새 journal 행이 생긴 것만 공유 재전송으로 센다(바뀌지 않으면 이벤트 없음).
    after_revision = _latest_revision(report_id)
    if after_revision is not None and after_revision != before_revision:
        tally(event=True)


def _latest_revision(report_id: str):
    """그 신고의 최신 공유 journal revision(없으면 None). trigger 는 reparse 로만 센다."""
    try:
        from services.community_store import CommunityStore

        store = CommunityStore.open()
        row = store.connect().execute(
            "SELECT source_revision, capture_trigger FROM source_journal"
            " WHERE local_dataset_id=? AND source_report_id=?"
            " ORDER BY source_revision DESC LIMIT 1",
            (store.local_dataset_id(), report_id)).fetchone()
    except Exception as exc:
        note_fallback("db_optimize.latest_revision", exc)
        return None
    if row is None:
        return None
    trigger = row["capture_trigger"] or ""
    if not trigger.startswith(REPARSE_TRIGGER):
        return None
    return row["source_revision"]


def _ensure_raw_file(report_id: str, envelope: dict) -> None:
    """원본 파일이 없으면 만든다(있으면 두지 않는다). 실패는 진단만."""
    import os

    import settings.settings as settings
    from core.utils import raw_detail as _raw

    path = _raw.raw_file_path(report_id, os.path.join(settings.datapath, "raw_details"))
    if path is None:
        return
    if os.path.exists(path):
        return
    try:
        _raw.write_raw_file(report_id, envelope)
    except Exception as exc:  # write_raw_file 은 예외를 내지 않지만 방어
        note_fallback("db_optimize.raw_file", exc)


# ── 끝나면: 대기열 이어서 ────────────────────────────────────────────────────

#: 미뤄 둔 전체 크롤 저장 키(`mysafety_sync_meta`). [{crawl_mode, queue_filename, header, ...}]
DEFERRED_META_KEY = "detail_raw_optimize_deferred_full"


def defer_full_crawl(engine, entry: dict) -> int:
    """번호 없는 수동 크롤(전체)을 최적화가 끝난 뒤로 미룬다. 반환: 쌓인 개수."""
    try:
        with engine.connect() as conn:
            value = conn.execute(select(models.sync_meta_table.c.value).where(
                models.sync_meta_table.c.key == DEFERRED_META_KEY)).scalar()
    except Exception as exc:
        note_fallback("db_optimize.defer_read", exc)
        value = None
    try:
        items = json.loads(value) if value else []
    except ValueError:
        items = []
    if not isinstance(items, list):
        items = []
    if not items or items[-1] != entry:
        items.append(entry)
    items = items[-10:]
    table = models.sync_meta_table
    try:
        with engine.begin() as conn:
            conn.execute(table.delete().where(table.c.key == DEFERRED_META_KEY))
            conn.execute(table.insert().values(key=DEFERRED_META_KEY,
                                               value=json.dumps(items, ensure_ascii=False)))
    except Exception as exc:
        note_fallback("db_optimize.defer_write", exc)
    return len(items)


def take_deferred_fulls(engine) -> list:
    """미뤄 둔 전체 크롤을 꺼내고 비운다."""
    table = models.sync_meta_table
    try:
        with engine.begin() as conn:
            value = conn.execute(select(table.c.value).where(table.c.key == DEFERRED_META_KEY)).scalar()
            conn.execute(table.delete().where(table.c.key == DEFERRED_META_KEY))
    except Exception as exc:
        note_fallback("db_optimize.defer_take", exc)
        return []
    try:
        items = json.loads(value) if value else []
    except ValueError:
        return []
    return items if isinstance(items, list) else []


def _after_finish(engine) -> None:
    """최적화가 끝나면 대기열 뒤에 붙은 크롤을 자동 시작한다(기존 pending/retry 장치)."""
    try:
        from services.crawl_manager import crawl_manager

        crawl_manager.request_pending_launch()
    except Exception as exc:
        note_fallback("db_optimize.after_finish", exc)
    for entry in take_deferred_fulls(engine):
        try:
            from services import crawl_control

            crawl_control.start_crawl(
                crawl_mode=entry.get("crawl_mode", "full"),
                queue_list="",
                queue_filename=entry.get("queue_filename", "queue.txt"),
                header=entry.get("header", "=== 크롤링 작업 시작 ==="),
                broadcast_source=entry.get("broadcast_source", "web_start"),
            )
        except Exception as exc:
            note_fallback("db_optimize.deferred_full", exc)
            logger.LoggerFactory.logbot.warning(f"[db_optimize] 미뤄 둔 크롤 시작 실패: {type(exc).__name__}")


# ── 시작/종료 ────────────────────────────────────────────────────────────────

def maybe_start(engine, *, background: bool = True, **run_kwargs) -> threading.Thread | None:
    """스키마 업그레이드 뒤 기동 때 한 번. 할 일 없으면 None(아무 일도 하지 않는다)."""
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return _thread
        _thread = None
        if not needs_run(engine):
            return None
        _stop.clear()
        meta = read_meta(engine) or {}
        with engine.connect() as conn:
            try:
                total = conn.execute(select(models.report_table.c.ID)).fetchall()
            except Exception:
                total = []
        _update(state="running", stage="starting", total=len(total), done=0, message="",
                finished_at="")
        append_log(f"DB 최적화 시작(대상 {len(total)}건, 파서 {PARSER_VERSION})"
                   + (f", 커서 {meta.get('cursor')}부터 이어서" if meta.get("cursor") else ""))
        logger.LoggerFactory.logbot.info(
            f"[db_optimize] 시작: 대상 {len(total)}건, 파서 {PARSER_VERSION}")

        def _run():
            try:
                process_all(engine, **run_kwargs)
            except Exception as exc:
                _update(state="error", stage="error", message=type(exc).__name__)
                append_log(f"오류로 멈춤({type(exc).__name__})")
                logger.LoggerFactory.logbot.error(f"[db_optimize] 오류로 멈춤: {exc}")
            finally:
                with _lock:
                    _thread = None

        thread = threading.Thread(target=_run, name="db-optimize", daemon=True)
        _thread = thread
        if background:
            thread.start()
        else:
            thread.run()
        return thread


def stop(timeout: float = 5.0) -> bool:
    """서버 종료 때: 다음 신고 경계에서 멈추고 스레드를 기다린다."""
    _stop.set()
    with _lock:
        thread = _thread
    if thread is None:
        return True
    if thread is threading.current_thread():
        return False
    thread.join(max(0, timeout))
    return not thread.is_alive()
