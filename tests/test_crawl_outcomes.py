import tempfile
import threading
import unittest
from unittest import mock
from services import crawl_run_state as states
from services.crawl_manager import CrawlManager


class CrawlOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patch = mock.patch.object(states.settings, 'datapath', self.temp.name)
        patch.start()
        self.addCleanup(patch.stop)

    def test_exit_zero_requires_matching_success_evidence(self):
        run = states.create()
        self.assertEqual(states.completion(run, 0)['state'], 'unknown')
        states.write(run, 'succeeded', list_complete=True, detail_complete=True)
        self.assertEqual(states.completion(run, 0)['state'], 'succeeded')
        self.assertEqual(states.completion(run, 1)['state'], 'unknown')
        self.assertEqual(states.completion(run, -15)['state'], 'cancelled')
        self.assertEqual(states.completion('f' * 32, 0)['state'], 'unknown')

    def test_partial_and_failure_remain_distinct_from_success(self):
        run = states.create()
        for state in ('partial', 'failed'):
            states.write(run, state)
            self.assertEqual(states.completion(run, 1)['state'], state)

    def test_duplicate_completion_emits_one_terminal_and_retains_reservation_through_markers(self):
        manager = object.__new__(CrawlManager)
        manager._state_lock = threading.Lock()
        manager._post_upload_active = False
        process = mock.Mock(args=[], _safetyreport_run_id=states.create())
        process.wait.return_value = 1
        observed = []
        with mock.patch.object(manager, 'clear_process'), \
             mock.patch.object(manager, 'pending_count', return_value=0), \
             mock.patch('time.sleep'), \
             mock.patch('services.crawl_manager.crawl_state_store.get_and_clear_crawl_done', return_value=None), \
             mock.patch('services.crawl_manager.crawl_state_store.peek_crawl_changes', return_value=[]), \
             mock.patch('services.crawl_manager.crawl_state_store.save_crawl_done_ext',
                        side_effect=lambda *a: observed.append(manager._post_upload_active)), \
             mock.patch('services.ws_manager.ws_manager.broadcast_from_thread') as notify, \
             mock.patch('services.community_uploader.wake'):
            manager.run_after_crawl(process, '/nonexistent/log')
            manager.run_after_crawl(process, '/nonexistent/log')
        self.assertEqual(observed, [True])
        self.assertFalse(manager._post_upload_active)
        notify.assert_called_once()
        self.assertEqual(notify.call_args.args[1]['outcome'], 'failed')
