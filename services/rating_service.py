from __future__ import annotations

import os
import shutil
import threading
import time

import settings.settings as settings

from core.utils import logger
from services import data_service
from services.crawl_manager import crawl_manager


def prepare_current_rating_log():
    os.makedirs(settings.logpath, exist_ok=True)
    log_file = os.path.join(settings.logpath, "current_rating.log")
    if os.path.exists(log_file):
        try:
            modified_at = os.path.getmtime(log_file)
            timestamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(modified_at))
            shutil.move(log_file, os.path.join(settings.logpath, f"star_{timestamp}.log"))
        except Exception as exc:
            logger.LoggerFactory.get_logger().error(f"별점 로그 백업 중 오류: {exc}")

    logger.LoggerFactory.set_star_log_file(log_file)
    return log_file


def resolve_rating_targets(engine, raw_values):
    normalized = [str(value).strip() for value in raw_values if str(value).strip()]
    return data_service.resolve_to_report_numbers(engine, normalized)


def _run_rating_worker(report_numbers: list[str], score: int):
    from services import star_rating_service

    star_rating_service.run_batch_rating(report_numbers, score=score)


def start_batch_rating(engine, report_numbers, score: int):
    final_report_numbers = resolve_rating_targets(engine, report_numbers)
    if not final_report_numbers:
        raise ValueError("유효한 신고 건을 찾을 수 없습니다.")

    if crawl_manager.is_crawling():
        raise RuntimeError(
            "현재 크롤링 프로세스가 진행 중입니다. 충돌 방지를 위해 크롤링 종료 후 실행해주세요."
        )

    prepare_current_rating_log()
    threading.Thread(
        target=_run_rating_worker,
        args=(final_report_numbers, score),
        daemon=True,
    ).start()
    return final_report_numbers
