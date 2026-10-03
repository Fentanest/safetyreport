import threading
import unittest
from unittest import mock
from services import community_rebuild as rebuild
from tests.test_community_rebuild import _setUp_env


class RebuildWorkerLifetimeTests(unittest.TestCase):
    def setUp(self):
        self.env = _setUp_env(self)

    def test_accept_before_backup_and_pause_fences_late_launch(self):
        entered, release = threading.Event(), threading.Event()
        def backup(run_id):
            entered.set()
            if not release.wait(5):
                raise TimeoutError('test barrier')
            return None, 'ok'
        with mock.patch.object(rebuild, '_backup_personal_db', side_effect=backup):
            result = rebuild.start('fixture', background=True)
            self.assertTrue(result['run_id'])
            self.assertTrue(entered.wait(5))
            run_id = result['run_id']
            paused = rebuild.pause('fixture')
            self.assertEqual(paused['state'], 'paused')
            release.set()
            rebuild._workers[run_id].join(5)
        self.assertFalse(rebuild._workers[run_id].is_alive())
        self.assertEqual(rebuild.status()['state'], 'paused')
        self.assertEqual(self.env.launches, [])
        self.assertIsNone(rebuild._store().connect().execute("SELECT * FROM leases WHERE name='rebuild'").fetchone())

    def test_old_attempt_cannot_write_items_or_finish_resumed_job(self):
        run_id = rebuild.start('fixture')['run_id']
        rebuild.bind_attempt(run_id, 'first')
        rebuild.register_list(run_id, ['101'])
        rebuild.bind_attempt(run_id, 'second')
        with mock.patch.dict('os.environ', {'SAFETYREPORT_CRAWL_RUN_ID': 'first'}):
            with self.assertRaisesRegex(RuntimeError, 'superseded'):
                rebuild.record_item(run_id, '101', 'fetched')
        rebuild.on_crawl_finished(run_id, attempt='first')
        self.assertEqual(rebuild._get_job(run_id)['state'], 'running')
        self.assertEqual(rebuild._counts(run_id)['pending'], 1)

    def test_old_or_paused_child_errors_cannot_overwrite_current_job(self):
        run_id = rebuild.start('fixture')['run_id']
        rebuild.bind_attempt(run_id, 'new')
        with mock.patch.dict('os.environ', {'SAFETYREPORT_CRAWL_RUN_ID': 'old'}):
            for method in (rebuild.mark_login_failed, rebuild.mark_paused_auth, rebuild.mark_store_unavailable):
                with self.assertRaisesRegex(RuntimeError, 'superseded'): method(run_id)
                self.assertEqual(rebuild._get_job(run_id)['state'], 'running')
        rebuild.pause('fixture')
        for method in (rebuild.mark_login_failed, rebuild.mark_paused_auth, rebuild.mark_store_unavailable):
            method(run_id)
        self.assertEqual(rebuild._get_job(run_id)['state'], 'paused')

    def test_pause_does_not_confirm_stopped_while_child_is_alive(self):
        from services.crawl_manager import crawl_manager
        run_id = rebuild.start('fixture')['run_id']
        process = mock.Mock(_rebuild_run_id=run_id)
        process.poll.return_value = None
        with mock.patch.object(crawl_manager, 'get_process', return_value=process), mock.patch.object(crawl_manager, 'stop_crawl') as stop:
            result = rebuild.pause('fixture')
        stop.assert_called_once()
        self.assertEqual(result['state'], 'running')
        self.assertEqual(rebuild._get_job(run_id)['last_error'], 'pause_stop_pending')


if __name__ == '__main__':
    unittest.main()
