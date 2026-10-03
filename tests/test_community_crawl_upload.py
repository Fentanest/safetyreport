"""이전 미전송 공유 자료가 새 크롤링보다 앞서는지 확인한다."""
import os
import tempfile
import unittest
from unittest import mock

from services.community_crawl_upload import PendingUploadError, flush, log_background_progress


class CrawlUploadBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.log = os.path.join(self.temp.name, "crawl.log")

    def tearDown(self):
        self.temp.cleanup()

    def log_text(self):
        with open(self.log, encoding="utf-8") as source:
            return source.read()

    def test_drains_multiple_runs_before_crawl(self):
        outcomes = [
            {"result": "more_pending", "counts": {"sent": 25}, "error_code": None},
            {"result": "sent", "counts": {"sent": 2}, "error_code": None},
        ]
        statuses = [2, 0]
        with mock.patch("services.community_uploader.request_upload", side_effect=outcomes) as upload, \
             mock.patch("services.community_uploader.crawl_pending_count", side_effect=statuses):
            flush(self.log, before_crawl=True)
        self.assertEqual(upload.call_count, 2)
        self.assertIn("대기 중인 공유 자료가 없습니다", self.log_text())

    def test_offline_keeps_crawl_stopped_and_logs_reason(self):
        with mock.patch("services.community_uploader.request_upload", return_value={"result": "cooldown", "error_code": "offline"}), \
             mock.patch("services.community_uploader.crawl_pending_count", return_value=3):
            with self.assertRaisesRegex(PendingUploadError, "3건"):
                flush(self.log, before_crawl=True)
        self.assertIn("offline", self.log_text())

    def test_waits_for_orphan_upload_lease_then_retries(self):
        outcomes = [{"result": "busy_other_run", "error_code": None},
                    {"result": "sent", "error_code": None}]
        statuses = [1, 0]
        with mock.patch("services.community_uploader.request_upload", side_effect=outcomes) as upload, \
             mock.patch("services.community_uploader.crawl_pending_count", side_effect=statuses), \
             mock.patch("time.sleep"):
            flush(self.log, before_crawl=True)
        self.assertEqual(upload.call_count, 2)
        self.assertIn("저장소 잠금", self.log_text())

    def test_after_crawl_logs_failed_upload_without_changing_crawl_result(self):
        with mock.patch("services.community_uploader.request_upload", return_value={"result": "cooldown", "error_code": "offline"}), \
             mock.patch("services.community_uploader.crawl_pending_count", return_value=3):
            flush(self.log, before_crawl=False)
        self.assertIn("3건 업로드 대기 중", self.log_text())

    def test_crawl_subprocess_is_not_started_when_preupload_fails(self):
        from services.crawl_control import _log_header
        from services.crawl_manager import CrawlManager

        manager = CrawlManager()
        with mock.patch.object(manager, "_active_process", None), \
             mock.patch.object(manager, "_post_upload_active", False), \
             mock.patch.object(manager, "_preparing", False), \
             mock.patch("services.crawl_control.get_current_crawl_log_path", return_value=self.log), \
             mock.patch("services.crawl_control.rotate_crawl_log"), \
             mock.patch("services.community_crawl_upload.flush", side_effect=PendingUploadError("offline")) as upload, \
             mock.patch("services.crawl_manager.block_if_fixture"), \
             mock.patch("services.crawl_manager.subprocess.Popen") as popen:
            _, prepare = _log_header("크롤링")
            with self.assertRaises(PendingUploadError):
                manager.start_crawl(["crawler"], cwd=self.temp.name, log_file=self.log, prepare=prepare)
            upload.assert_called_once_with(self.log, before_crawl=True)
            popen.assert_not_called()
            self.assertFalse(manager.is_crawling())

    def test_background_upload_progress_reaches_active_crawl_log(self):
        from services.crawl_manager import CrawlManager

        with open(self.log, "w", encoding="utf-8"):
            pass
        manager = CrawlManager()
        with mock.patch.object(manager, "_active_process", object()), \
             mock.patch("services.crawl_log_service.get_current_crawl_log_path", return_value=self.log):
            log_background_progress("진행: 확인 1건")
        self.assertIn("[Supabase] 진행: 확인 1건", self.log_text())

    def test_web_start_explains_pending_upload_instead_of_reporting_busy(self):
        from web.routers.crawl import start_crawl

        with mock.patch("services.community_gate.crawl_block", return_value=None), \
             mock.patch("services.crawl_control.start_crawl", side_effect=PendingUploadError("이전 공유 자료 3건이 업로드되지 않았습니다")):
            response = start_crawl(queue_list="", crawl_mode="full")
        self.assertEqual(response.status_code, 503)
        self.assertIn("3건", response.body.decode("utf-8"))
