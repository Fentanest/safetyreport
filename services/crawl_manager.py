import subprocess
import threading
import sys
import os
from typing import Optional

class CrawlManager:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(CrawlManager, cls).__new__(cls)
                cls._instance._active_process = None
                cls._instance._state_lock = threading.Lock()
        return cls._instance

    def is_crawling(self) -> bool:
        """크롤링이 현재 실행 중인지 반환"""
        with self._state_lock:
            return self._active_process is not None and self._active_process.poll() is None

    def start_crawl(self, cmd: list, cwd: str, log_file: str) -> bool:
        """크롤링 프로세스를 시작합니다. 이미 실행 중이면 False 반환."""
        with self._state_lock:
            if self._active_process is not None and self._active_process.poll() is None:
                return False

            os.makedirs(os.path.dirname(log_file), exist_ok=True)
            self._active_process = subprocess.Popen(
                cmd,
                cwd=cwd,
                stdout=open(log_file, 'a', encoding='utf-8'),
                stderr=subprocess.STDOUT,
                text=True
            )
            return True

    def stop_crawl(self) -> bool:
        """크롤링 강제 종료"""
        with self._state_lock:
            if self._active_process is not None and self._active_process.poll() is None:
                self._active_process.terminate()
                self._active_process = None
                return True
            return False

    def clear_process(self):
        """종료 대기 훅이나 로그 회전을 위한 프로세스 참조 초기화"""
        with self._state_lock:
            self._active_process = None

    def get_process(self) -> Optional[subprocess.Popen]:
        with self._state_lock:
            return self._active_process

crawl_manager = CrawlManager()
