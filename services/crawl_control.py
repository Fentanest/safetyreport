from __future__ import annotations

import functools
import os
import sys
import threading

import settings.settings as settings

from services.crawl_log_service import get_current_crawl_log_path, rotate_crawl_log
from services.crawl_manager import crawl_manager
from services.ws_manager import ws_manager

# 시작 판단(실행 중인가) → 로그 회전 → 프로세스 시작을 한 덩어리로. 웹·모바일·스케줄러 요청이 스레드에서 겹쳐도
# 두 번째 요청이 돌고 있는 크롤링의 로그를 회전시키거나 큐를 잃지 않게 한다(S-29 로 라우터가 스레드풀에서 돈다).
_launch_lock = threading.RLock()


def _serialized(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with _launch_lock:
            return func(*args, **kwargs)
    return wrapper


def get_work_dir():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _write_log_header(header: str, *, rotate_existing: bool = False):
    log_file = get_current_crawl_log_path()
    if rotate_existing:
        rotate_crawl_log(log_file)
    with open(log_file, "w", encoding="utf-8") as file_obj:
        file_obj.write(header)
        if not header.endswith("\n"):
            file_obj.write("\n")
    return log_file


def _log_header(header: str):
    """(로그 경로, prepare) — 로그 교체·머리말 쓰기를 crawl_manager.start_crawl 잠금 안에서 시작이 확정된 뒤에만 하게 한다
    (감사 R4-02: 다른 시작과 겹쳐 실행 중인 크롤의 로그를 지우지 않는다)."""
    log_file = get_current_crawl_log_path()
    return log_file, lambda: _write_log_header(header, rotate_existing=True)


def _build_command(*, crawl_mode: str = "full", queue_file: str | None = None):
    is_frozen = getattr(sys, "frozen", False)
    command = [sys.executable, "--mode", "crawl"] if is_frozen else [sys.executable, "-u", "start.py"]
    if crawl_mode == "reset":
        command.append("--reset")
    if queue_file:
        command.extend(["--queue", queue_file])
    return command


def _build_rebuild_command(run_id: str):
    """초기화 크롤 명령. --reset 은 절대 붙지 않는다."""
    command = _build_command()
    command.extend(["--force", "--rebuild", str(run_id)])
    return command


def _check_crawl_allowed():
    """일반 크롤 시작 전 게이트·초기화 확인(fail-closed: 확인 중 오류도 시작하지 않는다)."""
    from services import community_gate as gate
    from services import community_rebuild as rebuild

    try:
        fresh = gate.require_fresh(max_age=60.0)
    except Exception:
        raise RuntimeError("COMMUNITY_ONBOARDING_REQUIRED") from None
    if not fresh.get("can_enter"):
        raise RuntimeError("COMMUNITY_ONBOARDING_REQUIRED")
    try:
        blocked = rebuild.required() or rebuild.blocking_state() is not None
    except Exception:
        raise RuntimeError("COMMUNITY_REBUILD_REQUIRED") from None
    if blocked:
        raise RuntimeError("COMMUNITY_REBUILD_REQUIRED")


def _refuse_ambiguous(numbers) -> None:
    """이미 여러 신고에 걸리는 번호는 받지 않는다(ValueError → 400, 감사 R7-03) — 대기열에 넣었다가 조용히 버리지 않게."""
    from core.database.engine import get_engine
    from services import report_number_resolver

    try:
        bad = report_number_resolver.ambiguous_numbers(get_engine(), numbers)
    except Exception as exc:  # DB 가 아직 없거나 읽을 수 없으면 막지 않는다 — 크롤러가 목록을 받은 뒤 다시 판정·기록한다
        from core.utils import logger
        logger.LoggerFactory.logbot.warning(f"[crawl] 요청 번호 모호성 검사 생략: {type(exc).__name__}")
        return
    if bad:
        raise ValueError(f"여러 신고에 걸리는 번호라 어느 신고인지 정할 수 없습니다. 정확한 신고번호로 요청하세요: {', '.join(bad[:10])}")


def _write_queue_file(filename: str, queue_content: str):
    """실행마다 고유한 큐 파일(감사 R9-01 — 연속 직접 실행이 서로의 큐·결과 보고를 덮지 않게). 이름: <stem>_<uuid>.txt"""
    import uuid

    stem, ext = os.path.splitext(filename)
    path = os.path.join(settings.datapath, f"{stem}_{uuid.uuid4().hex}{ext or '.txt'}")
    with open(path, "w", encoding="utf-8") as file_obj:
        file_obj.write(queue_content)
    return path


def normalize_crawl_mode(crawl_mode) -> str:
    """full / reset 만 있다. 예전 값(min 등)은 full 로(최소 크롤링은 레거시 전용이라 2026-09-25 제거)."""
    return "reset" if str(crawl_mode or "") == "reset" else "full"


def configure_crawl_settings(*, crawl_mode: str):
    # reset 은 저장하지 않는다(다음 실행이 또 초기화되지 않게). crawl_type·max_empty_pages 는 더 쓰지 않는다.
    settings._instance.update_config("SETTINGS", "crawl_mode", "full")
    settings._instance.save()


def _discard_queue_file(queue_file: str | None) -> None:
    if not queue_file:
        return
    from services import crawl_queue_report

    crawl_queue_report.remove_files(queue_file)


def _start_after_crawl_hook(log_file: str, queue_file: str | None = None):
    """완료 훅. queue_file 이 있으면(큐 지정 직접 시작) 크롤이 끝난 뒤 번호별 보고를 읽어 처리하지 못한 번호(없음·모호)를
    남긴다(감사 R8-02 — 대기 큐 자동 시작과 같은 기록·상태·WS)."""
    process = crawl_manager.get_process()
    if not process:
        return

    def _after():
        try:
            crawl_manager.run_after_crawl(process, log_file)
        finally:
            if queue_file:
                crawl_manager.record_direct_queue_result(queue_file)

    threading.Thread(target=_after, daemon=True, name="crawl-direct-after").start()


@_serialized
def start_rebuild(run_id: str):
    """초기화 크롤 시작. 기존 락·로그·after hook 을 재사용한다."""
    from services.crawl_manager import crawl_manager as _manager

    if _manager.is_crawling():
        raise RuntimeError("크롤링이 이미 실행 중입니다.")
    command = _build_rebuild_command(run_id)
    log_file, prepare = _log_header(f"=== [초기화 크롤링] run {run_id} ===")
    if not _manager.start_crawl(command, cwd=get_work_dir(), log_file=log_file, prepare=prepare):
        raise RuntimeError("크롤링 프로세스를 시작하지 못했습니다.")
    ws_manager.broadcast_from_thread(
        "crawl_started",
        {"source": "community_rebuild", "run_id": str(run_id), "crawl_type": "api"},
    )
    _start_after_crawl_hook(log_file)
    return log_file


@_serialized
def start_crawl(
    *,
    crawl_mode: str,
    queue_list: str = "",
    queue_filename: str = "queue.txt",
    header: str,
    broadcast_source: str,
):
    if crawl_manager.is_crawling():
        raise RuntimeError("크롤링이 이미 실행 중입니다.")

    generation = crawl_manager.restore_generation()  # 검사 뒤 복원이 끼면 시작하지 않는다(R4-03)
    _check_crawl_allowed()

    if queue_list.strip():
        _refuse_ambiguous(queue_list.splitlines())  # R8-02: 모바일 /crawl/start·웹 시작도 같은 거부

    crawl_mode = normalize_crawl_mode(crawl_mode)
    configure_crawl_settings(crawl_mode=crawl_mode)

    queue_file = None
    if queue_list.strip():
        queue_file = _write_queue_file(queue_filename, queue_list)

    command = _build_command(
        crawl_mode=crawl_mode,
        queue_file=queue_file,
    )
    log_file, prepare = _log_header(header)
    try:
        started = crawl_manager.start_crawl(command, cwd=get_work_dir(), log_file=log_file, prepare=prepare,
                                            restore_generation=generation)
    except BaseException:
        _discard_queue_file(queue_file)
        raise
    if not started:
        _discard_queue_file(queue_file)  # 시작하지 못한 실행의 고유 큐 파일은 남기지 않는다(R10-03)
        raise RuntimeError("크롤링 프로세스를 시작하지 못했습니다.")

    ws_manager.broadcast_from_thread(
        "crawl_started",
        {
            "source": broadcast_source,
            "crawl_mode": crawl_mode,
            "crawl_type": "api",  # 구앱 호환(이벤트 필드 유지)
        },
    )
    _start_after_crawl_hook(log_file, queue_file)
    return log_file


@_serialized
def enqueue_report(report_number: str):
    normalized = str(report_number).strip()
    if not normalized:
        raise ValueError("report_number is required")
    _refuse_ambiguous([normalized])

    generation = crawl_manager.restore_generation()  # R4-03
    _check_crawl_allowed()

    if crawl_manager.is_crawling():
        queue_size = crawl_manager.append_to_pending(normalized)
        # 실행 중이던 크롤이 막 끝나 완료 훅이 번호 추가 전에 지나갔을 수 있다 — 한 번 더 시도(R5-02)
        crawl_manager.request_pending_launch()
        return {"status": "queued", "queue_size": queue_size}

    queue_file = _write_queue_file("mobile_queue.txt", normalized)
    log_file, prepare = _log_header(f"=== [모바일에서 시작된 크롤링] - 신고번호: {normalized} ===")
    command = _build_command(queue_file=queue_file)
    try:
        started = crawl_manager.start_crawl(command, cwd=get_work_dir(), log_file=log_file, prepare=prepare,
                                            restore_generation=generation)
    except BaseException:
        _discard_queue_file(queue_file)
        raise
    if not started:
        _discard_queue_file(queue_file)  # R10-03
        # 그 사이 다른 크롤(대기 큐 자동 시작 등)이 먼저 시작했다 — 번호를 대기 큐에 넣고, 그 크롤의 완료 훅이 이미
        # 지나갔을 수 있으니 한 번 더 시작을 시도한다(R5-02 — 실행 중이면 그 크롤이 끝날 때 이어서 처리)
        queue_size = crawl_manager.append_to_pending(normalized)
        crawl_manager.request_pending_launch()
        return {"status": "queued", "queue_size": queue_size}

    ws_manager.broadcast_from_thread(
        "crawl_started",
        {
            "source": "mobile_enqueue",
            "report_number": normalized,
            "crawl_mode": settings.crawl_mode,
            "crawl_type": settings.crawl_type,
        },
    )
    _start_after_crawl_hook(log_file, queue_file)
    return {"status": "success", "queue_size": 1}


@_serialized
def enqueue_reports(report_numbers: list[str], *, source: str = "web_selected"):
    normalized = []
    seen = set()
    for report_number in report_numbers:
        value = str(report_number).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)

    if not normalized:
        raise ValueError("report_numbers is required")
    _refuse_ambiguous(normalized)

    generation = crawl_manager.restore_generation()  # R4-03
    _check_crawl_allowed()

    def _queue_all():
        queue_size = 0
        for report_number in normalized:
            queue_size = crawl_manager.append_to_pending(report_number)
        return {
            "status": "queued",
            "requested_count": len(normalized),
            "queue_size": queue_size,
        }

    if crawl_manager.is_crawling():
        result = _queue_all()
        crawl_manager.request_pending_launch()  # R5-02
        return result

    queue_file = _write_queue_file("web_selected_queue.txt", "\n".join(normalized))
    log_file, prepare = _log_header(
        f"=== [웹 선택 크롤링] 신고번호 {len(normalized)}건 ===\n"
        + "\n".join(f"  - {report_number}" for report_number in normalized)
    )
    command = _build_command(queue_file=queue_file)
    try:
        started = crawl_manager.start_crawl(command, cwd=get_work_dir(), log_file=log_file, prepare=prepare,
                                            restore_generation=generation)
    except BaseException:
        _discard_queue_file(queue_file)
        raise
    if not started:
        _discard_queue_file(queue_file)  # R10-03
        result = _queue_all()  # 그 사이 다른 크롤이 먼저 시작했다 — 대기 큐로(R5-02: 한 번 더 시작 시도)
        crawl_manager.request_pending_launch()
        return result

    ws_manager.broadcast_from_thread(
        "crawl_started",
        {
            "source": source,
            "count": len(normalized),
            "crawl_mode": settings.crawl_mode,
            "crawl_type": settings.crawl_type,
        },
    )
    _start_after_crawl_hook(log_file, queue_file)
    return {
        "status": "success",
        "requested_count": len(normalized),
        "queue_size": len(normalized),
    }


@_serialized
def stop_crawl():
    if not crawl_manager.is_crawling():
        return False
    crawl_manager.stop_crawl()
    with open(get_current_crawl_log_path(), "a", encoding="utf-8") as file_obj:
        file_obj.write("\n[시스템] 사용자 요청으로 크롤링 프로세스가 강제 종료되었습니다.\n")
    return True


