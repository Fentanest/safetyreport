import threading
import unittest
from unittest import mock
from services.crawl_manager import CrawlManager


class PendingBatchTests(unittest.TestCase):
    def manager(self):
        manager = object.__new__(CrawlManager)
        manager._state_lock = threading.Lock()
        manager._pending_queue = ['existing']
        manager._load_pending_locked = mock.Mock()
        manager._save_pending_locked = mock.Mock()
        return manager

    def test_many_numbers_save_once_in_order_without_duplicates(self):
        manager = self.manager()
        self.assertEqual(manager.append_many_to_pending(['a', 'existing', 'b', 'a']), 3)
        self.assertEqual(manager._pending_queue, ['existing', 'a', 'b'])
        manager._save_pending_locked.assert_called_once()
        manager.append_many_to_pending(['a', 'b'])
        manager._save_pending_locked.assert_called_once()

    def test_save_failure_restores_whole_batch(self):
        manager = self.manager()
        manager._save_pending_locked.side_effect = OSError('fixture disk full')
        with self.assertRaises(RuntimeError):
            manager.append_many_to_pending(['a', 'b'])
        self.assertEqual(manager._pending_queue, ['existing'])
