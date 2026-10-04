import unittest
from unittest.mock import patch
from services import sunwi_fetcher as sf


class Clock:
    now = 0.0
    cancelled = False
    def is_set(self): return self.cancelled
    def wait(self, delay):
        self.now += delay
        return self.cancelled


class SunwiRequestBudgetTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.time_patch = patch.object(sf.time, 'monotonic', lambda: self.clock.now)
        self.time_patch.start()
        self.addCleanup(self.time_patch.stop)

    def session(self, callback):
        clock = self.clock
        class Session:
            calls = []
            closed = False
            def get(self, url, **kwargs):
                self.calls.append(kwargs)
                return callback(self, kwargs)
            def close(self): self.closed = True
        return Session()

    def test_outer_region_retry_does_not_reset_attempt_budget(self):
        def fail(session, kwargs): raise OSError('synthetic failure')
        session = self.session(fail)
        regions = {'서울': {'sido': '11', 'sigungu': {'강서': '11500'}}}
        with patch.object(sf, 'build_session', return_value=session), patch.object(sf, 'REGIONS', regions):
            result = sf.collect_statistics(logger_fn=lambda _: None, retry_failed_passes=3, stop_event=self.clock)
        self.assertEqual(len(session.calls), 6)
        self.assertEqual(len(result['failed']), 1)
        self.assertEqual(result['all_rows'], [])
        self.assertTrue(session.closed)

    def test_total_deadline_stops_requests_to_unvisited_regions_and_retry(self):
        def fail(session, kwargs):
            self.clock.now += sum(kwargs['timeout'])
            raise TimeoutError('synthetic deadline')
        session = self.session(fail)
        regions = {'서울': {'sido': '11', 'sigungu': {'가': '1', '나': '2'}}}
        with patch.object(sf, 'build_session', return_value=session), patch.object(sf, 'REGIONS', regions):
            result = sf.collect_statistics(logger_fn=lambda _: None, total_timeout=3,
                                           stop_event=self.clock)
        self.assertEqual(len(session.calls), 1)
        self.assertLessEqual(sum(session.calls[0]['timeout']), 3)
        self.assertEqual(len(result['failed']), 2)
        self.assertTrue(session.closed)

    def test_late_success_is_not_published(self):
        class Response:
            def raise_for_status(self): pass
            def json(inner):
                self.clock.now += 2
                return {'result': [{'NM': '인도', 'CNT': 7}]}
        session = self.session(lambda session, kwargs: Response())
        with self.assertRaises(TimeoutError):
            sf.fetch_stats(session, '11', '1', timeout=1, stop_event=self.clock)
        self.assertEqual(len(session.calls), 1)

    def test_cancel_during_backoff_interrupts_and_closes_session(self):
        def fail(session, kwargs):
            self.clock.cancelled = True
            raise OSError('synthetic failure')
        session = self.session(fail)
        with patch.object(sf, 'build_session', return_value=session):
            with self.assertRaises(InterruptedError):
                sf.collect_statistics(logger_fn=lambda _: None, stop_event=self.clock)
        self.assertEqual(len(session.calls), 1)
        self.assertTrue(session.closed)

    def test_success_keeps_all_category_rows_and_counts(self):
        class Response:
            def raise_for_status(self): pass
            def json(self): return {'result': [{'NM': '인도', 'CNT': '7'}]}
        session = self.session(lambda session, kwargs: Response())
        regions = {'서울': {'sido': '11', 'sigungu': {'강서': '11500'}}}
        with patch.object(sf, 'build_session', return_value=session), patch.object(sf, 'REGIONS', regions):
            result = sf.collect_statistics(logger_fn=lambda _: None, stop_event=self.clock)
        self.assertEqual(len(result['all_rows']), len(sf.TARGET_CATEGORIES))
        self.assertEqual(next(row['건수'] for row in result['all_rows'] if row['소분류'] == '인도'), 7)
        self.assertEqual(result['failed'], [])
        self.assertTrue(session.closed)


class SunwiWorkerLifetimeTests(unittest.TestCase):
    def test_join_timeout_preserves_live_worker_and_blocks_replacement(self):
        import threading
        from services import sunwi_service as service
        started, release = threading.Event(), threading.Event()
        def run():
            started.set()
            release.wait(5)
        worker = threading.Thread(target=run)
        worker.start()
        self.assertTrue(started.wait(1))
        try:
            with patch.object(service, '_worker_thread', worker), patch.object(service, '_log_warning'), \
                 patch.object(service.threading, 'Thread') as factory:
                self.assertFalse(service.stop_background_refresh(timeout=0))
                self.assertIs(service._worker_thread, worker)
                service.start_background_refresh()
                factory.assert_not_called()
                release.set()
                self.assertTrue(service.stop_background_refresh(timeout=1))
                self.assertIsNone(service._worker_thread)
        finally:
            release.set()
            worker.join(1)
