"""Sol 6차 지적 2건 회귀 — 실경로·실DB, skip·완화 없음.

1. 잠금 순서 교착: 기동 경로(`maybe_start`, done+보류 전체 크롤)는 수명 잠금을 놓은
   뒤에 보류 크롤을 시작해야 한다. 수동 크롤이 크롤 시작 잠금을 쥔 채 최적화 상태를
   조회해도 두 스레드가 타임아웃 없이 끝난다(수정 전에는 시작 잠금을 10초 넘게
   기다려 `started` 가 비어 실패한다).
2. 작업자 종료 재시작 판단은 실행 결과(`finished`)가 아니라 메타 기준 `needs_run`
   으로만 한다. 마지막 신고 뒤 일시정지로 done 기록을 건너뛴 뒤 시간 초과가 나도
   다시 띄워 done 을 기록한다(수정 전에는 2회차 실행 없이 done 미기록으로 끝난다).
"""
import threading
import time
import unittest
from unittest import mock

from services import crawl_control, db_optimize
from services.crawl_manager import crawl_manager
from services.community_capture import PARSER_VERSION

from tests.test_db_optimize import (
    OptimizeBase,
    _report_ids,
)


def _full_entry(name="queue.txt"):
    return {"crawl_mode": "full", "queue_filename": name,
            "header": "=== p2r6 ===", "broadcast_source": "web_start"}


class LockOrderNoDeadlockTests(OptimizeBase):
    def test_startup_and_manual_crawl_finish_without_deadlock(self):
        ids = _report_ids(self.engine)
        db_optimize.write_meta(self.engine, {"target": PARSER_VERSION, "cursor": ids[-1],
                                             "done": True, "total": len(ids), "stats": {}})
        db_optimize.defer_full_crawl(self.engine, _full_entry())

        b_holds = threading.Event()
        a_inside = threading.Event()
        b_result: list = []
        started: list = []
        real_needs_run = db_optimize.needs_run

        def gated_needs_run(engine):
            out = real_needs_run(engine)
            a_inside.set()  # 수명 잠금을 쥔 채 호출됨
            return out

        def fake_start_crawl(**kwargs):
            # 실제 start_crawl처럼 크롤 시작 잠금을 잡는다. 수명 잠금을 쥔 채
            # 기다리면 수동 크롤과 교착되므로 최대 10초만 기다린다(깨지면 실패 기록).
            if not crawl_control._launch_lock.acquire(timeout=10):
                started.append("deadlocked")
                return "kept"
            try:
                started.append(1)
                return "log"
            finally:
                crawl_control._launch_lock.release()

        def manual_crawl():
            # 수동 크롤: 크롤 시작 잠금을 쥔 채 최적화 상태를 조회한다.
            with crawl_control._launch_lock:
                b_holds.set()
                self.assertTrue(a_inside.wait(timeout=15))
                b_result.append(db_optimize.is_running())

        with mock.patch.object(db_optimize, "needs_run", side_effect=gated_needs_run), \
                mock.patch.object(crawl_control, "start_crawl", side_effect=fake_start_crawl), \
                mock.patch.object(crawl_manager, "is_crawling", return_value=False):
            worker = threading.Thread(target=manual_crawl, daemon=True)
            worker.start()
            self.assertTrue(b_holds.wait(timeout=15))
            ret = db_optimize.maybe_start(self.engine)
            worker.join(timeout=15)

        self.assertFalse(worker.is_alive())
        self.assertEqual(b_result, [False])
        self.assertEqual(started, [1])
        self.assertIsNone(ret)
        self.assertEqual(db_optimize.peek_deferred_fulls(self.engine), [])


class PauseTimeoutRerunTests(OptimizeBase):
    def tearDown(self):
        try:
            db_optimize.resume_after_restore()
        except Exception:
            pass
        super().tearDown()

    def test_pause_timeout_reruns_to_record_done(self):
        self.assertIsNone(db_optimize.read_meta(self.engine))
        calls: list = []
        proceed = threading.Event()
        timeouts: list = []

        def fake_process(engine, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                self.assertTrue(proceed.wait(timeout=20))
                # done 메타를 쓰지 않고 finished 로 끝난다 — 마지막 신고 직후
                # 일시정지로 done 기록을 건너뛴 상태와 같다.
                return {"total": 1, "done": 1, "finished": True, "stats": {}}
            db_optimize.write_meta(engine, {"target": PARSER_VERSION, "cursor": "x",
                                            "done": True, "total": 1, "stats": {}})
            return {"total": 1, "done": 1, "finished": True, "stats": {}}

        def do_pause():
            try:
                db_optimize.pause_for_restore(timeout=0.5)
            except db_optimize.RestorePauseTimeout as exc:
                timeouts.append(exc)

        with mock.patch.object(db_optimize, "process_all", side_effect=fake_process):
            thread = db_optimize.maybe_start(self.engine, background=True)
            self.assertIsNotNone(thread)
            pauser = threading.Thread(target=do_pause, daemon=True)
            pauser.start()
            deadline = time.monotonic() + 10
            while not db_optimize.restore_paused() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(db_optimize.restore_paused())
            # 작업자가 join 시한을 넘기면 시간 초과 정리가 토큰·멈춤을 풀고 재판단한다.
            time.sleep(1.0)
            pauser.join(timeout=10)
            self.assertEqual(len(timeouts), 1)
            proceed.set()
            deadline = time.monotonic() + 20
            meta = None
            while time.monotonic() < deadline:
                meta = db_optimize.read_meta(self.engine)
                if meta is not None and meta.get("done"):
                    break
                time.sleep(0.2)
            # finished=True 로 끝난 1회차 뒤에도 needs_run 기준으로 다시 떠서 done 기록.
            self.assertEqual(len(calls), 2)
            self.assertTrue(meta is not None and meta.get("done"))


if __name__ == "__main__":
    unittest.main()
