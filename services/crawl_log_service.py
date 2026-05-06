from __future__ import annotations

import os
import shutil
from datetime import datetime

import settings.settings as settings


def get_current_crawl_log_path() -> str:
    os.makedirs(settings.logpath, exist_ok=True)
    return os.path.join(settings.logpath, "current_crawl.log")


def rotate_crawl_log(log_file: str | None = None) -> None:
    target = log_file or get_current_crawl_log_path()
    if not os.path.exists(target):
        return
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H_%M_%S")
        shutil.copy2(target, os.path.join(os.path.dirname(target), f"crawl_{timestamp}.log"))
    except Exception:
        pass
