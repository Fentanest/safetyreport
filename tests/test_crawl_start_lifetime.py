"""외부 프로세스 없이 시작·감시·알림 실패 순서를 검증한다."""
import unittest
from unittest import mock

from services import crawl_control as control
import start


class CrawlStartLifetimeTests(unittest.TestCase):
    def test_watcher_is_ready_before_spawn_and_binds_even_if_notify_fails(self):
        events = []
        process = object()
        with mock.patch.object(control, '_prepare_after_crawl_hook',
                               side_effect=lambda *a: events.append('watch') or
                               (lambda p: events.append(('bind', p)))), \
             mock.patch.object(control.crawl_manager, 'start_crawl',
                               side_effect=lambda *a, **k: events.append('spawn') or True), \
             mock.patch.object(control.crawl_manager, 'get_process', return_value=process), \
             mock.patch.object(control.ws_manager, 'broadcast_from_thread', side_effect=RuntimeError):
            self.assertTrue(control._launch_watched([], '/fake/log', None))
            control._notify_started({'source': 'test'})
        self.assertEqual(events, ['watch', 'spawn', ('bind', process)])

    def test_failed_spawn_releases_prepared_watcher(self):
        bind = mock.Mock()
        with mock.patch.object(control, '_prepare_after_crawl_hook', return_value=bind), \
             mock.patch.object(control.crawl_manager, 'start_crawl', side_effect=OSError):
            with self.assertRaises(OSError):
                control._launch_watched([], '/fake/log', None)
        bind.assert_called_once_with(None)

    def test_failed_watcher_registration_cannot_spawn(self):
        with mock.patch.object(control, '_prepare_after_crawl_hook', side_effect=RuntimeError), \
             mock.patch.object(control.crawl_manager, 'start_crawl') as spawn:
            with self.assertRaises(RuntimeError):
                control._launch_watched([], '/fake/log', None)
        spawn.assert_not_called()

    def test_fatal_login_does_not_publish_success_or_last_sync(self):
        with mock.patch.object(start, '_parse_args', return_value={'reset': False}), \
             mock.patch.object(start, '_validate_settings'), \
             mock.patch.object(start, 'get_engine'), \
             mock.patch.object(start, '_prepare_database'), \
             mock.patch('core.crawler.direct_login.get_valid_token', side_effect=RuntimeError), \
             mock.patch.object(start.driv, 'create_driver', side_effect=RuntimeError), \
             mock.patch.object(start, '_process_and_save_results') as publish:
            self.assertEqual(start.main(), 1)
        publish.assert_not_called()

    def test_result_processing_failure_is_a_nonzero_exit(self):
        with mock.patch.object(start, '_parse_args', return_value={'reset': False}), \
             mock.patch.object(start, '_validate_settings'), \
             mock.patch.object(start, 'get_engine'), \
             mock.patch.object(start, '_prepare_database'), \
             mock.patch('core.crawler.direct_login.get_valid_token'), \
             mock.patch.object(start, '_run_crawling_process', return_value=start.CrawlResult(list_complete=True, detail_complete=True)), \
             mock.patch.object(start, '_process_and_save_results', side_effect=OSError):
            self.assertEqual(start.main(), 1)
