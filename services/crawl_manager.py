import json
import subprocess
import threading
import sys
import os
from contextlib import contextmanager
from typing import Optional, List

from services import crawl_state_store
from services.crawl_log_service import rotate_crawl_log
from core.utils.runtime_mode import block_if_fixture


class CrawlBlockedByRestore(RuntimeError):
    """DB 복원이 진행 중이라 크롤러를 시작하지 않았다(2026-09-26 감사 SOL-04)."""


class RestoreBlocked(RuntimeError):
    """크롤링 중이거나 다른 복원이 진행 중이라 복원을 시작하지 않았다."""


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
                cls._instance._restore_hold = False
        return cls._instance

    @contextmanager
    def hold_for_restore(self):
        """복원 동안 크롤러 시작을 막는다. 크롤링 중이거나 다른 복원 중이면 RestoreBlocked.
        검사와 표시를 start_crawl 과 같은 잠금 안에서 하므로, 검사 직후 크롤러가 끼어들 수 없다(SOL-04)."""
        with self._state_lock:
            if self._active_process is not None and self._active_process.poll() is None:
                raise RestoreBlocked("크롤링이 진행 중입니다. 끝난 뒤 다시 복원하세요.")
            if self._restore_hold:
                raise RestoreBlocked("다른 복원이 진행 중입니다.")
            self._restore_hold = True
        try:
            yield
        finally:
            with self._state_lock:
                self._restore_hold = False
            # 쓰기 장벽·hold 가 모두 풀린 뒤(restore 는 hold 를 바깥에서 잡는다) 대기 큐를 한 번 이어서 처리한다.
            threading.Thread(target=self._resume_pending_after_restore, daemon=True).start()

    def restore_in_progress(self) -> bool:
        with self._state_lock:
            return self._restore_hold

    def is_crawling(self) -> bool:
        """크롤링이 현재 실행 중인지 반환"""
        with self._state_lock:
            return self._active_process is not None and self._active_process.poll() is None

    def start_crawl(self, cmd: list, cwd: str, log_file: str) -> bool:
        """크롤링 프로세스를 시작합니다. 이미 실행 중이면 False 반환."""
        with self._state_lock:
            if self._active_process is not None and self._active_process.poll() is None:
                return False
            if self._restore_hold:
                raise CrawlBlockedByRestore("DB 복원이 진행 중입니다. 끝난 뒤 다시 시작하세요.")

            block_if_fixture("crawl subprocess")
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

    STOP_WAIT_SECONDS = 10.0
    KILL_WAIT_SECONDS = 5.0

    def stop_crawl(self) -> bool:
        """크롤링 강제 종료. 종료 신호를 보낸 뒤 **실제로 끝난 것을 확인한 다음에만** 참조를 지운다(감사 R2-01) —
        그 전까지 is_crawling·hold_for_restore 는 실행 중으로 보고 복원을 막는다. 끝나지 않으면 kill, 그래도 살아 있으면 참조 유지."""
        with self._state_lock:
            proc = self._active_process
            if proc is None or proc.poll() is not None:
                return False
            proc.terminate()
        try:
            proc.wait(timeout=self.STOP_WAIT_SECONDS)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=self.KILL_WAIT_SECONDS)
            except subprocess.TimeoutExpired:
                return True
        self.clear_process(proc)
        return True

    def clear_process(self, proc: Optional[subprocess.Popen] = None):
        """종료 대기 훅이나 로그 회전을 위한 프로세스 참조 초기화. proc 을 주면 그 프로세스일 때만 지운다
        (그 사이 새로 시작한 크롤의 참조를 지우지 않게)."""
        with self._state_lock:
            if proc is None or self._active_process is proc:
                self._active_process = None

    def get_process(self) -> Optional[subprocess.Popen]:
        with self._state_lock:
            return self._active_process

    # ── 대기 큐 (크롤링 중 들어온 신고번호 예약) ─────────────────────────────

    # 대기 큐는 파일에도 남긴다(감사 R2-02): 서버를 다시 시작해도 신고번호를 잃지 않는다. 시작 때 자동으로 크롤하지는 않고,
    # 다음 크롤이 끝나거나 복원이 끝날 때(게이트·초기화 검사 통과 시) 이어서 처리한다.
    PENDING_FILE = "crawl_pending_queue.json"

    def _pending_path(self) -> str:
        import settings.settings as s
        return os.path.join(s.datapath, self.PENDING_FILE)

    def _load_pending_locked(self) -> None:
        if getattr(self, "_pending_loaded", False):
            return
        self._pending_loaded = True
        try:
            with open(self._pending_path(), encoding="utf-8") as f:
                saved = json.load(f)
        except (OSError, ValueError):
            return
        for r in saved if isinstance(saved, list) else []:
            if isinstance(r, str) and r not in self._pending_queue:
                self._pending_queue.append(r)

    def _save_pending_locked(self) -> None:
        path = self._pending_path()
        try:
            if not self._pending_queue:
                if os.path.exists(path):
                    os.remove(path)
                return
            tmp = f"{path}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._pending_queue, f, ensure_ascii=False)
            os.replace(tmp, path)
        except OSError:
            pass  # 파일을 못 써도 메모리 큐는 그대로 동작한다

    def append_to_pending(self, report_number: str) -> int:
        """크롤링 중 들어온 신고번호를 대기 큐에 추가 (중복 제외). 현재 큐 크기 반환."""
        with self._state_lock:
            self._load_pending_locked()
            if report_number not in self._pending_queue:
                self._pending_queue.append(report_number)
                self._save_pending_locked()
            return len(self._pending_queue)

    def pop_pending(self) -> List[str]:
        """대기 큐 전체를 반환하고 초기화."""
        with self._state_lock:
            self._load_pending_locked()
            items = list(self._pending_queue)
            self._pending_queue.clear()
            self._save_pending_locked()
            return items

    def pending_count(self) -> int:
        with self._state_lock:
            self._load_pending_locked()
            return len(self._pending_queue)

    def _resume_pending_after_restore(self) -> None:
        """복원이 끝난 뒤 대기 큐를 한 번 이어서 처리한다(감사 R2-02). 복원은 공유 데이터셋을 회전시켜 1회 초기화가
        필요해질 수 있으므로 일반 크롤과 같은 허용 검사(게이트·초기화)를 먼저 하고, 막히면 큐에 그대로 남긴다."""
        if self.is_crawling() or not self.pending_count():
            return
        try:
            from services import crawl_control
            crawl_control._check_crawl_allowed()
        except Exception:
            return
        pending = self.pop_pending()
        if pending:
            self.launch_pending_crawl(pending)

    # ── 크롤링 완료 후 공통 처리 ──────────────────────────────────────────────

    def _resume_geocode_backfill(self):
        try:
            from core.database.engine import get_engine
            from services import geocode_service
            geocode_service.ensure_map_backfill_started(get_engine(), batch_size=120)
        except Exception as exc:
            from core.utils import logger
            logger.LoggerFactory.logbot.warning(f"[geocode] 크롤링 종료 후 자동 백필 재개 실패: {exc}")

    def run_after_crawl(self, proc, log_file: str):
        """크롤링 프로세스 완료 후 공통 처리 (배경 스레드에서 호출).
        로그 회전 → WS 브로드캐스트 → 대기 큐 자동 실행."""
        import time
        from services.ws_manager import ws_manager

        if proc:
            proc.wait()
        self.clear_process(proc)
        # 초기화 크롤 후처리 훅(T3b): --rebuild <run_id> 로 시작한 크롤이면 같은 run 으로 종결 판정.
        try:
            from services import community_rebuild as _rebuild

            cmd_args = list(getattr(proc, "args", None) or [])
            if "--rebuild" in cmd_args:
                _run_id = str(cmd_args[cmd_args.index("--rebuild") + 1])
                _rebuild.on_crawl_finished(_run_id)
        except Exception:
            pass
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
        if not self.is_crawling():
            self._resume_geocode_backfill()

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
        try:
            started = self.start_crawl(cmd, cwd=work_dir, log_file=log_file)
        except CrawlBlockedByRestore:
            # 복원과 겹쳤다 — 신고번호를 잃지 않게 대기 큐로 되돌린다(다음 크롤링 때 이어서).
            for r in pending:
                self.append_to_pending(str(r))
            return
        if started:
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
