"""DB 최적화 작업: 상세 원본 기반 재파싱 (2026-10-17 명세 3절).

파서 버전이 오르면 기동 때 한 번, 신고 ID 순으로 훑는다. 원본 표에 행이 있고 sha 가 맞으면
네트워크 없이 저장된 원본의 `result` 로 다시 파싱하고, 없으면 상세 API 로 다시 받아
원본 표·파일에 저장(`source='backfill'`)한 뒤 파싱한다. 저장은 `reports_repo.reparse_one`
(파서가 만드는 열만 갱신, 목록 열·사용자 override 유지, 바뀐 신고만 공유 `completed_observation`)이다.

취소 수단 없음. 서버 종료 뒤에는 메타 커서부터 이어서 한다. 크롤과는 `is_running()` 으로
순서를 맞춘다(최적화가 도는 동안 크롤 시작 요청은 대기열 뒤에 붙는다).

잠금 순서 규칙(교착 금지):
- `_life_lock`(수명: 스레드·토큰 스택·종료 요청·일시정지 요청) 안에서는 상태 판단
  (`needs_run` 등 메타 읽기)·`_state` 갱신·스레드 생성만 한다. 보류 전체 크롤 시작
  (`start_crawl` 등)·크롤 상태 조회(`crawl_manager.is_crawling` 등)처럼 다른 모듈의
  잠금을 잡는 호출은 절대 하지 않는다. 보류 크롤 시작이 필요하면 수명 잠금을 놓은
  뒤 별도로 실행한다.
- 다른 모듈의 잠금(크롤 시작·크롤 상태 등)을 쥔 채 수명 잠금을 잡는 경로도 없다.
  상태 조회(`is_running` 등)는 수명 잠금이 아니라 `_lock`(상태 잠금)만 잡는다.
- 허용 방향은 `_life_lock` → `_lock` 한 방향뿐이다(역방향 중첩 금지).
- 보류 배출 직렬화: `_drain_lock` 안에서 수명 잠금을 잠깐 잡는 것은 허용하고,
  수명 잠금 안에서 `_drain_lock` 을 잡는 것은 금지한다. 배출은 `_drain_lock` 을
  논블로킹으로 잡아 한 번에 하나만 돈다(이미 진행 중이면 기다리지 않고 건너뜀 —
  진행 중인 배출이 끝에서 다시 확인한다). 보류 목록은 그 잠금 안에서 다시 읽고,
  크롤 시작 직전에 복원 일시정지·종료 요청을 수명 잠금으로 잠깐 다시 확인한다.
  `pause_for_restore` 는 표시를 켠 뒤 `_drain_lock` 을 한 번 잡았다 놓아 진행 중인
  배출이 끝났음을 보장한다(수명 잠금을 쥔 채 잡지 않고, 표시 설정 뒤 순차로 잡는다).
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
#: 일시 실패(네트워크·타임아웃·저장 실패)를 영구 건너뜀으로 바꾸기 전 시도 횟수(메타 attempts에 신고별 누적).
MAX_RETRY_ATTEMPTS = 3
#: 3번 일시 실패 뒤 건너뜀 사유(통계 skipped 키).
RETRY_EXCEEDED_REASON = "retry_exceeded"
#: 로그인 필요·크롤 실행 중·초기화 대기 때 다음 확인까지 쉬는 시간(초).
WAIT_SECONDS = 5.0
LOGIN_RETRY_SECONDS = 60.0

LOG_KEEP = 50

_lock = threading.RLock()
#: `_state`(화면 표시용 상태) 전용 잠금(`_lock` 과 같은 객체). `is_running`,
#: `job_for_status`, `_update`, `append_log` 이 쓴다. 수명 잠금을 쥔 채 다른 모듈의
#: 잠금을 잡지 않는다는 규칙에 따라 상태 조회는 이 잠금만 잡는다.
#: 작업자 수명 상태 기계용 단일 잠금. `_thread`, 일시정지
#: 토큰 스택, `_shutdown`(서버 종료 요청), `_pause_requested` 를 지킨다. 이 값들은
#: 잠금 안에서만 읽고 바꾼다(작업자 루프의 멈춤 확인 읽기는 예외 — `_stop` 이벤트로 본다).
#: 수명 잠금 안에서는 상태 판단·스레드 생성만 하고, 보류 크롤 시작·크롤 상태 조회처럼
#: 다른 모듈 잠금을 잡는 호출을 하지 않는다(모듈 상단 잠금 순서 규칙).
_life_lock = threading.RLock()
#: 보류 크롤 배출 직렬화 잠금(single-flight). `_drain_deferred_if_done`·`_after_finish`·
#: `_after_error`·`maybe_start_deferred_from_crawl` 이 논블로킹으로 잡아 한 번에
#: 하나만 돈다. 이미 진행 중이면 기다리지 않고 건너뛰고, 진행 중인 배출이 끝에서
#: 다시 확인한다. 이 잠금 안에서는 수명 잠금을 잠깐 잡을 수 있고(허용 방향),
#: 수명 잠금 안에서는 이 잠금을 잡지 않는다(역방향 금지 — 모듈 상단 규칙).
_drain_lock = threading.RLock()
_thread: threading.Thread | None = None
_stop = threading.Event()
#: 서버 종료 요청. `stop()` 만 켜고, 새 작업자 기동(`_spawn_locked` 실제 생성)과
#: 시험 리셋에서만 끈다 — 시간 초과·resume·maybe_start 판단이 지우지 않는다.
_shutdown = False
#: 복원 일시정지 요청. 토큰 스택이 비지 않았으면 True.
#: `_restore_pause` 와 같은 값을 유지한다(읽기 경로 호환 별칭).
_pause_requested = False
#: 복원 진행 표시. 켜져 있는 동안 `maybe_start` 는 시작하지 않는다.
#: DB 교체 경로(`exchange.restore` 등)가 장벽을 잡기 전에 `pause_for_restore` 로 켜고,
#: 복원 종료(성공·실패 공통)에서 `resume_after_restore` 로 끈다.
#: 중첩 호출을 견디도록 토큰 스택으로 소유자를 가린다 — 획득한 호출자만 해제한다.
_restore_pause = False
_restore_tokens: list = []
#: `resume_after_restore` 구 호출(인자 없음, 시험 정리 등)용 표시. 새 코드는 토큰을 넘긴다.
_NO_TOKEN: object = object()
#: 마지막 기동의 엔진·실행 인자 — `resume_after_restore(token)`·시간 초과 처리·
#: 작업자 종료가 잠금 안에서 `_spawn_locked()` 로 재판단할 때 쓴다.
_current_engine = None
_current_run_kwargs: dict = {}
#: 복원 시작 때 실행 중 최적화가 현재 신고 1건을 끝내고 멈추길 기다리는 시간(초).
#: 네트워크 재조회 1건이 끝나는 정도를 본다 — 넘으면 복원을 거절한다.
RESTORE_PAUSE_TIMEOUT = 60.0
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


# ── 복원 경합 감지 ─────────────────────────────────────────────────────────────

def _barrier_generation() -> int:
    try:
        from core.database import write_barrier

        return write_barrier.generation()
    except Exception as exc:
        note_fallback("db_optimize.barrier_generation", exc)
        return 0


def _dataset_id() -> str:
    try:
        from services.community_store import CommunityStore

        return CommunityStore.open().local_dataset_id()
    except Exception as exc:
        note_fallback("db_optimize.dataset_id", exc)
        return ""


def _restore_hold() -> bool:
    """복원 장벽이 잡혀 있으면 True — 현재 신고를 끝내고 멈춘다."""
    try:
        from services.crawl_manager import crawl_manager

        if crawl_manager.restore_in_progress():
            return True
    except Exception as exc:
        note_fallback("db_optimize.restore_hold", exc)
    try:
        from core.database import write_barrier

        return bool(write_barrier.in_exclusive())
    except Exception as exc:
        note_fallback("db_optimize.restore_hold_barrier", exc)
        return False


def _capture_identity() -> tuple:
    return (_dataset_id(), _barrier_generation())


def _identity_changed(start_identity: tuple) -> bool:
    try:
        return _capture_identity() != start_identity
    except Exception as exc:
        note_fallback("db_optimize.identity_changed", exc)
        return False


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
    # target이 현재 파서 버전과 다르면 cursor를 버리고 처음부터(통계도 초기화).
    if meta.get("target") != PARSER_VERSION:
        cursor = ""
        stats = {}
        attempts: dict[str, int] = {}
    else:
        cursor = meta.get("cursor") or ""
        stats = dict(meta.get("stats") or {})
        raw_attempts = meta.get("attempts") or {}
        attempts = {str(k): int(v) for k, v in raw_attempts.items()
                    if isinstance(v, int) and v > 0} if isinstance(raw_attempts, dict) else {}
        # 복원된 DB에 옛 커서가 가리키는 ID가 없으면 처음부터(복원 DB에 옛 커서를 쓰지 않음).
        if cursor and cursor not in ids:
            cursor = ""
    # stats 는 중단·재개에 걸쳐 쌓이는 작업 전체 통계(메타에 저장), run 은 이번 실행분(반환값)이다.
    stats.setdefault("reused", 0)
    stats.setdefault("refetched", 0)
    stats.setdefault("changed", 0)
    stats.setdefault("events", 0)
    stats.setdefault("skipped", {})
    run = {"reused": 0, "refetched": 0, "changed": 0, "events": 0, "skipped": {}}
    # 시작 때 DB 정체(데이터셋·장벽 세대)를 잡는다 — 매 신고 처리 때 비교해 다르면 복원으로 보고 멈춘다.
    start_identity = _capture_identity()

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

    def _write_progress(cursor_id: str) -> None:
        write_meta(engine, {"target": PARSER_VERSION, "cursor": cursor_id, "done": False,
                            "total": total, "stats": stats, "attempts": attempts})

    def _restored() -> bool:
        return bool(_restore_hold() or _identity_changed(start_identity))

    def _halt_for_restore() -> bool:
        """복원 일시정지 요청 또는 이미 일어난 교체 — 현재 신고를 끝내고 멈춘다."""
        return bool(restore_paused() or _restored())

    def _record_transient(report_id: str, note: str) -> None:
        count = int(attempts.get(report_id, 0)) + 1
        attempts[report_id] = count
        if count >= MAX_RETRY_ATTEMPTS:
            _tally(skipped=RETRY_EXCEEDED_REASON)
            append_log(f"{report_id} 건너뜀({RETRY_EXCEEDED_REASON}: {note})" if note
                       else f"{report_id} 건너뜀({RETRY_EXCEEDED_REASON})")
            logger.LoggerFactory.logbot.warning(f"[db_optimize] {report_id} 재시도 초과로 건너뜀: {note}")
        else:
            append_log(f"{report_id} 일시 실패({note}), 나중에 다시 시도합니다({count}/{MAX_RETRY_ATTEMPTS})" if note
                       else f"{report_id} 일시 실패, 나중에 다시 시도합니다({count}/{MAX_RETRY_ATTEMPTS})")
            logger.LoggerFactory.logbot.warning(f"[db_optimize] {report_id} 일시 실패로 나중에 다시: {note}")

    def _record_resolved(report_id: str) -> None:
        attempts.pop(report_id, None)

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
            if _restored():
                return False
            stop_event.wait(login_retry_seconds)
            if stop_event.is_set() or _restored():
                return False
            if _ensure_session():
                _update(state="running", stage="running", message="")
                return True
        return False

    def _attempt_report(report_id: str) -> str:
        """한 신고를 한 번 시도한다. 반환: 'resolved' | 'deferred' | 'login_wait' | 'restored' | 'stopped'.

        매 신고 시작과 저장 직전에 멈춤 요청을 확인한다. 복원 일시정지·교체가 보이면
        아무 것도 쓰지 않고 'restored', 서버 종료 등 일반 멈춤 뒤에는 저장하지 않고
        'stopped' 로 멈춤 신호를 올린다. 네트워크 재조회 결과도 저장 직전에 멈춤을
        확인해 복원·종료 뒤 새 DB에 쓰지 않는다.
        """
        from core.storage import reports_repo

        nonlocal session
        fetch_note = ""
        if _halt_for_restore():
            return "restored"
        if stop_event.is_set():
            return "stopped"
        envelope, raw_state = _stored_result(engine, report_id)
        if raw_state == "ok":
            result = envelope.get("result") if isinstance(envelope, dict) else None
            if not isinstance(result, dict):
                _tally(skipped="result_not_object")
                append_log(f"{report_id} 건너뜀(result 없음)")
                _record_resolved(report_id)
                return "resolved"
            details = _parse_result(report_id, result)
            if details is None:
                _tally(skipped="parse_failed")
                _record_resolved(report_id)
                return "resolved"
            rec = _build_rec(report_id, details)
            if _halt_for_restore():
                return "restored"
            if stop_event.is_set():
                return "stopped"
            try:
                _save_reparsed(engine, rec, report_id, reused=True, tally=_tally)
            except reports_repo._ReparseSkipped as exc:
                # _save_reparsed 안에서 이미 tally·로그함 — 영구 건너뜀으로 확정
                _record_resolved(report_id)
                return "resolved"
            except _SaveTransient as exc:
                _record_transient(report_id, f"저장 실패({exc})")
                return "deferred"
            _record_resolved(report_id)
            _ensure_raw_file(report_id, envelope)
            return "resolved"
        # 원본 없음·손상 → 다시 받기(네트워크는 멈춤 확인 뒤 — 저장은 아래에서 다시 확인).
        if not _ensure_session():
            return "login_wait"
        auth_tries = 0
        while True:
            data, outcome, note = _fetch_envelope(
                session, report_id, fetch_fn=fetch_fn or _default_fetch,
                interval=interval, sleep=do_sleep)
            if interval and outcome == "ok":
                do_sleep(interval)
            if outcome == "auth":
                auth_tries += 1
                session = None
                fetch_note = note
                if auth_tries >= MAX_RETRY_ATTEMPTS:
                    _record_transient(report_id, f"로그인 갱신 중 예외({note or 'auth'})")
                    return "deferred"
                if not _ensure_session():
                    return "login_wait"
                continue
            break
        if outcome == "permanent":
            _tally(skipped="not_found")
            detail = note or fetch_note
            append_log(f"{report_id} 건너뜀(not_found{': ' + detail if detail else ''})")
            _record_resolved(report_id)
            return "resolved"
        if outcome == "retryable":
            _record_transient(report_id, note or fetch_note or "fetch_failed")
            return "deferred"
        if outcome != "ok":
            _record_transient(report_id, note or fetch_note or str(outcome))
            return "deferred"
        details = _parse_result(report_id, data["result"])
        if details is None:
            _tally(skipped="parse_failed")
            _record_resolved(report_id)
            return "resolved"
        rec = _build_rec(report_id, details)
        rec.raw_canonical = _raw.canonical_bytes(data)
        rec.raw_sha256 = _raw.sha256_hex(rec.raw_canonical)
        rec.raw_size = len(rec.raw_canonical)
        rec.raw_source = BACKFILL_SOURCE
        # 재조회 결과 저장 직전에 멈춤 확인 — 복원·종료 뒤에는 받은 내용을 쓰지 않는다.
        if _halt_for_restore():
            return "restored"
        if stop_event.is_set():
            return "stopped"
        try:
            _save_reparsed(engine, rec, report_id, reused=False, tally=_tally)
        except reports_repo._ReparseSkipped:
            _record_resolved(report_id)
            return "resolved"
        except _SaveTransient as exc:
            _record_transient(report_id, f"저장 실패({exc})")
            return "deferred"
        _record_resolved(report_id)
        _tally(refetched=True)
        return "resolved"

    index = start_index
    stopped_reason: str | None = None  # None | "stop" | "restore"
    pending_retry: list[str] = []
    # 이전 회차에서 커서는 넘겼지만 다시 시도하기로 한 신고(시도 3회 미만)는 이번 회차 끝 재시도 대상이다.
    try:
        _order = {rid: pos for pos, rid in enumerate(ids)}
        for rid, count in list(attempts.items()):
            if 0 < int(count) < MAX_RETRY_ATTEMPTS and rid in _order and _order[rid] < start_index:
                if rid not in pending_retry:
                    pending_retry.append(rid)
        pending_retry.sort(key=lambda rid: _order[rid])
    except Exception as exc:
        note_fallback("db_optimize.pending_retry_init", exc)
    while index < len(ids):
        if _halt_for_restore():
            stopped_reason = "restore"
            break
        if stop_event.is_set():
            stopped_reason = "stop"
            break
        if not wait_for_turn(is_crawling=is_crawling, rebuild_pending=rebuild_pending,
                             wait_seconds=wait_seconds, stop=stop_event):
            stopped_reason = "restore" if _halt_for_restore() else "stop"
            break
        if _halt_for_restore():
            stopped_reason = "restore"
            break
        if _state.get("stage") in ("waiting_crawl", "waiting_rebuild"):
            _update(state="running", stage="running", message="")
        report_id = ids[index]

        outcome = _attempt_report(report_id)
        if outcome in ("restored", "stopped"):
            stopped_reason = "restore" if _halt_for_restore() else "stop"
            break
        if outcome == "login_wait":
            if not _wait_login():
                stopped_reason = "restore" if _restored() else "stop"
                break
            continue  # 같은 신고부터 다시
        if outcome == "deferred":
            if report_id not in pending_retry:
                pending_retry.append(report_id)
        # 매 신고 처리 때 DB 정체 확인 — 복원이 끼면 옛 커서를 복원 DB에 쓰지 않고 멈춘다.
        if _halt_for_restore():
            stopped_reason = "restore"
            break

        index += 1
        done_count = index
        _update(done=done_count)
        if _halt_for_restore():
            stopped_reason = "restore"
            break
        _write_progress(report_id)

    # 이번 회차 끝 재시도: 일시 실패분 중 아직 3회 미만인 것만 같은 회차에서 다시 시도한다.
    retry_round = 0
    while (stopped_reason is None and pending_retry
           and not stop_event.is_set() and not _restored()):
        retry_round += 1
        if retry_round > MAX_RETRY_ATTEMPTS:
            break
        current = [rid for rid in pending_retry
                   if int(attempts.get(rid, 0)) < MAX_RETRY_ATTEMPTS]
        if not current:
            break
        pending_retry = []
        for report_id in current:
            if _halt_for_restore():
                stopped_reason = "restore"
                pending_retry.extend(r for r in current[current.index(report_id):]
                                     if int(attempts.get(r, 0)) < MAX_RETRY_ATTEMPTS)
                break
            if stop_event.is_set():
                stopped_reason = "stop"
                pending_retry.extend(r for r in current[current.index(report_id):]
                                     if int(attempts.get(r, 0)) < MAX_RETRY_ATTEMPTS)
                break
            if not wait_for_turn(is_crawling=is_crawling, rebuild_pending=rebuild_pending,
                                 wait_seconds=wait_seconds, stop=stop_event):
                stopped_reason = "restore" if _halt_for_restore() else "stop"
                pending_retry.extend(current[current.index(report_id):])
                break
            outcome = _attempt_report(report_id)
            if outcome in ("restored", "stopped"):
                stopped_reason = "restore" if _halt_for_restore() else "stop"
                pending_retry.extend(current[current.index(report_id):])
                break
            if outcome == "login_wait":
                if not _wait_login():
                    stopped_reason = "restore" if _halt_for_restore() else "stop"
                    pending_retry.extend(current[current.index(report_id):])
                    break
                outcome2 = _attempt_report(report_id)
                outcome = outcome2
                if outcome in ("restored", "stopped"):
                    stopped_reason = "restore" if _halt_for_restore() else "stop"
                    pending_retry.extend(r for r in current[current.index(report_id) + 1:]
                                         if int(attempts.get(r, 0)) < MAX_RETRY_ATTEMPTS)
                    break
            if outcome == "deferred" and int(attempts.get(report_id, 0)) < MAX_RETRY_ATTEMPTS:
                if report_id not in pending_retry:
                    pending_retry.append(report_id)
            if _halt_for_restore():
                stopped_reason = "restore"
                break
            _write_progress(ids[index - 1] if index else "")
        # 다음 바퀴에서 다신 시도할 게 없으면 끝
        pending_retry = [rid for rid in pending_retry if int(attempts.get(rid, 0)) < MAX_RETRY_ATTEMPTS]

    # 다음 기동 때 다시 시도할 일시 실패가 남아 있으면 done이 아니다.
    has_pending = any(int(v) < MAX_RETRY_ATTEMPTS for v in attempts.values())
    finished = (stopped_reason is None and index >= len(ids)
                and not pending_retry and not has_pending)
    if stopped_reason == "restore":
        # 복원된 DB에 옛 ID 목록·커서·done을 쓰지 않는다 — 메타를 그대로 둔다.
        append_log("DB 복원으로 멈춤 — 복원된 DB에서 처음부터 다시 판단합니다")
        _update(state="idle", stage="", message="DB 복원으로 멈춤 — 다음 기동 때 다시 판단합니다")
        logger.LoggerFactory.logbot.warning("[db_optimize] DB 복원으로 멈춤(옛 커서 미기록)")
        return {"total": total, "done": done_count, "finished": False, "stats": run}
    if finished and not stop_event.is_set():
        write_meta(engine, {"target": PARSER_VERSION, "cursor": ids[-1] if ids else "",
                            "done": True, "total": total, "stats": stats, "attempts": {}})
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


class _SaveTransient(RuntimeError):
    """일시 저장 실패 — 신고별 시도 횟수를 남기고 나중에 다시 시도하라는 신호."""


def _save_reparsed(engine, rec, report_id: str, *, reused: bool, tally) -> None:
    """`reparse_one` 한 건 + 통계 집계. 영구 건너뜀은 tally, 일시 저장 실패는 _SaveTransient."""
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
        raise _SaveTransient(type(exc).__name__) from exc
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
    """번호 없는 수동 크롤(전체)을 최적화가 끝난 뒤로 미룬다. 반환: 쌓인 개수.

    여러 건은 하나로 합친다 — 전체 크롤은 큐 없이 같은 전체를 돌리므로 첫 요청 하나만 남긴다.
    실제로 시작될 때까지 DB 메타에 남긴다(시작 성공 뒤에 지움)."""
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
    if not items:
        items = [entry]
    # 이미 보류가 있으면 하나로 합쳐 유지한다 — 새 요청을 추가로 쌓지 않는다(유실 없이 1건).
    items = items[:1]
    table = models.sync_meta_table
    try:
        with engine.begin() as conn:
            conn.execute(table.delete().where(table.c.key == DEFERRED_META_KEY))
            conn.execute(table.insert().values(key=DEFERRED_META_KEY,
                                               value=json.dumps(items, ensure_ascii=False)))
    except Exception as exc:
        note_fallback("db_optimize.defer_write", exc)
    return len(items)


def peek_deferred_fulls(engine) -> list:
    """미뤄 둔 전체 크롤을 지우지 않고 읽는다(시작 성공 뒤에 지움)."""
    try:
        with engine.connect() as conn:
            value = conn.execute(select(models.sync_meta_table.c.value).where(
                models.sync_meta_table.c.key == DEFERRED_META_KEY)).scalar()
    except Exception as exc:
        note_fallback("db_optimize.defer_read", exc)
        return []
    try:
        items = json.loads(value) if value else []
    except ValueError:
        return []
    return items if isinstance(items, list) else []


def take_deferred_fulls(engine) -> list:
    """미뤄 둔 전체 크롤을 꺼내고 비운다(호환용 — 신규 경로는 peek 뒤 시작 성공 때만 지운다)."""
    items = peek_deferred_fulls(engine)
    if not items:
        return []
    table = models.sync_meta_table
    try:
        with engine.begin() as conn:
            conn.execute(table.delete().where(table.c.key == DEFERRED_META_KEY))
    except Exception as exc:
        note_fallback("db_optimize.defer_take", exc)
        return []
    return items


def _clear_deferred_fulls(engine) -> None:
    table = models.sync_meta_table
    try:
        with engine.begin() as conn:
            conn.execute(table.delete().where(table.c.key == DEFERRED_META_KEY))
    except Exception as exc:
        note_fallback("db_optimize.defer_take", exc)


def _launch_one_deferred(engine, entry: dict) -> str:
    """보류 전체 1건을 시작한다. 반환: 'started' | 'kept'.

    `_life_lock` 을 잡지 않은 상태에서만 호출한다(크롤 시작은 다른 모듈 잠금을
    잡으므로 수명 잠금 안에서 절대 부르지 않는다 — 모듈 상단 잠금 순서 규칙).

    크롤 시작이 성공한 뒤에만 보류를 지운다. 지우기 실패는 진단만 남긴다 —
    드물게 보류가 남아 다음에 한 번 더 시작될 수 있다(요청 유실보다 낫다).
    시작 실패·서버 종료면 보류는 그대로 남아 다음 훅·기동에서 시작한다.
    """
    from services.crawl_manager import crawl_manager as _manager

    # 최적화가 도는 동안에는 시작하지 않고 계속 보류한다.
    try:
        if is_running():
            try:
                _manager.request_pending_launch()
            except Exception as exc:
                note_fallback("db_optimize.deferred_full_retry", exc)
            return "kept"
    except Exception as exc:
        note_fallback("db_optimize.deferred_full_gate", exc)
        return "kept"
    if _manager.is_crawling():
        try:
            _manager.request_pending_launch()
        except Exception as exc:
            note_fallback("db_optimize.deferred_full_retry", exc)
        logger.LoggerFactory.logbot.info("[db_optimize] 미뤄 둔 전체 크롤 보류 유지: 크롤 실행 중")
        return "kept"
    # 크롤 시작 직전에 복원 일시정지·종료 요청을 수명 잠금으로 잠깐 다시 확인한다.
    # 배출 게이트를 통과한 뒤 `pause_for_restore()` 가 표시를 켰으면 시작하지 않고
    # 보류를 둔다(잠금 순서: 배출 잠금 안에서 수명 잠금을 잠깐 잡는 것은 허용).
    try:
        with _life_lock:
            if _shutdown or _restore_pause or bool(_restore_tokens):
                return "kept"
    except Exception as exc:
        note_fallback("db_optimize.deferred_full_restore_gate", exc)
        return "kept"
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
        # 시작 실패·서버 종료면 보류는 그대로 둔다 — 다음 훅·기동에서 다시 시도한다.
        if "이미 실행 중" in str(exc) or "already" in str(exc).lower():
            try:
                from services.crawl_manager import crawl_manager as _manager2

                _manager2.request_pending_launch()
            except Exception as exc2:
                note_fallback("db_optimize.deferred_full_retry_launch", exc2)
        return "kept"
    else:
        # 시작 성공 뒤에만 보류를 지운다. 지우기 실패는 위에서 진단만 남기고,
        # 드물게 보류가 남아 다음에 한 번 더 시작될 수 있다(요청 유실보다 낫다).
        _clear_deferred_fulls(engine)
        return "started"


def _drain_locked(engine, *, always_request: bool, require_done: bool = False) -> None:
    """`_drain_lock` 보유 가정. 보류 전체 크롤을 시작한다(단일 합침 — 한 번만).

    보류 목록은 이 잠금 안에서 다시 읽는다. 크롤 시작 직전에는 복원 일시정지·
    종료 요청을 수명 잠금으로 잠깐 다시 확인하고(`_launch_one_deferred` 안 최종
    게이트) 걸려 있으면 시작하지 않는다. 한 번 시도한 뒤 다시 확인해 건너뛴
    동시 배출의 몫까지 처리한다 — 시작 성공 뒤 새 보류가 보이면 한 번 더 시도하고,
    시작 실패(kept)·게이트 불충족이면 둔다(즉시 재시도 무한 루프 금지).

    `require_done` 이 True인 기동 경로(`_drain_deferred_if_done`)에서만 살아 있는
    작업자·`needs_run`·크롤 실행 중 게이트를 본다. 완료·오류·훅 경로
    (`_after_finish`·`_after_error`·`maybe_start_deferred_from_crawl`)는 기존처럼
    보류가 있으면 시작한다 — 크롤 실행 중·최적화 진행 중 여부는
    `_launch_one_deferred` 안 게이트가 판단한다.
    """
    if always_request:
        try:
            from services.crawl_manager import crawl_manager

            crawl_manager.request_pending_launch()
        except Exception as exc:
            note_fallback("db_optimize.after_finish", exc)
    while True:
        try:
            with _life_lock:
                if _shutdown or _restore_pause or bool(_restore_tokens):
                    return
                thread = _thread
            if require_done:
                if thread is not None and thread.is_alive():
                    return
                if is_running():
                    return
                try:
                    from services.crawl_manager import crawl_manager as _manager

                    if _manager.is_crawling():
                        try:
                            _manager.request_pending_launch()
                        except Exception as exc:
                            note_fallback("db_optimize.deferred_full_retry", exc)
                        return
                except Exception as exc:
                    note_fallback("db_optimize.deferred_drain_crawl_gate", exc)
                    return
                if engine is None or needs_run(engine):
                    return
            entries = peek_deferred_fulls(engine)
            if not entries:
                return
        except Exception as exc:
            note_fallback("db_optimize.deferred_drain_gate", exc)
            return
        try:
            result = _launch_one_deferred(engine, entries[0])
        except Exception as exc:
            note_fallback("db_optimize.deferred_full", exc)
            return
        # 끝에서 재확인 — 건너뛴 동시 배출의 몫이 있으면 한 번 더 시도한다.
        # kept(게이트·실패로 보류 유지)면 즉시 재시도하지 않고 둔다.
        if result != "started":
            return
        try:
            if not peek_deferred_fulls(engine):
                return
        except Exception as exc:
            note_fallback("db_optimize.deferred_drain_recheck", exc)
            return
        # 새 보류가 보이면 루프가 게이트부터 다시 판단한다.


def maybe_start_deferred_from_crawl(engine) -> None:
    """크롤 종료 훅용: 보류 전체 크롤이 남아 있으면 기존 pending/retry 장치로 시작한다.

    최적화 진행 중이면 계속 보류한다. 전용 배출 잠금으로 한 번에 하나만 돈다 —
    이미 진행 중이면 기다리지 않고 건너뛰고 진행 중인 배출이 끝에서 다시 확인한다."""
    if not _drain_lock.acquire(blocking=False):
        return
    try:
        _drain_locked(engine, always_request=False)
    except Exception as exc:
        note_fallback("db_optimize.deferred_hook", exc)
    finally:
        try:
            _drain_lock.release()
        except Exception as exc:
            note_fallback("db_optimize.drain_release", exc)


def _after_finish(engine) -> None:
    """최적화가 끝나면 대기열 뒤에 붙은 크롤을 자동 시작한다(기존 pending/retry 장치)."""
    if not _drain_lock.acquire(blocking=False):
        return
    try:
        _drain_locked(engine, always_request=True)
    except Exception as exc:
        note_fallback("db_optimize.deferred_on_startup", exc)
    finally:
        try:
            _drain_lock.release()
        except Exception as exc:
            note_fallback("db_optimize.drain_release", exc)


def _after_error(engine) -> None:
    """작업 예외(error) 뒤: 크롤 보류를 풀어 대기 중인 크롤(번호·전체)을 정상 실행한다.

    최적화는 크롤이 끝난 뒤 또는 다음 기동 때 다시 시도한다(무한 차단 금지 — error는 is_running False)."""
    if not _drain_lock.acquire(blocking=False):
        return
    try:
        _drain_locked(engine, always_request=True)
    except Exception as exc:
        note_fallback("db_optimize.deferred_full_after_error", exc)
    finally:
        try:
            _drain_lock.release()
        except Exception as exc:
            note_fallback("db_optimize.drain_release", exc)


# ── 복원 상호 배제 ─────────────────────────────────────────────────────────────

class RestorePauseTimeout(RuntimeError):
    """실행 중 최적화가 시간 안에 멈추지 않아 복원을 거절했다."""


def restore_paused() -> bool:
    """복원 진행 표시가 켜져 있으면 True — 복원 경로가 최적화를 멈추게 한 상태."""
    with _life_lock:
        return _restore_pause


def stop_and_join(timeout: float = RESTORE_PAUSE_TIMEOUT) -> bool:
    """멈춤을 요청하고 스레드가 끝날 때까지 기다린다. 시간 안에 끝나면 True."""
    with _life_lock:
        _stop.set()
        thread = _thread
    if thread is None:
        return True
    if thread is threading.current_thread():
        return False
    thread.join(max(0, timeout))
    return not thread.is_alive()


def pause_for_restore(timeout: float = RESTORE_PAUSE_TIMEOUT) -> object:
    """DB 교체 경로가 장벽을 잡기 전에 부른다: 복원 진행 표시를 켜고 실행 중 최적화에
    멈춤을 요청해 현재 처리 중인 신고 1건(네트워크 재조회 중이면 그 저장을 하지 않고)을
    끝내고 스레드가 끝날 때까지 기다린다. 이렇게 하면 복원과 최적화가 동시에 돌지
    않으므로 ID 목록·정체 경합이 없다(정체 확인은 방어용으로 유지).

    성공하면 소유 토큰을 돌려준다 — 호출자는 `finally` 에서
    `resume_after_restore(token)` 으로 자기가 획득한 경우에만 해제한다(중첩된 두 번째
    복원이 먼저 끝나도 첫 복원의 표시가 꺼지지 않는다).

    시간 안에 끝나지 않으면 토큰만 거두고 RestorePauseTimeout — 호출자는 복원을
    명확한 오류로 거절한다. 스택이 비면 일시정지 요청을 끄고, 서버 종료 요청이
    없을 때만 `_stop` 을 풀어 기존 스레드가 그대로 계속 진행·완료하게 한 뒤
    `_spawn_locked()` 로 재판단한다(작업자가 이미 끝났어도 다시 띄운다 —
    `_shutdown` 이면 안 띄운다). 중첩 호출(스택이 남음)의 시간 초과는 자기 토큰만
    거두고 첫 복원의 표시·멈춤 요청은 그대로 둔다."""

    global _pause_requested, _restore_pause
    token = object()
    with _life_lock:
        _restore_tokens.append(token)
        _pause_requested = True
        _restore_pause = True
    # 일시정지 표시를 켠 뒤 배출 잠금을 한 번 잡았다 놓아 진행 중인 배출이
    # 끝났음을 보장한다. 수명 잠금을 쥔 채 잡지 않고 순차로 잡는다
    # (잠금 순서: 배출 잠금 안에서 수명 잠금을 잠깐 잡는 것은 허용, 역방향 금지).
    with _drain_lock:
        pass
    if stop_and_join(timeout):
        return token
    drain_engine = None
    with _life_lock:
        try:
            _restore_tokens.remove(token)
        except ValueError:
            pass
        if not _restore_tokens:
            _pause_requested = False
            _restore_pause = False
            if not _shutdown:
                _stop.clear()
            # 수명 잠금 안에서는 기동 판단·스레드 생성만 한다. 보류 크롤 시작은
            # 잠금을 놓은 뒤 `_drain_deferred_if_done` 으로 실행한다(잠금 순서 규칙).
            spawned = _try_spawn_locked()
            if spawned is None and not _shutdown:
                drain_engine = _current_engine
    if drain_engine is not None:
        _drain_deferred_if_done(drain_engine)
    raise RestorePauseTimeout(
        "DB 최적화가 끝나지 않아 복원하지 않았습니다. 잠시 뒤 다시 시도하세요.")


def resume_after_restore(token: object = _NO_TOKEN) -> None:
    """복원 종료(성공·실패 공통)에서 복원 진행 표시를 끈다.

    토큰을 넘기면 스택에 그 토큰이 있을 때만(자기가 획득한 경우에만) 거두고,
    스택이 비어야 표시가 꺼진다 — 획득 전 사전 검사 실패(토큰 없음)는 표시를
    건드리지 않는다. 스택이 비면 `_spawn_locked()` 으로 재판단한다.
    인자 없이 부르면(시험 정리·구 경로 호환) 전부 해제만 하고 띄우지 않는다.
    """
    global _pause_requested, _restore_pause
    drain_engine = None
    with _life_lock:
        if token is _NO_TOKEN:
            _restore_tokens.clear()
            _pause_requested = False
            _restore_pause = False
            return
        try:
            _restore_tokens.remove(token)
        except ValueError:
            return
        if _restore_tokens:
            return
        _pause_requested = False
        _restore_pause = False
        # 수명 잠금 안에서는 기동 판단·스레드 생성만 한다. 보류 크롤 시작은
        # 잠금을 놓은 뒤 `_drain_deferred_if_done` 으로 실행한다(잠금 순서 규칙).
        spawned = _try_spawn_locked()
        if spawned is None and not _shutdown:
            drain_engine = _current_engine
    if drain_engine is not None:
        _drain_deferred_if_done(drain_engine)


# ── 작업자 수명 상태 기계 ────────────────────────────────────────────────────
# `_life_lock` 하나가 `_thread`·토큰 스택·`_shutdown`·`_pause_requested` 를 지킨다.
# `_shutdown` 은 `stop()` 만 켜고 새 수명 시작에서만 끈다. 모든 기동·재판단은
# `_spawn_locked()` 하나만 쓴다: `_shutdown` 아님 + 토큰 없음 + 살아 있는 스레드
# 없음 + 할 일 있음(`needs_run`)일 때만 새 스레드를 띄운다.
# 잠금 순서 규칙(모듈 상단): 수명 잠금 안에서는 상태 판단·스레드 생성만 하고,
# 보류 크롤 시작은 잠금을 놓은 뒤 `_drain_deferred_if_done` 으로 실행한다.

def _spawn_locked(engine=None, run_kwargs=None, background: bool = True):
    """`_life_lock` 보유 가정. 조건이 맞을 때만 새 작업자를 띄우고 스레드를 돌려준다.

    살아 있는 스레드가 있으면 새로 띄우지 않고 그 스레드를 돌려준다.
    `maybe_start`·`resume_after_restore`·시간 초과 처리·작업자 종료가 모두 이 함수만 쓴다.
    수명 잠금 안에서는 상태 판단·스레드 생성만 하므로 보류 크롤 시작은 하지 않는다 —
    할 일 없이 끝났을 때 보류가 있으면 호출자가 잠금을 놓은 뒤
    `_drain_deferred_if_done` 으로 시작한다.

    `process_all` 은 새 스레드에서 돈다 — 수명 잠금 안에서는 스레드 객체 생성·
    `_thread` 등록·`_update` 상태 갱신만 하고, 크롤 실행 전체를 잠금 안에서
    돌리지 않는다. `background=False` 호출자는 잠금을 놓은 뒤 `join` 으로
    기다린다(동기 실행 중에도 `restore_paused()` 가 막히지 않는다).
    """
    global _thread, _shutdown, _pause_requested, _restore_pause
    global _current_engine, _current_run_kwargs
    if engine is None:
        engine = _current_engine
        run_kwargs = _current_run_kwargs
    else:
        _current_engine = engine
        _current_run_kwargs = dict(run_kwargs or {})
    run_kwargs = _current_run_kwargs
    thread = _thread
    if thread is not None and thread.is_alive():
        return thread
    _thread = None
    if _shutdown:
        return None
    if _restore_tokens:
        _pause_requested = True
        _restore_pause = True
        return None
    if engine is None:
        return None
    runnable = needs_run(engine)
    # `needs_run` 확인 뒤에 켜진 종료 요청도 존중한다 — 기동 분기가 지우지 않는다.
    if _shutdown:
        return None
    if _restore_tokens:
        _pause_requested = True
        _restore_pause = True
        return None
    if not runnable:
        # 할 일 없음 — 보류 크롤 시작은 수명 잠금을 놓은 뒤 호출자가
        # `_drain_deferred_if_done` 으로 실행한다(잠금 순서 규칙).
        return None
    # 새 수명 시작 — 여기서만 서버 종료 요청을 초기화한다.
    _shutdown = False
    _pause_requested = False
    _restore_pause = False
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

    slot: dict = {}

    def _run():
        global _thread
        failed = False
        try:
            process_all(engine, **run_kwargs)
        except Exception as exc:
            failed = True
            _update(state="error", stage="error", message=type(exc).__name__)
            append_log(f"오류로 멈춤({type(exc).__name__})")
            logger.LoggerFactory.logbot.error(f"[db_optimize] 오류로 멈춤: {exc}")
            try:
                _after_error(engine)
            except Exception as exc2:
                note_fallback("db_optimize.after_error", exc2)
        finally:
            drain_engine = None
            with _life_lock:
                # 자기 스레드일 때만 비운다 — 뒤에 뜬 새 스레드를 지우지 않는다.
                if _thread is slot.get("thread"):
                    _thread = None
                # 재시작 판단은 실행 결과(finished 등)가 아니라 메타 기준 `needs_run` 으로만
                # 한다 — `_spawn_locked` 이 잠금 안에서 판단한다. 실패 종료는 재시작하지
                # 않는다(예외 규칙 유지). 마지막 신고 뒤 일시정지로 done 기록을 건너뛴
                # 경우도 `needs_run` 이 True이므로 시간 초과·resume 때 다시 뜬다.
                # 모듈 `_stop` 이 아닌 주입 stop 으로 도는 시험 작업자는 재판단하지 않는다.
                if not failed and run_kwargs.get("stop", _stop) is _stop:
                    spawned = _try_spawn_locked()
                    if spawned is None and not _shutdown:
                        drain_engine = _current_engine
            if drain_engine is not None:
                _drain_deferred_if_done(drain_engine)

    thread = threading.Thread(target=_run, name="db-optimize", daemon=True)
    slot["thread"] = thread
    _thread = thread
    # 스레드 시작만 잠금 안에서 한다(빠른 생성 — `process_all` 본체는 새 스레드에서
    # 잠금 없이 돈다). `background=False` 여도 여기서 시작하고 호출자가 잠금 밖에서
    # `join` 한다 — `thread.run()` 을 잠금 안에서 직접 돌리지 않는다(잠금 역전 금지).
    thread.start()
    return thread


def _try_spawn_locked():
    """`_life_lock` 보유 가정. 백그라운드 재판단용 — 예외는 진단만 남긴다."""
    try:
        return _spawn_locked()
    except Exception as exc:
        note_fallback("db_optimize.spawn_locked", exc)
        return None


def _drain_deferred_if_done(engine) -> None:
    """보류 전체 크롤을 시작한다 — `_life_lock` 을 잡지 않은 상태에서만 호출한다.

    전용 배출 잠금으로 한 번에 하나만 돈다 — 이미 진행 중이면 기다리지 않고
    건너뛰고, 진행 중인 배출이 끝에서 다시 확인한다. 보류 목록은 그 잠금 안에서
    다시 읽고, 크롤 시작 직전에 복원 일시정지·종료 요청을 수명 잠금으로 잠깐 다시
    확인한다(모듈 상단 잠금 순서 규칙: 배출 잠금 안 수명 잠금은 허용, 역방향 금지).
    크롤 시작(`start_crawl` 등)은 다른 모듈의 잠금을 잡으므로 수명 잠금을 쥔 채
    절대 부르지 않는다. 할 일이 남았거나(`needs_run`) 일시정지·종료 중이거나 살아
    있는 작업자가 있으면 시작하지 않고 보류를 둔다.
    """
    if engine is None:
        return
    if not _drain_lock.acquire(blocking=False):
        return
    try:
        _drain_locked(engine, always_request=False, require_done=True)
    except Exception as exc:
        note_fallback("db_optimize.deferred_on_startup", exc)
    finally:
        try:
            _drain_lock.release()
        except Exception as exc:
            note_fallback("db_optimize.drain_release", exc)


def maybe_start(engine, *, background: bool = True, **run_kwargs) -> threading.Thread | None:
    """스키마 업그레이드 뒤 기동 때 한 번. 할 일 없으면 None(아무 일도 하지 않는다).

    최적화가 이미 done이어도 미뤄 둔 전체 크롤이 있으면 기동 경로에서 꺼내 시작한다 —
    보류 시작은 수명 잠금을 놓은 뒤 실행한다(잠금 순서 규칙). `background=False` 여도
    `process_all` 은 새 스레드에서 잠금 없이 돌고 호출자는 잠금 밖에서 `join` 한다 —
    잠금 안에서는 상태 갱신·스레드 생성만 한다."""
    with _life_lock:
        thread = _spawn_locked(engine, run_kwargs, background)
    if thread is None:
        _drain_deferred_if_done(engine)
        return None
    if not background:
        thread.join()
    return thread


def stop(timeout: float = 5.0) -> bool:
    """서버 종료 때: 다음 신고 경계에서 멈추고 스레드를 기다린다.

    잠금 안에서 `_shutdown` 을 켜고 현재 스레드 참조를 얻은 뒤 잠금 밖에서 join
    한다. 스레드가 없어도 종료 요청은 남는다 — 뒤따르는 재판단이 새 작업자를
    띄우지 않는다."""
    global _shutdown
    with _life_lock:
        _shutdown = True
        _stop.set()
        thread = _thread
    if thread is None:
        return True
    if thread is threading.current_thread():
        return False
    thread.join(max(0, timeout))
    return not thread.is_alive()
