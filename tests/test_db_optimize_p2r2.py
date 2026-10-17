"""복원 재판단·크롤 훅 회귀 — 실경로·실DB, skip·완화 없음.

공유 구간·started 표시 시험은 2026-10-17 단순화 설계(복원·최적화 상호 배제,
보류는 시작 성공 뒤 삭제)로 사라져 함께 삭제했다. 대체 시험은
tests/test_db_optimize_pause.py.
"""
import os
import unittest
from unittest import mock

import settings.settings as settings
from services import db_optimize

from tests.test_db_optimize import OptimizeBase


def _entry(name="queue.txt"):
    return {"crawl_mode": "full", "queue_filename": name,
            "header": "=== 테스트 전체 ===", "broadcast_source": "web_start"}


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


if __name__ == "__main__":
    unittest.main()
