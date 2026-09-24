"""변경 기록과 기기별 읽은 위치 (저장 계층 재설계 R5, 결정 D-5, S-18)."""
import os
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine

from core.database import database
from core.storage import change_log
from core.utils import logger


class ChangeLogTests(unittest.TestCase):
    def setUp(self):
        logger.LoggerFactory.create_logger(mode="crawl")
        self._dir = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self._dir.name) / 'data.db'}")
        database.upgrade_schema(self.engine, maintenance=False)

    def tearDown(self):
        self.engine.dispose()
        self._dir.cleanup()

    def batch(self, *ids):
        change_log.append_batch(self.engine, [{"ID": i, "notification_kind": "report", "신고번호": f"SPP-{i}"} for i in ids])

    def test_every_device_gets_the_same_changes(self):
        self.batch("a", "b")
        self.assertEqual([c["ID"] for c in change_log.read_for_device(self.engine, "phone-1")], ["a", "b"])
        self.assertEqual([c["ID"] for c in change_log.read_for_device(self.engine, "tablet-2")], ["a", "b"])  # 예전엔 첫 기기만
        self.assertEqual(change_log.read_for_device(self.engine, "phone-1"), [])  # 같은 기기 두 번째는 없음

    def test_a_new_device_starts_from_the_latest_batch(self):
        import time
        self.batch("old")
        time.sleep(0.01)
        self.batch("new1", "new2")
        self.assertEqual([c["ID"] for c in change_log.read_for_device(self.engine, "fresh")], ["new1", "new2"])

    def test_old_app_without_device_id_behaves_like_before(self):
        self.batch("a")
        self.assertEqual([c["ID"] for c in change_log.read_for_device(self.engine, None)], ["a"])
        self.assertEqual(change_log.read_for_device(self.engine, ""), [])  # 같은 legacy 위치
        self.batch("b")
        self.assertEqual([c["ID"] for c in change_log.read_for_device(self.engine, None)], ["b"])

    def test_save_crawl_changes_also_records_the_batch_and_writes_atomically(self):
        import settings.settings as app_settings
        from scripts.dev import fixture_server
        from services import crawl_state_store

        fixture_server.seed_engine(self.engine)
        crawl_state_store.save_crawl_changes(self.engine, [{"id": "90000001", "change_type": "변경"}])
        self.assertEqual([c["ID"] for c in change_log.read_for_device(self.engine, "phone")], ["90000001"])
        leftovers = [p for p in os.listdir(app_settings.datapath) if ".tmp" in p]
        self.assertEqual(leftovers, [])
        crawl_state_store.clear_crawl_changes()

    def test_take_json_keeps_a_file_it_cannot_parse(self):
        from services import crawl_state_store

        path = Path(self._dir.name) / "broken.json"
        path.write_text("{not json", encoding="utf-8")
        self.assertEqual(crawl_state_store._take_json(str(path), []), [])
        self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
