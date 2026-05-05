from __future__ import annotations

import os
import shutil
import sys
import threading
from datetime import datetime

import settings.settings as settings

from services.crawl_manager import crawl_manager
from services.ws_manager import ws_manager


def get_work_dir():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def get_current_crawl_log_path():
    os.makedirs(settings.logpath, exist_ok=True)
    return os.path.join(settings.logpath, "current_crawl.log")


def rotate_crawl_log(log_file: str | None = None):
    target = log_file or get_current_crawl_log_path()
    if not os.path.exists(target):
        return
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H_%M_%S")
        shutil.copy2(target, os.path.join(os.path.dirname(target), f"crawl_{timestamp}.log"))
    except Exception:
        pass


def _write_log_header(header: str, *, rotate_existing: bool = False):
    log_file = get_current_crawl_log_path()
    if rotate_existing:
        rotate_crawl_log(log_file)
    with open(log_file, "w", encoding="utf-8") as file_obj:
        file_obj.write(header)
        if not header.endswith("\n"):
            file_obj.write("\n")
    return log_file


def _build_command(*, login_mode: str = "member", crawl_mode: str = "full", queue_file: str | None = None):
    is_frozen = getattr(sys, "frozen", False)
    command = [sys.executable, "--mode", "crawl"] if is_frozen else [sys.executable, "-u", "start.py"]
    if login_mode == "nonmember":
        command.append("--nonmember")
    if crawl_mode == "min":
        command.append("--min")
    elif crawl_mode == "reset":
        command.append("--reset")
    if queue_file:
        command.extend(["--queue", queue_file])
    return command


def _write_queue_file(filename: str, queue_content: str):
    path = os.path.join(settings.datapath, filename)
    with open(path, "w", encoding="utf-8") as file_obj:
        file_obj.write(queue_content)
    return path


def configure_crawl_settings(*, crawl_type: str, crawl_mode: str, max_empty_pages: int):
    settings._instance.update_config("SETTINGS", "max_empty_pages", max_empty_pages)
    settings._instance.update_config("Crawler", "crawl_type", "api" if crawl_type == "api" else "legacy")
    settings._instance.update_config("SETTINGS", "crawl_mode", "full" if crawl_mode == "reset" else crawl_mode)
    settings._instance.save()


def _start_after_crawl_hook(log_file: str):
    process = crawl_manager.get_process()
    if process:
        threading.Thread(
            target=crawl_manager.run_after_crawl,
            args=(process, log_file),
            daemon=True,
        ).start()


def start_crawl(
    *,
    login_mode: str,
    crawl_mode: str,
    crawl_type: str,
    max_empty_pages: int,
    queue_list: str = "",
    queue_filename: str = "queue.txt",
    header: str,
    broadcast_source: str,
):
    if crawl_manager.is_crawling():
        raise RuntimeError("크롤링이 이미 실행 중입니다.")

    configure_crawl_settings(
        crawl_type=crawl_type,
        crawl_mode=crawl_mode,
        max_empty_pages=max_empty_pages,
    )

    queue_file = None
    if queue_list.strip():
        queue_file = _write_queue_file(queue_filename, queue_list)

    command = _build_command(
        login_mode=login_mode,
        crawl_mode=crawl_mode,
        queue_file=queue_file,
    )
    log_file = _write_log_header(header, rotate_existing=True)
    if not crawl_manager.start_crawl(command, cwd=get_work_dir(), log_file=log_file):
        raise RuntimeError("크롤링 프로세스를 시작하지 못했습니다.")

    ws_manager.broadcast_from_thread(
        "crawl_started",
        {
            "source": broadcast_source,
            "login_mode": login_mode,
            "crawl_mode": crawl_mode,
            "crawl_type": crawl_type,
        },
    )
    _start_after_crawl_hook(log_file)
    return log_file


def enqueue_report(report_number: str):
    normalized = str(report_number).strip()
    if not normalized:
        raise ValueError("report_number is required")

    if crawl_manager.is_crawling():
        queue_size = crawl_manager.append_to_pending(normalized)
        return {"status": "queued", "queue_size": queue_size}

    queue_file = _write_queue_file("mobile_queue.txt", normalized)
    log_file = _write_log_header(
        f"=== [모바일에서 시작된 크롤링] - 신고번호: {normalized} ===",
        rotate_existing=True,
    )
    command = _build_command(queue_file=queue_file)
    if not crawl_manager.start_crawl(command, cwd=get_work_dir(), log_file=log_file):
        raise RuntimeError("크롤링 프로세스를 시작하지 못했습니다.")

    ws_manager.broadcast_from_thread(
        "crawl_started",
        {
            "source": "mobile_enqueue",
            "report_number": normalized,
            "crawl_mode": settings.crawl_mode,
            "crawl_type": settings.crawl_type,
        },
    )
    _start_after_crawl_hook(log_file)
    return {"status": "success", "queue_size": 1}


def stop_crawl():
    if not crawl_manager.is_crawling():
        return False
    crawl_manager.stop_crawl()
    with open(get_current_crawl_log_path(), "a", encoding="utf-8") as file_obj:
        file_obj.write("\n[시스템] 사용자 요청으로 크롤링 프로세스가 강제 종료되었습니다.\n")
    return True


def resume_crawl():
    signal_file = os.path.join(settings.datapath, "resume.sig")
    with open(signal_file, "w", encoding="utf-8") as file_obj:
        file_obj.write("RESUME")
    return signal_file
