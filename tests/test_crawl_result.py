"""EO R-07: 크롤 서브프로세스의 명시적 실행 결과(CrawlResult)가 종료 코드·영속 기록·last_sync·완료 마커를 정한다.

목록 HTTP·상세 스트림만 가짜로 두고 실제 start.main → 저장 → 후처리를 돈다.
"""

import os
import tempfile
import unittest
from unittest import mock

import pandas as pd
from sqlalchemy import select

import settings.settings as settings
from core.database import models
from core.database.engine import get_engine
from core.utils import logger
from scripts.dev import fixture_server


class CrawlResultTest(unittest.TestCase):
    def setUp(self):
        logger.LoggerFactory.create_logger(mode="crawl")
        get_engine().dispose()
        for ext in ("", "-wal", "-shm"):
            if os.path.exists(settings.db_path + ext):
                os.remove(settings.db_path + ext)
        fixture_server.seed_engine(get_engine())
        with get_engine().connect() as conn:
            self.ids = [str(r[0]) for r in conn.execute(select(models.title_table.c.ID).order_by(models.title_table.c.ID))][:2]
        from services import crawl_state_store
        crawl_state_store.get_and_clear_crawl_done()

    def _last_sync(self):
        with get_engine().connect() as conn:
            return conn.execute(select(models.sync_meta_table.c.value)
                                .where(models.sync_meta_table.c.key == "last_sync")).scalar()

    def _run(self, *, list_ok=True, stop_after=None, page_range=None):
        import start
        from services import crawl_run_state

        args = {"queue_file": None, "page_range": page_range, "force": False, "rebuild": None, "reset": False}
        titles = [pd.DataFrame({"ID": self.ids, "신고번호": [f"SPP-X-{i}" for i in self.ids], "상태": ["진행"] * 2})]

        def crawl_titles(driver=None, browser_fallback=False, progress=None, **kw):
            progress["list_ok"] = list_ok
            return titles, 1

        def crawl_details(driver=None, report_ids=None, browser_fallback=False):
            for n, rid in enumerate(report_ids):
                if stop_after is not None and n >= stop_after:
                    raise ConnectionError("끊김")
                yield (pd.DataFrame([{"ID": rid, "처리상태": "수용", "처리내용": "R-07", "종결여부": "Y"}]), "traffic",
                       "자동차·교통위반-신호위반")

        run_id = crawl_run_state.create()
        with mock.patch.dict(os.environ, {crawl_run_state.ENV_KEY: run_id}), \
                mock.patch.object(start, "_parse_args", return_value=args), \
                mock.patch.object(start, "_validate_settings"), mock.patch.object(start, "_prepare_database"), \
                mock.patch("core.crawler.direct_login.get_valid_token"), \
                mock.patch.object(start.crawltitle_api, "crawl_titles", side_effect=crawl_titles), \
                mock.patch.object(start.crawldetail_api, "crawl_details", side_effect=crawl_details), \
                mock.patch.object(start.database, "get_pending_detail_ids", return_value=list(self.ids)), \
                mock.patch.object(start.settings, "telegram_enabled", False), \
                mock.patch.object(start.settings, "auto_export_excel", False), \
                mock.patch.object(start.settings, "auto_export_sheet", False):
            code = start.main()
        from services import crawl_state_store
        return code, crawl_run_state.read(run_id), self._last_sync(), crawl_state_store.get_and_clear_crawl_done()

    def test_complete_run_succeeds_and_writes_last_sync(self):
        before = self._last_sync()
        code, state, last_sync, done = self._run()
        self.assertEqual(code, 0)
        self.assertEqual(state["state"], "succeeded")
        self.assertTrue(state["list_complete"] and state["detail_complete"])
        self.assertNotIn("save_errors", state)
        self.assertNotIn("stream_error", state)
        self.assertIsNotNone(last_sync)
        self.assertNotEqual(last_sync, before)
        self.assertEqual(done["outcome"], "succeeded")

    def test_incomplete_list_is_partial_without_last_sync(self):
        before = self._last_sync()
        code, state, last_sync, done = self._run(list_ok=False)
        self.assertEqual(code, 1)
        self.assertEqual(state["state"], "partial")
        self.assertFalse(state["list_complete"])
        self.assertEqual(last_sync, before, "부분 완료는 last_sync 를 바꾸지 않는다")
        self.assertEqual(done["outcome"], "partial")

    def test_interrupted_details_keep_what_was_saved_and_report_the_stream_error(self):
        before = self._last_sync()
        code, state, last_sync, done = self._run(stop_after=1)
        self.assertEqual(code, 1)
        self.assertEqual(state["state"], "partial")
        self.assertFalse(state["detail_complete"])
        self.assertEqual(state["stream_error"], "ConnectionError")
        self.assertEqual(last_sync, before)
        with get_engine().connect() as conn:
            saved = {str(r[0]) for r in conn.execute(select(models.detail_traffic_table.c.ID)
                                                     .where(models.detail_traffic_table.c.처리내용 == "R-07"))}
        self.assertEqual(saved, {self.ids[0]}, "끊기기 전에 받은 1건은 저장된다")

    def test_options_and_result_contract(self):
        import start

        options = start.CrawlOptions.from_args({"queue_file": "q", "page_range": (1, 2), "force": 1, "rebuild": None})
        self.assertEqual((options.queue_file, options.page_range, options.force), ("q", (1, 2), True))
        self.assertIs(start.CrawlOptions.from_args(options), options)
        result = start.CrawlResult(list_complete=True, detail_complete=False, save_errors=2)
        self.assertFalse(result.successful)
        self.assertEqual(result.evidence(), {"list_complete": True, "detail_complete": False, "save_errors": 2})


if __name__ == "__main__":
    unittest.main()
