import threading
import unittest
from unittest.mock import patch


class BackgroundWorkerLifetimeTests(unittest.TestCase):
    def _live(self):
        started, release = threading.Event(), threading.Event()
        def work():
            started.set()
            release.wait(5)
        thread = threading.Thread(target=work)
        thread.start()
        self.assertTrue(started.wait(1))
        def finish():
            release.set()
            thread.join(1)
        self.addCleanup(finish)
        return thread, release

    def test_uploader_timeout_retains_worker_and_prevents_replacement(self):
        from services import community_uploader as service
        thread, release = self._live()
        with patch.object(service, '_bg_thread', thread), patch.object(service.threading, 'Thread') as factory:
            self.assertFalse(service.stop_background(0))
            self.assertIs(service._bg_thread, thread)
            service.start_background()
            factory.assert_not_called()
            release.set()
            self.assertTrue(service.stop_background(1))
            self.assertIsNone(service._bg_thread)

    def test_login_timeout_retains_worker_and_prevents_replacement(self):
        from core.crawler import direct_login as service
        thread, release = self._live()
        with patch.object(service, '_keepalive_thread', thread), patch.object(service.threading, 'Thread') as factory:
            self.assertFalse(service.stop_keepalive(0))
            self.assertIs(service._keepalive_thread, thread)
            self.assertFalse(service.start_keepalive())
            factory.assert_not_called()
            release.set()
            self.assertTrue(service.stop_keepalive(1))
            self.assertIsNone(service._keepalive_thread)

    def test_auth_cancelled_workers_remain_owned_and_share_one_join_budget(self):
        from services import community_auth_service as module
        service = module.CommunityAuthService.__new__(module.CommunityAuthService)
        service._workers_lock = threading.RLock()
        service._shutdown = threading.Event()
        service._retiring_workers = []
        clock = [0.0]
        class Worker:
            timeouts = []
            def is_alive(self): return True
            def join(self, timeout):
                self.timeouts.append(timeout)
                clock[0] += timeout
        workers = [Worker(), Worker(), Worker()]
        stops = [threading.Event() for _ in workers]
        service._workers = {str(i): (worker, stops[i]) for i, worker in enumerate(workers)}
        service._stop_worker('2')
        self.assertNotIn('2', service._workers)
        self.assertEqual(service._retiring_workers, [(workers[2], stops[2])])
        with patch.object(module.time, 'monotonic', lambda: clock[0]):
            self.assertFalse(service.shutdown(timeout=5))
        self.assertEqual(sum(Worker.timeouts), 5)
        self.assertTrue(all(stop.is_set() for stop in stops))
        self.assertEqual(len(service._workers), 2)
        self.assertEqual(len(service._retiring_workers), 1)
        with patch.object(module.threading, 'Thread') as factory:
            service._start_worker('new')
            factory.assert_not_called()
