import subprocess
import threading
import sys
import os
from typing import Optional, List

from services import crawl_state_store
from services.crawl_log_service import rotate_crawl_log

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

    # ── 크롤링 완료 후 공통 처리 ──────────────────────────────────────────────

    def run_after_crawl(self, proc, log_file: str):
        """크롤링 프로세스 완료 후 공통 처리 (배경 스레드에서 호출).
        로그 회전 → WS 브로드캐스트 → 대기 큐 자동 실행."""
        import time
        from services.ws_manager import ws_manager

        if proc:
            proc.wait()
        self.clear_process()
        time.sleep(1)

        if os.path.exists(log_file):
            try:
                with open(log_file, 'a', encoding='utf-8') as f:
                    f.write("\n[시스템] 크롤링 작업이 완료되었습니다.\n")
                rotate_crawl_log(log_file)
            except Exception:
                pass

        try:
            done = crawl_state_store.get_and_clear_crawl_done()
            changed_count = done["changed_count"] if done else 0
            ws_manager.broadcast_from_thread("crawl_finished", {"changed_count": changed_count})
        except Exception:
            changed_count = 0
        try:
            changes = crawl_state_store.peek_crawl_changes()
            if changes:
                ws_manager.broadcast_from_thread("crawl_changes", {"changes": changes})
            crawl_state_store.save_crawl_done_ext(changed_count, changes or [])
        except Exception:
            pass

        pending = self.pop_pending()
        if pending:
            self.launch_pending_crawl(pending)

    def launch_pending_crawl(self, pending: list):
        """대기 큐의 신고번호로 새 크롤링을 즉시 시작 (배경 스레드에서 호출 가능)."""
        import settings.settings as s
        from services.ws_manager import ws_manager

        if not pending:
            return

        is_frozen = getattr(sys, 'frozen', False)
        cmd = [sys.executable, "--mode", "crawl"] if is_frozen else [sys.executable, "-u", "start.py"]

        queue_file = os.path.join(s.datapath, 'pending_queue.txt')
        with open(queue_file, 'w', encoding='utf-8') as f:
            f.write('\n'.join(str(r) for r in pending))
        cmd.extend(["--queue", queue_file])

        log_dir = os.path.join(s.datapath, 'logs')
        log_file = os.path.join(log_dir, 'current_crawl.log')
        rotate_crawl_log(log_file)

        with open(log_file, 'w', encoding='utf-8') as f:
            f.write(f"=== [대기 큐 자동 시작] 신고번호 {len(pending)}건 ===\n")
            f.write('\n'.join(f"  - {r}" for r in pending) + '\n')

        work_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        if self.start_crawl(cmd, cwd=work_dir, log_file=log_file):
            ws_manager.broadcast_from_thread("crawl_started", {
                "source": "pending_queue",
                "count": len(pending),
                "crawl_mode": s.crawl_mode,
                "crawl_type": s.crawl_type,
            })
            proc = self.get_process()
            if proc:
                threading.Thread(
                    target=self.run_after_crawl, args=(proc, log_file), daemon=True
                ).start()


crawl_manager = CrawlManager()
