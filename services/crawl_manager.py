import subprocess
import threading
import sys
import os
from typing import Optional, List

class CrawlManager:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(CrawlManager, cls).__new__(cls)
                cls._instance._active_process = None
                cls._instance._state_lock = threading.Lock()
                cls._instance._pending_queue: List[str] = []
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
            
            # Force UTF-8 for subprocesses on Windows to avoid encoding issues in log streaming
            env = os.environ.copy()
            env["PYTHONUTF8"] = "1"
            
            self._active_process = subprocess.Popen(
                cmd,
                cwd=cwd,
                stdout=open(log_file, 'a', encoding='utf-8', errors='replace'),
                stderr=subprocess.STDOUT,
                env=env,
                encoding='utf-8',
                errors='replace'
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

    # ── 대기 큐 (크롤링 중 들어온 신고번호 예약) ─────────────────────────────

    def append_to_pending(self, report_number: str) -> int:
        """크롤링 중 들어온 신고번호를 대기 큐에 추가 (중복 제외). 현재 큐 크기 반환."""
        with self._state_lock:
            if report_number not in self._pending_queue:
                self._pending_queue.append(report_number)
            return len(self._pending_queue)

    def pop_pending(self) -> List[str]:
        """대기 큐 전체를 반환하고 초기화."""
        with self._state_lock:
            items = list(self._pending_queue)
            self._pending_queue.clear()
            return items

    def pending_count(self) -> int:
        with self._state_lock:
            return len(self._pending_queue)

crawl_manager = CrawlManager()
