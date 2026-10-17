"""복원·최적화 상호 배제(pause_for_restore)와 보류 크롤 단순화 회귀.

새 설계(공유 구간·started 표시 없음)를 고정한다. 구 설계 코드에서는
`pause_for_restore` 등이 없어 실패한다.
"""
import os
import threading
import time
import unittest
from unittest import mock

from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

import settings.settings as settings
from core.database import write_barrier
from services import db_optimize

from tests.test_db_optimize import (
    OptimizeBase,
    _envelope,
    _insert_raw,
    _report_ids,
)


def _entry(name="queue.txt"):
    return {"crawl_mode": "full", "queue_filename": name,
            "header": "=== 테스트 전체 ===", "broadcast_source": "web_start"}


def _resume_quietly():
    resume = getattr(db_optimize, "resume_after_restore", None)
    if callable(resume):
        try:
            resume()
        except Exception:
            pass


class PauseBase(OptimizeBase):
    def tearDown(self):
        _resume_quietly()
        super().tearDown()
        _resume_quietly()


class PauseStopsOptimizeTests(PauseBase):
    def _start_bg(self, **run_kwargs):
        """실제 기동 경로로 백그라운드 최적화를 시작한다(일시정지가 아는 스레드)."""
        run_kwargs.setdefault("is_crawling", lambda: False)
        run_kwargs.setdefault("rebuild_pending", lambda: False)
        run_kwargs.setdefault("interval", 0)
        run_kwargs.setdefault("wait_seconds", 0)
        run_kwargs.setdefault("login_retry_seconds", 0.01)
        run_kwargs.setdefault("sleep", lambda s: None)
        thread = db_optimize.maybe_start(self.engine, background=True, **run_kwargs)
        self.assertIsNotNone(thread)
        return thread

    def test_pause_stops_running_optimize_after_current_item(self):
        ids = _report_ids(self.engine)
        for rid in ids:
            _insert_raw(self.engine, rid, _envelope(rid))
        self.assertGreaterEqual(len(ids), 2)
        calls = []
        entered = threading.Event()
        real_save = db_optimize._save_reparsed

        def hooked(engine, rec, report_id, **kwargs):
            calls.append(report_id)
            entered.set()
            time.sleep(0.5)  # 복원 요청이 현재 1건 처리 중에 끼게 한다
            return real_save(engine, rec, report_id, **kwargs)

        def _boom(*args, **kwargs):
            raise AssertionError("네트워크를 쓰면 안 됨")

        with mock.patch.object(db_optimize, "_save_reparsed", side_effect=hooked):
            thread = self._start_bg(session_factory=_boom, fetch_fn=_boom)
            try:
                self.assertTrue(entered.wait(timeout=10))
                db_optimize.pause_for_restore(timeout=10)
                thread.join(timeout=15)
                self.assertFalse(thread.is_alive())
                # 현재 1건만 끝나고 멈췄다 — 나머지는 복원 뒤 새 DB에서 다시 판단한다.
                self.assertEqual(len(calls), 1)
                meta = db_optimize.read_meta(self.engine)
                if meta is not None:
                    self.assertFalse(meta.get("done", False))
            finally:
                db_optimize.stop(timeout=10)
                thread.join(timeout=10)
                _resume_quietly()

    def test_pause_skips_save_of_inflight_fetch(self):
        ids = _report_ids(self.engine)
        self.assertGreaterEqual(len(ids), 1)
        # 원본 없음 → 네트워크 재조회 경로. 저장은 하지 않아야 한다.
        saved = []
        real_save = db_optimize._save_reparsed

        def hooked(engine, rec, report_id, **kwargs):
            saved.append(report_id)
            return real_save(engine, rec, report_id, **kwargs)

        fetch_entered = threading.Event()

        def fetch(report_id):
            fetch_entered.set()
            deadline = time.monotonic() + 10
            while not db_optimize.restore_paused():
                if time.monotonic() > deadline:
                    raise AssertionError("pause가 걸리지 않음")
                time.sleep(0.01)
            return _envelope(report_id)

        with mock.patch.object(db_optimize, "_save_reparsed", side_effect=hooked):
            thread = self._start_bg(session_factory=lambda: object(), fetch_fn=fetch)
            try:
                self.assertTrue(fetch_entered.wait(timeout=10))
                db_optimize.pause_for_restore(timeout=10)
                thread.join(timeout=15)
                self.assertFalse(thread.is_alive())
                self.assertEqual(saved, [])
            finally:
                db_optimize.stop(timeout=10)
                thread.join(timeout=10)
                _resume_quietly()

    def test_pause_timeout_clears_stop_and_thread_continues_to_finish(self):
        ids = _report_ids(self.engine)
        self.assertGreaterEqual(len(ids), 1)
        entered = threading.Event()
        release = threading.Event()

        def fetch(report_id):
            entered.set()
            self.assertTrue(release.wait(timeout=15))
            return _envelope(report_id)

        thread = self._start_bg(session_factory=lambda: object(), fetch_fn=fetch)
        try:
            self.assertTrue(entered.wait(timeout=10))
            # 네트워크 재조회가 끝나지 않으면 복원을 거절하되, 멈춤 요청도 풀어
            # 기존 스레드가 그대로 계속 진행·완료한다(표시도 함께 해제).
            with self.assertRaises(db_optimize.RestorePauseTimeout):
                db_optimize.pause_for_restore(timeout=0.2)
            self.assertFalse(db_optimize.restore_paused())
            self.assertFalse(db_optimize._stop.is_set())
            release.set()
            thread.join(timeout=30)
            self.assertFalse(thread.is_alive())
            meta = db_optimize.read_meta(self.engine)
            self.assertTrue(meta.get("done"))
        finally:
            release.set()
            db_optimize.stop(timeout=10)
            thread.join(timeout=10)
            _resume_quietly()

    def test_stopped_thread_restarts_when_no_pause(self):
        ids = _report_ids(self.engine)
        self.assertGreaterEqual(len(ids), 2)
        proceed = threading.Event()

        def fetch(report_id):
            if not proceed.is_set():
                self.assertTrue(proceed.wait(timeout=15))
            return _envelope(report_id)

        thread = self._start_bg(session_factory=lambda: object(), fetch_fn=fetch)
        try:
            # 멈춤 요청으로 끝난 스레드 — 복원 표시가 없으면 종료 시점에 다시 판단해
            # 작업이 중단된 채 남지 않고 새 스레드가 이어서 끝낸다.
            db_optimize._stop.set()
            proceed.set()
            thread.join(timeout=20)
            self.assertFalse(thread.is_alive())
            deadline = time.monotonic() + 30
            meta = None
            while time.monotonic() < deadline:
                meta = db_optimize.read_meta(self.engine)
                if meta is not None and meta.get("done"):
                    break
                time.sleep(0.2)
            self.assertTrue(meta is not None and meta.get("done"))
        finally:
            proceed.set()
            _resume_quietly()

    def test_maybe_start_ignored_while_paused_then_new_real_thread(self):
        db_optimize.pause_for_restore(timeout=5)
        try:
            self.assertTrue(db_optimize.restore_paused())
            # 복원 진행 중에는 시작하지 않는다 — 옛 스레드를 반환하는 경로도 없다.
            self.assertIsNone(db_optimize.maybe_start(self.engine))
        finally:
            db_optimize.resume_after_restore()
        self.assertFalse(db_optimize.restore_paused())

        def _boom(*args, **kwargs):
            raise AssertionError("네트워크를 쓰면 안 됨")

        thread = db_optimize.maybe_start(
            self.engine, is_crawling=lambda: False, rebuild_pending=lambda: False,
            interval=0, wait_seconds=0, login_retry_seconds=0.01,
            sleep=lambda s: None, session_factory=_boom, fetch_fn=_boom)
        try:
            self.assertIsNotNone(thread)
            self.assertIsInstance(thread, threading.Thread)
            self.assertTrue(thread.is_alive())
        finally:
            db_optimize.stop(timeout=10)
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())


class RestorePausePathTests(PauseBase):
    def _patch_restore_path(self):
        p1 = mock.patch("core.storage.exchange._integrity_check", return_value=None)
        p2 = mock.patch("core.storage.exchange.ensure_restore_allowed", return_value=None)
        p3 = mock.patch("core.storage.exchange.refuse_other_version", return_value=None)
        p4 = mock.patch("services.account_data.refuse_foreign_owner", return_value=None)
        p5 = mock.patch("core.storage.exchange._current_kakao_id", return_value="x")
        for p in (p1, p2, p3, p4, p5):
            p.start()
            self.addCleanup(p.stop)

    def test_restore_pauses_before_barrier_and_resumes(self):
        from core.storage import exchange

        self._patch_restore_path()
        order = []
        token = object()
        with mock.patch.object(db_optimize, "pause_for_restore",
                               side_effect=lambda **k: order.append("pause") or token), \
                mock.patch.object(db_optimize, "resume_after_restore",
                                  side_effect=lambda *a, **k: order.append("resume")), \
                mock.patch.object(exchange, "_restore_exclusive",
                                  side_effect=lambda *a: order.append("exclusive") or ("b", 1)), \
                mock.patch("services.db_optimize.maybe_start",
                           side_effect=lambda *a, **k: order.append("maybe_start")):
            exchange.restore("/tmp/fixture.db", "server")
        self.assertEqual(order, ["pause", "exclusive", "resume", "maybe_start"])

    def test_restore_refused_when_pause_times_out(self):
        from core.storage import exchange

        self._patch_restore_path()
        with mock.patch.object(db_optimize, "pause_for_restore",
                               side_effect=db_optimize.RestorePauseTimeout("최적화 중")), \
                mock.patch.object(exchange, "_restore_exclusive") as rex, \
                mock.patch("services.db_optimize.maybe_start"):
            with self.assertRaises(exchange.RestoreRefused):
                exchange.restore("/tmp/fixture.db", "server")
        self.assertFalse(rex.called)

    def test_second_restore_precheck_failure_keeps_first_pause(self):
        from core.storage import exchange

        self._patch_restore_path()
        token = db_optimize.pause_for_restore(timeout=5)
        try:
            self.assertTrue(db_optimize.restore_paused())
            # 첫 복원 진행 중 두 번째 복원 요청이 사전 검사에서 거절되면 —
            # 첫 복원의 표시를 끄지 않고 최적화를 다시 시작하지도 않는다.
            with mock.patch("core.storage.exchange._integrity_check",
                            side_effect=RuntimeError("손상")), \
                    mock.patch("services.db_optimize.maybe_start") as ms:
                with self.assertRaises(exchange.CorruptDatabaseRefused):
                    exchange.restore("/tmp/fixture.db", "server")
            self.assertTrue(db_optimize.restore_paused())
            self.assertFalse(ms.called)
        finally:
            db_optimize.resume_after_restore(token)
        self.assertFalse(db_optimize.restore_paused())


class DeferredNoStartedTests(PauseBase):
    def test_start_failure_keeps_pending(self):
        from services import crawl_control

        db_optimize.defer_full_crawl(self.engine, _entry())
        with mock.patch.object(crawl_control, "start_crawl",
                               side_effect=RuntimeError("boom")):
            db_optimize._after_finish(self.engine)
        self.assertEqual(len(db_optimize.peek_deferred_fulls(self.engine)), 1)

    def test_shutdown_failure_keeps_pending(self):
        from services import crawl_control

        db_optimize.defer_full_crawl(self.engine, _entry())
        with mock.patch.object(crawl_control, "start_crawl",
                               side_effect=RuntimeError("서버가 종료 중입니다.")):
            db_optimize._after_finish(self.engine)
        self.assertEqual(len(db_optimize.peek_deferred_fulls(self.engine)), 1)

    def test_success_clears_pending(self):
        from services import crawl_control

        db_optimize.defer_full_crawl(self.engine, _entry())
        started = []
        with mock.patch.object(crawl_control, "start_crawl",
                               side_effect=lambda **k: started.append(k) or "log"):
            result = db_optimize._launch_one_deferred(self.engine, _entry())
        self.assertEqual(result, "started")
        self.assertEqual(len(started), 1)
        self.assertEqual(db_optimize.peek_deferred_fulls(self.engine), [])

    def test_clear_failure_only_noted_may_start_once_more(self):
        from services import crawl_control

        db_optimize.defer_full_crawl(self.engine, _entry())
        started = []
        with mock.patch.object(crawl_control, "start_crawl",
                               side_effect=lambda **k: started.append(k) or "log"), \
                mock.patch.object(db_optimize, "_clear_deferred_fulls",
                                   side_effect=lambda e: None):
            result = db_optimize._launch_one_deferred(self.engine, _entry())
        # 지우기 실패는 진단만 — 보류가 남아 다음에 한 번 더 시작될 수 있다.
        self.assertEqual(result, "started")
        self.assertEqual(len(db_optimize.peek_deferred_fulls(self.engine)), 1)
        with mock.patch.object(crawl_control, "start_crawl",
                               side_effect=lambda **k: started.append(k) or "log"):
            db_optimize._launch_one_deferred(self.engine, _entry())
        self.assertEqual(len(started), 2)
        self.assertEqual(db_optimize.peek_deferred_fulls(self.engine), [])


class WriteBarrierDrainTests(unittest.TestCase):
    def test_two_checkouts_no_deadlock_and_drain_refuses_then_succeeds(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                               poolclass=StaticPool)
        write_barrier.attach(engine)
        try:
            holding = threading.Event()
            release = threading.Event()
            second_done = threading.Event()
            errors = []

            def worker():
                try:
                    conn1 = engine.connect()
                    try:
                        conn1.execute(text("SELECT 1"))
                        holding.set()
                        self.assertTrue(release.wait(timeout=10))
                    finally:
                        conn1.close()
                    # 첫 연결을 반납한 뒤 두 번째 checkout — 교착 없이 끝나야 한다.
                    conn2 = engine.connect()
                    try:
                        conn2.execute(text("SELECT 1"))
                    finally:
                        conn2.close()
                    second_done.set()
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)
                    holding.set()
                    second_done.set()

            th = threading.Thread(target=worker, daemon=True)
            th.start()
            self.assertTrue(holding.wait(timeout=10))
            # 연결을 빌린 채로는 복원이 거절된다(기존 동작).
            with self.assertRaises(write_barrier.BarrierTimeout):
                with write_barrier.exclusive(drain_seconds=0.2):
                    pass
            release.set()
            self.assertTrue(second_done.wait(timeout=10))
            th.join(timeout=10)
            self.assertFalse(th.is_alive())
            self.assertEqual(errors, [])
            gen = write_barrier.generation()
            with write_barrier.exclusive(drain_seconds=5.0):
                pass
            self.assertEqual(write_barrier.generation(), gen + 1)
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
