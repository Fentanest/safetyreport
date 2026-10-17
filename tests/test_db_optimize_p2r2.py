"""Sol 재검수 P2-R2 5건 회귀 — 실경로·실DB, skip·완화 없음.

각 시험은 수정 전 코드에서 실패한다.
"""
import json
import os
import threading
import time
import unittest
from unittest import mock

from sqlalchemy import select

import settings.settings as settings
from core.database import models
from core.database import write_barrier
from core.database.engine import get_engine
from core.utils import logger
from services import db_optimize
from services.community_store import CommunityStore

from tests.test_db_optimize import (
    OptimizeBase,
    _envelope,
    _insert_raw,
    _report_ids,
)


def _entry(name="queue.txt"):
    return {"crawl_mode": "full", "queue_filename": name,
            "header": "=== 테스트 전체 ===", "broadcast_source": "web_start"}


class SharedBarrierTests(unittest.TestCase):
    def test_exclusive_waits_for_shared_and_vice_versa(self):
        entered = threading.Event()
        release = threading.Event()

        def holder():
            with write_barrier.shared():
                entered.set()
                self.assertGreater(write_barrier.shared_holders(), 0)
                release.wait(timeout=10)

        th = threading.Thread(target=holder, daemon=True)
        th.start()
        self.assertTrue(entered.wait(timeout=10))
        # 공유 구간이 열려 있으면 exclusive는 드레인을 기다리다 거절된다.
        with self.assertRaises(write_barrier.BarrierTimeout):
            with write_barrier.exclusive(drain_seconds=0.05):
                pass
        release.set()
        th.join(timeout=10)
        self.assertFalse(th.is_alive())
        # 공유 구간이 닫히면 exclusive가 들어간다.
        with write_barrier.exclusive(drain_seconds=2.0):
            gen_before = write_barrier.generation()
        self.assertEqual(write_barrier.generation(), gen_before + 1)

    def test_shared_blocks_while_exclusive(self):
        errors = []

        def try_shared():
            try:
                with write_barrier.shared(wait_seconds=0.05):
                    pass
            except write_barrier.BarrierTimeout:
                errors.append("timeout")
            except Exception as exc:  # noqa: BLE001
                errors.append(type(exc).__name__)

        with write_barrier.exclusive(drain_seconds=2.0):
            th = threading.Thread(target=try_shared, daemon=True)
            th.start()
            th.join(timeout=10)
            self.assertFalse(th.is_alive())
        self.assertEqual(errors, ["timeout"])


class OptimizeSharedTests(OptimizeBase):
    def test_per_report_runs_inside_shared(self):
        ids = _report_ids(self.engine)
        for rid in ids:
            _insert_raw(self.engine, rid, _envelope(rid))
        seen_shared = []
        real_save = db_optimize._save_reparsed

        def hooked(engine, rec, report_id, **kwargs):
            # 저장 시점에는 공유 구간 안에 있어야 한다 — 다른 스레드의 exclusive가 바로 끼지 못한다.
            seen_shared.append(write_barrier.shared_holders())
            errors = []

            def try_exclusive():
                try:
                    with write_barrier.exclusive(drain_seconds=0.05):
                        pass
                except write_barrier.BarrierTimeout:
                    errors.append("timeout")
                except Exception as exc:  # noqa: BLE001
                    errors.append(type(exc).__name__)

            th = threading.Thread(target=try_exclusive, daemon=True)
            th.start()
            th.join(timeout=10)
            self.assertFalse(th.is_alive())
            self.assertEqual(errors, ["timeout"])
            return real_save(engine, rec, report_id, **kwargs)

        with mock.patch.object(db_optimize, "_save_reparsed", side_effect=hooked):
            result = self._run(**self._no_network())
        self.assertTrue(result["finished"])
        self.assertTrue(seen_shared)
        self.assertTrue(all(n > 0 for n in seen_shared))

    def test_restore_during_run_stops_and_writes_nothing_new(self):
        ids = _report_ids(self.engine)
        for rid in ids:
            _insert_raw(self.engine, rid, _envelope(rid))
        calls = []
        real_save = db_optimize._save_reparsed

        def hooked(engine, rec, report_id, **kwargs):
            calls.append(report_id)
            out = real_save(engine, rec, report_id, **kwargs)
            if len(calls) == 1:
                CommunityStore.open().rotate_dataset("restore_test_p2r2")
            return out

        with mock.patch.object(db_optimize, "_save_reparsed", side_effect=hooked):
            result = self._run(**self._no_network())
        self.assertEqual(len(calls), 1)
        self.assertFalse(result["finished"])
        meta = db_optimize.read_meta(self.engine)
        if meta is not None:
            self.assertFalse(meta.get("done"))
            self.assertNotEqual(meta.get("cursor"), ids[-1])

    def test_fetched_result_not_saved_after_restore(self):
        ids = _report_ids(self.engine)
        # 원본 없음 → 네트워크 재조회 경로
        saved = []
        real_save = db_optimize._save_reparsed

        def hooked(engine, rec, report_id, **kwargs):
            saved.append(report_id)
            return real_save(engine, rec, report_id, **kwargs)

        def fetch(report_id):
            # 공유 구간 밖(네트워크)에서 복원이 끼면 저장은 새 DB에 하지 않는다.
            CommunityStore.open().rotate_dataset("restore_fetch_test")
            return _envelope(report_id)

        with mock.patch.object(db_optimize, "_save_reparsed", side_effect=hooked):
            result = self._run(session_factory=lambda: object(), fetch_fn=fetch)
        self.assertFalse(result["finished"])
        self.assertEqual(saved, [])
        meta = db_optimize.read_meta(self.engine)
        if meta is not None:
            self.assertFalse(meta.get("done", False))


class RestoreMaybeStartTests(OptimizeBase):
    def _patch_restore_path(self):
        p1 = mock.patch("core.storage.exchange._integrity_check", return_value=None)
        p2 = mock.patch("core.storage.exchange.ensure_restore_allowed", return_value=None)
        p3 = mock.patch("core.storage.exchange.refuse_other_version", return_value=None)
        p4 = mock.patch("services.account_data.refuse_foreign_owner", return_value=None)
        p5 = mock.patch("core.storage.exchange._current_kakao_id", return_value="x")
        for p in (p1, p2, p3, p4, p5):
            p.start()
            self.addCleanup(p.stop)

    def test_restore_success_calls_maybe_start(self):
        from core.storage import exchange

        self._patch_restore_path()
        with mock.patch.object(exchange, "_restore_exclusive", return_value=("b", 1)) as rex, \
                mock.patch("services.db_optimize.maybe_start") as ms:
            exchange.restore("/tmp/fixture.db", "server")
        self.assertTrue(rex.called)
        self.assertTrue(ms.called)

    def test_restore_failure_calls_maybe_start(self):
        from core.storage import exchange

        self._patch_restore_path()
        with mock.patch.object(exchange, "_restore_exclusive", side_effect=RuntimeError("boom")), \
                mock.patch("services.db_optimize.maybe_start") as ms:
            with self.assertRaises(RuntimeError):
                exchange.restore("/tmp/fixture.db", "server")
        self.assertTrue(ms.called)


class CrawlHookTests(OptimizeBase):
    def _fake_proc(self):
        proc = mock.Mock()
        proc.wait.return_value = 0
        proc.poll.return_value = 0
        proc.args = ["start.py"]
        return proc

    def test_run_after_crawl_starts_deferred_full(self):
        from services import crawl_control
        from services.crawl_manager import crawl_manager

        db_optimize.defer_full_crawl(self.engine, _entry())
        started = []
        proc = self._fake_proc()
        log_file = os.path.join(settings.datapath, "logs", "hook_test.log")
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        with open(log_file, "w", encoding="utf-8") as fh:
            fh.write("x\n")
        with mock.patch.object(crawl_control, "start_crawl",
                               side_effect=lambda **k: started.append(k) or log_file), \
                mock.patch.object(crawl_manager, "launch_pending_crawl", return_value=False), \
                mock.patch("services.crawl_manager.CrawlManager._read_queue_report", return_value=set()), \
                mock.patch("services.ws_manager.ws_manager.broadcast_from_thread"), \
                mock.patch("services.community_uploader.wake"):
            crawl_manager.run_after_crawl(proc, log_file)
        self.assertEqual(len(started), 1)
        self.assertEqual(db_optimize.peek_deferred_fulls(self.engine), [])

    def test_run_after_crawl_keeps_deferred_while_optimize_running(self):
        from services import crawl_control
        from services.crawl_manager import crawl_manager

        db_optimize.defer_full_crawl(self.engine, _entry())
        with db_optimize._lock:
            db_optimize._state.update({"state": "running", "stage": "running"})
        try:
            started = []
            proc = self._fake_proc()
            log_file = os.path.join(settings.datapath, "logs", "hook_test2.log")
            os.makedirs(os.path.dirname(log_file), exist_ok=True)
            with open(log_file, "w", encoding="utf-8") as fh:
                fh.write("x\n")
            with mock.patch.object(crawl_control, "start_crawl",
                                   side_effect=lambda **k: started.append(k) or log_file), \
                    mock.patch.object(crawl_manager, "launch_pending_crawl", return_value=False), \
                    mock.patch("services.crawl_manager.CrawlManager._read_queue_report", return_value=set()), \
                    mock.patch("services.ws_manager.ws_manager.broadcast_from_thread"), \
                    mock.patch("services.community_uploader.wake"):
                crawl_manager.run_after_crawl(proc, log_file)
            self.assertEqual(started, [])
            self.assertEqual(len(db_optimize.peek_deferred_fulls(self.engine)), 1)
        finally:
            with db_optimize._lock:
                db_optimize._state.update({"state": "idle", "stage": ""})


class DeferredStartedTests(OptimizeBase):
    def test_started_prevents_double_start_and_recovers_failure(self):
        from services import crawl_control

        db_optimize.defer_full_crawl(self.engine, _entry())
        # 시작 성공 뒤 보류 삭제 실패로 남아도 다음에는 다시 시작하지 않고 정리한다.
        started = []
        real_clear = db_optimize._clear_deferred_fulls
        with mock.patch.object(crawl_control, "start_crawl",
                               side_effect=lambda **k: started.append(k) or "log"), \
                mock.patch.object(db_optimize, "_clear_deferred_fulls",
                                   side_effect=lambda e: None):
            db_optimize._after_finish(self.engine)
        self.assertEqual(len(started), 1)
        # 삭제가 안 됐으므로 보류·started가 남는다.
        self.assertEqual(len(db_optimize.peek_deferred_fulls(self.engine)), 1)
        self.assertIsNotNone(db_optimize._read_deferred_started(self.engine))
        # 다음 기동·훅에서는 다시 시작하지 않고 정리만 한다.
        with mock.patch.object(crawl_control, "start_crawl",
                               side_effect=lambda **k: started.append(k) or "log"):
            db_optimize._after_finish(self.engine)
        self.assertEqual(len(started), 1)
        self.assertEqual(db_optimize.peek_deferred_fulls(self.engine), [])
        self.assertIsNone(db_optimize._read_deferred_started(self.engine))

    def test_start_failure_reverts_started(self):
        from services import crawl_control

        db_optimize.defer_full_crawl(self.engine, _entry())
        with mock.patch.object(crawl_control, "start_crawl", side_effect=RuntimeError("boom")):
            db_optimize._after_finish(self.engine)
        # 시작 실패 시 started를 되돌리고 보류는 둔다.
        self.assertEqual(len(db_optimize.peek_deferred_fulls(self.engine)), 1)
        self.assertIsNone(db_optimize._read_deferred_started(self.engine))

    def test_started_written_before_start(self):
        from services import crawl_control

        db_optimize.defer_full_crawl(self.engine, _entry())
        seen = {}

        def _start(**kwargs):
            seen["started"] = db_optimize._read_deferred_started(self.engine)
            return "log"

        with mock.patch.object(crawl_control, "start_crawl", side_effect=_start):
            db_optimize._after_finish(self.engine)
        self.assertIsNotNone(seen.get("started"))
        self.assertIn("run", seen["started"])
        self.assertIn("at", seen["started"])


if __name__ == "__main__":
    unittest.main()
