import threading
import unittest
from unittest import mock
from services.crawl_manager import CrawlManager, RestoreBlocked, CrawlBlockedByRestore


class RatingAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.manager = object.__new__(CrawlManager)
        for key, value in dict(_state_lock=threading.Lock(), _active_process=None,
                _preparing=False, _post_upload_active=False, _restore_hold=False,
                _restore_generation=1, _rating_token=None).items():
            setattr(self.manager, key, value)

    def test_rating_reservation_blocks_another_rating_crawl_and_restore(self):
        token = self.manager.reserve_rating(1)
        with self.assertRaises(RuntimeError):
            self.manager.reserve_rating(1)
        with mock.patch('services.crawl_manager.subprocess.Popen') as spawn:
            self.assertFalse(self.manager.start_crawl([], '.', '/fake/log'))
            spawn.assert_not_called()
        with self.assertRaises(RestoreBlocked):
            with self.manager.hold_for_restore():
                self.fail('restore entered during rating')
        self.manager.release_rating(object())
        self.assertIs(self.manager._rating_token, token)
        self.manager.release_rating(token)
        self.assertIsNone(self.manager._rating_token)

    def test_crawl_restore_and_stale_generation_block_rating(self):
        self.manager._preparing = True
        with self.assertRaises(RuntimeError):
            self.manager.reserve_rating(1)
        self.manager._preparing = False
        self.manager._restore_hold = True
        with self.assertRaises(CrawlBlockedByRestore):
            self.manager.reserve_rating(1)
        self.manager._restore_hold = False
        with self.assertRaises(CrawlBlockedByRestore):
            self.manager.reserve_rating(0)
