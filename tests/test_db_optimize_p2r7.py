"""Sol 7차 지적 3건 회귀 — 실경로·실DB, skip·완화 없음.

1. 보류 배출이 복원 일시정지를 확인한 뒤 잠금을 놓으면, 그 사이 pause가
   토큰을 획득해도 배출이 끝까지 진행해 크롤 시작을 시도한다(높음).
   → 배출은 크롤 시작 직전에 수명 잠금으로 잠깐 다시 확인하고 걸려 있으면
   시작하지 않는다. 순서를 Event로 강제하면 일시정지 중 시작 없음.
2. 두 배출 호출이 같은 보류 항목을 각각 읽고 시작할 수 있다(중간).
   → 전용 배출 잠금 single-flight(이미 진행 중이면 기다리지 않고 건너뜀,
   진행 중인 배출이 끝에서 재확인). 두 배출 동시 → 시작 1회.
3. background=False에서 thread.run()이 수명 잠금 안에서 process_all 전체를
   실행한다. 크롤 시작 잠금을 기다리는 동안 그 잠금을 쥔 호출자가
   restore_paused()로 수명 잠금을 기다리면 교착(중간).
   → background=False도 process_all을 수명 잠금 밖에서 실행(잠금 안 상태 갱신만).
   크롤 잠금 대기 중 restore_paused()가 막히지 않음.

수정 전 실패 확인용(각 시험은 구 코드에서 실패한다).
"""
import threading
import time
import unittest
from unittest import mock

from services import crawl_control, db_optimize
from services.crawl_manager import crawl_manager
from services.community_capture import PARSER_VERSION

from tests.test_db_optimize import OptimizeBase, _report_ids


def _full_entry(name="queue.txt"):
    return {"crawl_mode": "full", "queue_filename": name,
            "header": "=== p2r7 ===", "broadcast_source": "web_start"}


class PauseVsDrainOrderTests(OptimizeBase):
    def tearDown(self):
        try:
            db_optimize.resume_after_restore()
        except Exception:
            pass
        super().tearDown()

    def test_no_start_while_paused_event_ordered(self):
        ids = _report_ids(self.engine)
        db_optimize.write_meta(self.engine, {"target": PARSER_VERSION, "cursor": ids[-1],
                                              "done": True, "total": len(ids), "stats": {}})
        db_optimize.defer_full_crawl(self.engine, _full_entry())
        started: list = []
        drain_checked = threading.Event()
        pause_done = threading.Event()
        real_peek = db_optimize.peek_deferred_fulls
        calls = {"n": 0}

        def hooked_peek(engine):
            out = real_peek(engine)
            if calls["n"] == 0:
                calls["n"] += 1
                drain_checked.set()
                # 배출 게이트(복원 확인)가 끝난 뒤·시작 전 — 여기서 일시정지가 걸린다.
                self.assertTrue(pause_done.wait(timeout=15))
            return out

        def acquire_pause():
            self.assertTrue(drain_checked.wait(timeout=15))
            token = object()
            with db_optimize._life_lock:
                db_optimize._restore_tokens.append(token)
                db_optimize._pause_requested = True
                db_optimize._restore_pause = True
            pause_done.set()
            return token

        pauser = threading.Thread(target=acquire_pause, daemon=True)
        with mock.patch.object(db_optimize, "peek_deferred_fulls", side_effect=hooked_peek), \
                mock.patch.object(crawl_control, "start_crawl",
                                   side_effect=lambda **k: started.append(k) or "log"), \
                mock.patch.object(crawl_manager, "is_crawling", return_value=False):
            pauser.start()
            db_optimize._drain_deferred_if_done(self.engine)
            pauser.join(timeout=15)
        self.assertFalse(pauser.is_alive())
        # 일시정지가 걸린 뒤에는 시작하지 않고 보류를 둔다.
        self.assertEqual(started, [])
        self.assertEqual(len(db_optimize.peek_deferred_fulls(self.engine)), 1)


class ConcurrentDrainTests(OptimizeBase):
    def test_two_drains_start_once(self):
        ids = _report_ids(self.engine)
        db_optimize.write_meta(self.engine, {"target": PARSER_VERSION, "cursor": ids[-1],
                                              "done": True, "total": len(ids), "stats": {}})
        db_optimize.defer_full_crawl(self.engine, _full_entry())
        started: list = []
        real_clear = db_optimize._clear_deferred_fulls

        def delayed_clear(engine):
            # 첫 시작의 지우기를 늦춰 두 배출이 같은 항목을 읽는 창을 강제한다.
            time.sleep(0.5)
            return real_clear(engine)

        def fake_start(**kwargs):
            started.append(kwargs)
            return "log"

        go = threading.Event()

        def drain_one():
            self.assertTrue(go.wait(timeout=15))
            db_optimize._drain_deferred_if_done(self.engine)

        with mock.patch.object(crawl_control, "start_crawl", side_effect=fake_start), \
                mock.patch.object(crawl_manager, "is_crawling", return_value=False), \
                mock.patch.object(db_optimize, "_clear_deferred_fulls", side_effect=delayed_clear):
            workers = [threading.Thread(target=drain_one, daemon=True) for _ in range(2)]
            for worker in workers:
                worker.start()
            go.set()
            for worker in workers:
                worker.join(timeout=15)
        self.assertEqual(len(started), 1)
        self.assertEqual(db_optimize.peek_deferred_fulls(self.engine), [])


class SyncRunLockOrderTests(OptimizeBase):
    def test_restore_paused_not_blocked_while_sync_waits_crawl_lock(self):
        holder_holds = threading.Event()
        release_holder = threading.Event()

        def hold_launch_lock():
            with crawl_control._launch_lock:
                holder_holds.set()
                # 동기 실행의 크롤 잠금 대기가 끝날 때까지 쥐고 있는다.
                self.assertTrue(release_holder.wait(timeout=15))

        def fake_process(engine, **kwargs):
            # 크롤 시작 잠금을 기다리는 척 — 제한 시간 뒤 풀린다.
            acquired = crawl_control._launch_lock.acquire(timeout=5)
            try:
                return {"total": 0, "done": 0, "finished": True, "stats": {}}
            finally:
                if acquired:
                    crawl_control._launch_lock.release()

        durations: list = []
        restored: list = []

        def ask_restore():
            begin = time.monotonic()
            out = db_optimize.restore_paused()
            durations.append(time.monotonic() - begin)
            restored.append(out)

        holder = threading.Thread(target=hold_launch_lock, daemon=True)
        holder.start()
        self.assertTrue(holder_holds.wait(timeout=15))
        try:
            with mock.patch.object(db_optimize, "process_all", side_effect=fake_process):
                runner_done = threading.Event()
                runner_exc: list = []

                def run_sync():
                    try:
                        db_optimize.maybe_start(self.engine, background=False,
                                                is_crawling=lambda: False,
                                                rebuild_pending=lambda: False,
                                                interval=0, wait_seconds=0,
                                                sleep=lambda s: None, stop=self.stop)
                    except Exception as exc:  # noqa: BLE001
                        runner_exc.append(exc)
                    finally:
                        runner_done.set()

                runner = threading.Thread(target=run_sync, daemon=True)
                runner.start()
                # 동기 실행이 크롤 잠금 대기에 들어갈 시간을 준다.
                time.sleep(0.5)
                asker = threading.Thread(target=ask_restore, daemon=True)
                asker.start()
                asker.join(timeout=15)
                self.assertFalse(asker.is_alive())
                runner.join(timeout=15)
                self.assertFalse(runner.is_alive())
                self.assertEqual(runner_exc, [])
        finally:
            release_holder.set()
            holder.join(timeout=15)
        self.assertEqual(len(restored), 1)
        # 수명 잠금을 쥔 채 크롤 잠금을 기다리면 복원 조회가 덩달아 막힌다(구 코드).
        # 고친 뒤에는 대기 중에도 바로 돌아온다.
        self.assertLess(durations[0], 2.0)


if __name__ == "__main__":
    unittest.main()
