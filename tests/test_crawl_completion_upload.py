"""모든 크롤링 진입점이 지나는 완료 훅에서 부모 업로더를 깨운다."""
import unittest
from unittest import mock

from services.crawl_manager import CrawlManager


class CrawlCompletionUploadTests(unittest.TestCase):
    def test_completed_crawl_wakes_parent_uploader(self):
        manager = CrawlManager()
        process = mock.Mock(args=[])
        with (
            mock.patch.object(manager, "clear_process"),
            mock.patch.object(manager, "pending_count", return_value=0),
            mock.patch("time.sleep"),
            mock.patch("services.crawl_manager.crawl_state_store.get_and_clear_crawl_done", return_value=None),
            mock.patch("services.crawl_manager.crawl_state_store.peek_crawl_changes", return_value=[]),
            mock.patch("services.crawl_manager.crawl_state_store.save_crawl_done_ext"),
            mock.patch("services.ws_manager.ws_manager.broadcast_from_thread"),
            mock.patch("services.community_uploader.wake") as wake,
        ):
            manager.run_after_crawl(process, "/nonexistent/crawl.log")
        process.wait.assert_called_once()
        wake.assert_called_once()


if __name__ == "__main__":
    unittest.main()
