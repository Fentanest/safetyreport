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
                # 복원이 시작될 때마다 +1. 크롤 시작 전 허용 검사를 한 뒤 복원이 끼어들었으면 시작하지 않는다(감사 R4-03).
                cls._instance._restore_generation = 0
                cls._instance._reserved: set = set()  # 실행 중인 대기 큐 크롤이 맡은 번호(R4-01)
                cls._instance._launch_lock = threading.Lock()  # 대기 큐 시작 직렬화(R4-02)
                cls._instance._retry_timer = None  # 남은 번호 재시도(R5-02)
                cls._instance._retry_delay = cls.RETRY_FIRST_SECONDS
                cls._instance._request_worker_active = False  # 시작 요청 합치기(R6-04)
                cls._instance._request_again = False
        return cls._instance

    def restore_generation(self) -> int:
        with self._state_lock:
            return self._restore_generation

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
            self._restore_generation += 1
        try:
            yield
        finally:
            with self._state_lock:
                self._restore_hold = False
            # 쓰기 장벽·hold 가 모두 풀린 뒤(restore 는 hold 를 바깥에서 잡는다) 대기 큐를 한 번 이어서 처리한다.
            threading.Thread(target=self._resume_pending_after_restore, daemon=True, name="crawl-resume-after-restore").start()

    def restore_in_progress(self) -> bool:
        with self._state_lock:
            return self._restore_hold

    def is_crawling(self) -> bool:
        """크롤링이 현재 실행 중인지 반환"""
        with self._state_lock:
            return self._active_process is not None and self._active_process.poll() is None

    def start_crawl(self, cmd: list, cwd: str, log_file: str, *, prepare=None,
                    restore_generation: Optional[int] = None) -> bool:
        """크롤링 프로세스를 시작합니다. 이미 실행 중이면 False 반환.
        restore_generation: 호출자가 허용 검사(게이트·초기화) 전에 읽은 복원 세대. 그 사이 복원이 있었으면 시작하지 않는다(R4-03).
        prepare: 시작이 확정된 뒤(같은 잠금 안, Popen 직전)에만 실행 — 로그 교체·큐 파일 작성이 다른 시작과 겹치지 않는다(R4-02)."""
        with self._state_lock:
            if self._active_process is not None and self._active_process.poll() is None:
                return False
            if self._restore_hold:
                raise CrawlBlockedByRestore("DB 복원이 진행 중입니다. 끝난 뒤 다시 시작하세요.")
            if restore_generation is not None and restore_generation != self._restore_generation:
                raise CrawlBlockedByRestore("DB 가 복원되어 공유 데이터 확인을 다시 해야 합니다. 다시 시작하세요.")

            block_if_fixture("crawl subprocess")
            os.makedirs(os.path.dirname(log_file), exist_ok=True)
            if prepare is not None:
                prepare()

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
        """큐를 파일에 원자적으로 쓴다. 실패하면 OSError 를 그대로 올린다(감사 R3-03 — 저장 실패를 숨기지 않는다)."""
        path = self._pending_path()
        if not self._pending_queue:
            if os.path.exists(path):
                os.remove(path)
            return
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._pending_queue, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    def append_to_pending(self, report_number: str) -> int:
        """크롤링 중 들어온 신고번호를 대기 큐에 추가 (중복 제외). 현재 큐 크기 반환.
        파일에 남기지 못하면 메모리에도 넣지 않고 RuntimeError — 호출자가 '대기열에 넣음' 으로 답하지 않게 한다(R3-03)."""
        with self._state_lock:
            self._load_pending_locked()
            if report_number not in self._pending_queue:
                self._pending_queue.append(report_number)
                try:
                    self._save_pending_locked()
                except OSError as exc:
                    self._pending_queue.remove(report_number)
                    raise RuntimeError(f"대기 큐를 저장하지 못했습니다({type(exc).__name__}). 잠시 뒤 다시 요청하세요.") from None
            return len(self._pending_queue)

    def pending_items(self) -> List[str]:
        """큐 전체(실행 중인 대기 큐 크롤이 맡은 번호 포함)."""
        with self._state_lock:
            self._load_pending_locked()
            return list(self._pending_queue)

    def _unreserved_locked(self) -> List[str]:
        self._load_pending_locked()
        return [r for r in self._pending_queue if r not in self._reserved]

    @classmethod
    def _read_queue_report(cls, queue_file: str) -> set:
        from services import crawl_queue_report

        done, not_found, ambiguous = crawl_queue_report.read(queue_file)
        from core.utils import logger
        if not_found:
            logger.LoggerFactory.logbot.warning(f"[crawl] 대기 큐에서 목록 전체에 없는 신고번호 제외: {not_found[:20]}")
        if ambiguous:
            logger.LoggerFactory.logbot.error(
                f"[crawl] 여러 신고에 걸리는 번호라 대기 큐에서 제외 — 정확한 신고번호로 다시 요청하세요: {ambiguous[:20]}")
        if (not_found or ambiguous) and not cls._publish_unresolved(not_found, ambiguous):
            # 기록을 못 남기면 큐에서 빼지 않는다 — 사용자가 볼 수 없는 채로 사라지지 않게(감사 R8-03). 다음에 다시 판정한다.
            done = done - set(not_found) - set(ambiguous)
        return done

    @staticmethod
    def _publish_unresolved(not_found, ambiguous) -> bool:
        """처리하지 못한 번호를 사용자가 볼 수 있게 남긴다(감사 R7-03): 파일(/crawl/status 의 unresolved)·WS. 파일에 남겼으면 True."""
        from services import crawl_queue_report

        if not crawl_queue_report.record_unresolved(not_found, ambiguous):
            return False
        try:
            from services.ws_manager import ws_manager
            ws_manager.broadcast_from_thread("crawl_queue_unresolved", {"not_found": list(not_found), "ambiguous": list(ambiguous)})
        except Exception:
            pass
        return True

    def record_direct_queue_result(self, queue_file: str) -> bool:
        """큐 지정 **직접 시작** 크롤이 끝난 뒤(감사 R8-02·R9-01·R9-02): 없음·모호 번호를 기록하고 실행 파일(고유 큐·보고)을 지운다.
        기록을 저장하지 못하면 보고 파일을 지우지 않고 남긴다(False). 직접 시작은 대기 큐가 아니므로 처리하지 못한 번호를 자동으로
        다시 돌리지 않는다 — 사용자가 멈춘 크롤을 되살리지 않게(감사 R10-02). 결과는 크롤 로그와 /crawl/status 의 unresolved 로 본다."""
        from services import crawl_queue_report

        _, not_found, ambiguous = crawl_queue_report.read(queue_file)
        if (not_found or ambiguous) and not self._publish_unresolved(not_found, ambiguous):
            return False
        crawl_queue_report.remove_files(queue_file)
        return True

    def _settle_pending(self, items: List[str], done: set) -> List[str]:
        """대기 큐 크롤이 끝난 뒤: 자식이 끝냈다고 보고한 번호만 큐에서 빼고, 나머지는 예약만 풀어 큐에 남긴다(R4-01·R5-01).
        남은 번호 목록을 돌려준다. 파일 저장 실패는 기록만(재시작 뒤 한 번 더 조회될 뿐)."""
        with self._state_lock:
            self._load_pending_locked()
            self._reserved.difference_update(items)
            finished = set(items) & done
            if not finished:
                return list(items)
            self._pending_queue[:] = [r for r in self._pending_queue if r not in finished]
            try:
                self._save_pending_locked()
            except OSError as exc:
                from core.utils import logger
                logger.LoggerFactory.logbot.warning(f"[crawl] 대기 큐 파일 갱신 실패: {type(exc).__name__}")
            return [r for r in items if r not in finished]

    # ── 남은 번호 다시 시도(감사 R5-02) — 실패한 번호로 곧바로 다시 돌지 않게 늘어나는 간격으로 ──────────────
    RETRY_FIRST_SECONDS = 60.0
    RETRY_MAX_SECONDS = 1800.0

    def request_pending_launch(self) -> None:
        """대기 번호를 넣은 쪽이 한 번 더 시작을 시도하게 한다(완료 훅이 이미 지나간 경우 대비). 요청은 **작업자 하나로 합친다**
        (감사 R6-04): 이미 작업자가 있으면 '한 번 더' 표시만 한다. 작업자를 못 띄우면 재시도 타이머에 맡긴다(예외를 올리지 않음)."""
        with self._state_lock:
            if self._request_worker_active:
                self._request_again = True
                return
            self._request_worker_active = True
            self._request_again = False
        try:
            threading.Thread(target=self._request_worker, daemon=True, name="crawl-pending-request").start()
        except Exception:
            with self._state_lock:
                self._request_worker_active = False
            self._schedule_retry()

    def _request_worker(self) -> None:
        while True:
            try:
                self.launch_pending_crawl()
            except Exception as exc:
                from core.utils import logger
                logger.LoggerFactory.logbot.warning(f"[crawl] 대기 큐 시작 요청 실패: {type(exc).__name__}")
                self._schedule_retry()
            # '더 할 일 없음' 판단과 작업자 종료 표시를 한 잠금 구간에서 — 그 사이 들어온 요청이 유실되지 않게(감사 R7-02)
            with self._state_lock:
                if not self._request_again:
                    self._request_worker_active = False
                    return
                self._request_again = False

    def schedule_retry_if_pending(self) -> None:
        """서버 기동 때: 파일에 남은 대기 번호가 있으면 재시도 타이머 하나를 건다(감사 R6-03 — 재시작으로 재시도가 사라지지 않게).
        시작 때 곧바로 크롤하지는 않는다."""
        if self.pending_count():
            self._schedule_retry()

    def _schedule_retry(self) -> None:
        with self._state_lock:
            if self._retry_timer is not None:
                return
            delay = self._retry_delay
            self._retry_delay = min(delay * 2, self.RETRY_MAX_SECONDS)
            timer = threading.Timer(delay, self._retry_fire)
            timer.daemon = True
            timer.name = "crawl-pending-retry"
            self._retry_timer = timer
        timer.start()

    def _retry_fire(self) -> None:
        with self._state_lock:
            self._retry_timer = None
        started = False
        try:
            if not self.is_crawling() and self.pending_count():
                started = self.launch_pending_crawl()
        except Exception as exc:
            from core.utils import logger
            logger.LoggerFactory.logbot.warning(f"[crawl] 대기 큐 재시도 실패: {type(exc).__name__}")
        # 게이트·초기화에 막히거나 시작하지 못했는데 번호가 남았으면 늘어나는 간격으로 다시 건다(R6-03).
        # 실행 중이면 그 크롤의 완료 훅이 이어서 처리하므로 걸지 않는다.
        if not started and not self.is_crawling() and self.pending_count():
            self._schedule_retry()

    def pop_pending(self) -> List[str]:
        """대기 큐 전체를 반환하고 초기화(테스트·수동 정리용 — 자동 시작은 launch_pending_crawl 이 시작 성공 뒤에만 뺀다)."""
        with self._state_lock:
            self._load_pending_locked()
            items = list(self._pending_queue)
            self._pending_queue.clear()
            self._reserved.clear()
            try:
                self._save_pending_locked()
            except OSError:
                pass
            return items

    def pending_count(self) -> int:
        """아직 어떤 실행도 맡지 않은 대기 번호 수."""
        with self._state_lock:
            return len(self._unreserved_locked())

    def _resume_pending_after_restore(self) -> None:
        """복원이 끝난 뒤 대기 큐를 한 번 이어서 처리한다(감사 R2-02). 검사·시작·큐 갱신은 launch_pending_crawl 한 경계."""
        if not self.is_crawling() and self.pending_count():
            self.launch_pending_crawl()

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

        if self.pending_count():
            self.launch_pending_crawl()
        if not self.is_crawling():
            self._resume_geocode_backfill()

    def launch_pending_crawl(self) -> bool:
        """대기 큐의 신고번호로 새 크롤링을 시작한다(배경 스레드에서 호출 가능). 모든 자동 시작의 한 경계:
        - 한 번에 하나만(R4-02): 같은 번호를 두 실행이 맡지 않는다.
        - 복원 세대를 먼저 읽고 일반 크롤과 같은 허용 검사(게이트·1회 초기화). 검사 뒤 복원이 끼면 시작하지 않는다(R3-02·R4-03).
        - 로그 교체·실행별 큐 파일 작성은 start_crawl 잠금 안에서 시작이 확정된 뒤에만(R4-02 — 다른 실행의 로그를 지우지 않음).
        - 맡은 번호는 '예약'으로 두고, 자식이 정상 종료한 뒤에만 큐에서 뺀다(R4-01). 실패하면 예약만 풀려 큐에 남는다.
        시작하면 True."""
        import uuid
        import settings.settings as s
        from core.utils import logger
        from services.ws_manager import ws_manager

        with self._launch_lock:
            with self._state_lock:
                pending = self._unreserved_locked()
                generation = self._restore_generation
            if not pending:
                return False
            try:
                from services import crawl_control
                crawl_control._check_crawl_allowed()
            except Exception as exc:
                logger.LoggerFactory.logbot.info(f"[crawl] 대기 큐 {len(pending)}건 보류: {exc}")
                return False

            is_frozen = getattr(sys, 'frozen', False)
            cmd = [sys.executable, "--mode", "crawl"] if is_frozen else [sys.executable, "-u", "start.py"]
            queue_file = os.path.join(s.datapath, f'pending_queue_{uuid.uuid4().hex}.txt')
            cmd.extend(["--queue", queue_file])
            log_file = os.path.join(s.datapath, 'logs', 'current_crawl.log')

            def prepare():  # start_crawl 잠금 안: 시작이 확정된 뒤에만 파일을 만든다(실패하면 아래 except 가 파일을 지운다)
                with open(queue_file, 'w', encoding='utf-8') as f:
                    f.write('\n'.join(str(r) for r in pending))
                rotate_crawl_log(log_file)
                with open(log_file, 'w', encoding='utf-8') as f:
                    f.write(f"=== [대기 큐 자동 시작] 신고번호 {len(pending)}건 ===\n")
                    f.write('\n'.join(f"  - {r}" for r in pending) + '\n')
                self._reserved.update(pending)  # 같은 잠금(_state_lock) 안

            work_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
            try:
                started = self.start_crawl(cmd, cwd=work_dir, log_file=log_file, prepare=prepare,
                                           restore_generation=generation)
            except CrawlBlockedByRestore as exc:
                logger.LoggerFactory.logbot.info(f"[crawl] 대기 큐 {len(pending)}건 보류: {exc}")
                started = False
            except Exception:
                # prepare 나 Popen 이 실패했다 — 예약을 풀고 큐에 남긴다. 번호가 담긴 임시 파일도 지운다(R5-04)
                with self._state_lock:
                    self._reserved.difference_update(pending)
                self._remove_run_files(queue_file)
                raise
            if not started:
                with self._state_lock:
                    self._reserved.difference_update(pending)
                self._remove_run_files(queue_file)
                return False
            proc = self.get_process()

        def _after():
            # 무슨 일이 있어도(보고 손상·완료 훅 예외) 예약을 풀고 실행 파일을 지운다(감사 R6-05).
            left = list(pending)
            try:
                try:
                    if proc:
                        proc.wait()
                except Exception:
                    pass
                done = self._read_queue_report(queue_file)
                # 끝낸 번호는 먼저 빼고, 남은 번호의 예약은 완료 훅 뒤에 푼다 — 실패한 번호로 곧바로 다시 돌지 않게.
                finished_now = [r for r in pending if r in done]
                if finished_now:
                    self._settle_pending(finished_now, done)
                left = [r for r in pending if r not in done]
                self.run_after_crawl(proc, log_file)
            finally:
                if left:
                    self._settle_pending(left, set())
                    self._schedule_retry()  # 남은 번호는 늘어나는 간격으로 다시(R5-02)
                else:
                    with self._state_lock:
                        self._retry_delay = self.RETRY_FIRST_SECONDS
                self._remove_run_files(queue_file)

        # 감시 스레드를 먼저 붙인다 — 알림이 실패해도 예약이 풀리게(R5-03). 스레드를 못 띄우면 예약을 풀고 알린다.
        try:
            threading.Thread(target=_after, daemon=True, name="crawl-pending-after").start()
        except Exception:
            with self._state_lock:
                self._reserved.difference_update(pending)
            raise
        try:
            ws_manager.broadcast_from_thread("crawl_started", {
                "source": "pending_queue",
                "count": len(pending),
                "crawl_mode": s.crawl_mode,
                "crawl_type": s.crawl_type,
            })
        except Exception as exc:
            logger.LoggerFactory.logbot.warning(f"[crawl] 대기 큐 시작 알림 실패: {type(exc).__name__}")
        return True

    @staticmethod
    def _remove_run_files(queue_file: str) -> None:
        from services import crawl_queue_report

        crawl_queue_report.remove_files(queue_file)

crawl_manager = CrawlManager()
