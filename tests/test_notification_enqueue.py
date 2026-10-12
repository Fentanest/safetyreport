"""Notification ACK loss and process restarts, with synthetic data and no crawler."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
from services.crawl_manager import CrawlManager
from services import crawl_control


class NotificationEnqueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'pending.json'
        self.manager = self.new_manager()

    def new_manager(self):
        manager = object.__new__(CrawlManager)
        manager._state_lock = threading.Lock()
        manager._pending_queue = []
        manager._reserved = set()
        manager._notification_receipts = {}
        manager._notification_again = set()
        manager._restore_hold = manager._shutting_down = False
        manager._pending_path = lambda: str(self.path)
        return manager

    def test_lost_ack_replay_after_completion_and_restart_does_not_requeue(self):
        m = self.manager
        m.append_notification('SPP-2610-1234567', 'event-1', 'device-a')
        m._settle_pending(['SPP-2610-1234567'], {'SPP-2610-1234567'})
        restarted = self.new_manager()
        restarted.append_notification('SPP-2610-1234567', 'event-1', 'device-a')
        self.assertEqual([], restarted.pending_items())
        restarted.append_notification('SPP-2610-1234567', 'event-2', 'device-a')
        self.assertEqual(['SPP-2610-1234567'], restarted.pending_items())

    def test_failed_atomic_save_preserves_original_and_is_retryable(self):
        with mock.patch.object(self.manager, '_save_pending_locked', side_effect=OSError):
            with self.assertRaises(RuntimeError):
                self.manager.append_notification('SPP-2610-1234567', 'event', 'device')
        self.assertEqual([], self.manager.pending_items())
        self.assertEqual({}, self.manager._notification_receipts)
        self.manager.append_notification('SPP-2610-1234567', 'event', 'device')
        self.assertEqual(['SPP-2610-1234567'], self.new_manager().pending_items())

    def test_updated_notification_during_crawl_survives_old_completion(self):
        m = self.manager
        n = 'SPP-2610-1234567'
        m.append_notification(n, 'first', 'device')
        m._reserved.add(n)
        m.append_notification(n, 'second', 'device')
        self.assertEqual([n], m._settle_pending([n], {n}))
        self.assertEqual([n], m.pending_items())
        self.assertEqual([], m._settle_pending([n], {n}))
        self.assertEqual([], m.pending_items())

    def test_legacy_list_survives_upgrade_and_id_conflict_is_refused(self):
        self.path.write_text(json.dumps(['SPP-2610-7654321']))
        self.manager.append_notification('SPP-2610-1234567', 'event', 'device')
        self.assertEqual(2, len(self.new_manager().pending_items()))
        with self.assertRaises(ValueError):
            self.manager.append_notification('SPP-2610-7654321', 'event', 'device')

    def test_concurrent_duplicate_admission_creates_one_obligation(self):
        threads = [threading.Thread(target=self.manager.append_notification,
                   args=('SPP-2610-1234567', 'event', 'device')) for _ in range(20)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(['SPP-2610-1234567'], self.manager.pending_items())
        self.assertEqual(1, len(self.manager._notification_receipts))

    def test_corrupt_persistent_queue_is_not_overwritten_or_acknowledged(self):
        self.path.write_text('{broken')
        for _ in range(2):
            with self.assertRaises(RuntimeError):
                self.manager.append_notification('SPP-2610-1234567', 'event', 'device')
        self.assertEqual('{broken', self.path.read_text())

    def test_declined_background_launch_schedules_retry_without_new_notification(self):
        m = self.manager
        m._request_again = False
        m._request_worker_active = True
        with mock.patch.object(m, 'launch_pending_crawl', return_value=False), \
             mock.patch.object(m, 'is_crawling', return_value=False), \
             mock.patch.object(m, 'pending_count', return_value=1), \
             mock.patch.object(m, '_schedule_retry') as retry:
            m._request_worker()
        retry.assert_called_once()
        self.assertFalse(m._request_worker_active)

    def test_route_control_checks_gate_before_durable_admission(self):
        with mock.patch.object(crawl_control, '_check_crawl_allowed', side_effect=RuntimeError), \
             mock.patch.object(crawl_control.crawl_manager, 'append_notification') as append:
            with self.assertRaises(RuntimeError):
                crawl_control.enqueue_notification('SPP-2610-1234567',
                    'inbox.enqueue.native.notification.00000000-0000-0000-0000-000000000000', 'fixture')
            append.assert_not_called()


class NotificationRouteTests(unittest.TestCase):
    def setUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from web.routers import api_route
        self.route = api_route
        app = FastAPI()
        app.include_router(api_route.router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.payload = {'report_number': 'SPP-2610-1234567',
                        'request_id': 'inbox.enqueue.native.notification.00000000-0000-0000-0000-000000000000'}
        for target, value in [('validate_api_key', True), ('get_api_key_name', 'fixture')]:
            patch = mock.patch.object(api_route.database, target, return_value=value)
            patch.start()
            self.addCleanup(patch.stop)
        patch = mock.patch.object(api_route.ws_manager, 'track_api_request')
        patch.start()
        self.addCleanup(patch.stop)

    def test_missing_key_is_refused_before_enqueue(self):
        with mock.patch.object(crawl_control, 'enqueue_notification') as enqueue:
            response = self.client.post('/api/v1/crawl/notification', json=self.payload)
        self.assertEqual(401, response.status_code)
        enqueue.assert_not_called()

    def test_ack_echoes_id_and_passes_authenticated_device_scope(self):
        ack = {'status': 'accepted', 'request_id': self.payload['request_id']}
        with mock.patch.object(self.route, '_raise_if_community_blocked'), \
             mock.patch.object(crawl_control, 'enqueue_notification', return_value=ack) as enqueue:
            response = self.client.post('/api/v1/crawl/notification', json=self.payload, headers={'X-API-Key': 'fixture'})
        self.assertEqual(200, response.status_code)
        self.assertEqual(ack, response.json())
        enqueue.assert_called_once_with(self.payload['report_number'], self.payload['request_id'], 'fixture')

    def test_gate_denial_never_acknowledges(self):
        from fastapi import HTTPException
        with mock.patch.object(self.route, '_raise_if_community_blocked', side_effect=HTTPException(403, 'COMMUNITY_ONBOARDING_REQUIRED')), \
             mock.patch.object(crawl_control, 'enqueue_notification') as enqueue:
            response = self.client.post('/api/v1/crawl/notification', json=self.payload, headers={'X-API-Key': 'fixture'})
        self.assertEqual(403, response.status_code)
        enqueue.assert_not_called()

    def test_storage_failure_is_retryable_service_unavailable(self):
        with mock.patch.object(self.route, '_raise_if_community_blocked'), \
             mock.patch.object(crawl_control, 'enqueue_notification', side_effect=RuntimeError('fixture disk error')):
            response = self.client.post('/api/v1/crawl/notification', json=self.payload, headers={'X-API-Key': 'fixture'})
        self.assertEqual(503, response.status_code)
