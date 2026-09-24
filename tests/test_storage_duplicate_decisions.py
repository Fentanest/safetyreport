"""중복군 사용자 판단 보존 (저장 계층 재설계 R2c, 결정 D-6)."""
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine, select

from core.database import database, models
from core.utils import logger
from scripts.dev import fixture_server
from services import duplicate_group_service as dgs


class DuplicateDecisionTests(unittest.TestCase):
    def setUp(self):
        logger.LoggerFactory.create_logger(mode="crawl")
        self._dir = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self._dir.name) / 'data.db'}")
        fixture_server.seed_engine(self.engine)
        with self.engine.connect() as conn:
            self.group_id = conn.execute(select(models.duplicate_group_table.c.group_id)).scalar()
        self.assertIsNotNone(self.group_id)

    def tearDown(self):
        self.engine.dispose()
        self._dir.cleanup()

    def group(self):
        with self.engine.connect() as conn:
            return dict(conn.execute(select(models.duplicate_group_table)).mappings().one())

    def decide(self):
        self.assertTrue(dgs.update_duplicate_group(
            self.engine, self.group_id, duplicate_status="not_duplicate", representative_mode="manual",
            representative_id="90000012", note="서로 다른 건"))

    def test_decision_survives_group_rebuild_from_scratch(self):
        self.decide()
        with self.engine.begin() as conn:  # 그룹이 통째로 사라진 상황(초기화 크롤링 등)
            conn.execute(models.duplicate_member_table.delete())
            conn.execute(models.duplicate_group_table.delete())
        dgs.refresh_duplicate_groups(self.engine)
        g = self.group()
        self.assertEqual((g["status"], g["representative_mode"], g["representative_id"], g["note"]),
                         ("not_duplicate", "manual", "90000012", "서로 다른 건"))

    def test_reset_crawl_keeps_decisions(self):
        import start

        self.decide()
        start._prepare_database(self.engine, reset=True)
        with self.engine.connect() as conn:
            self.assertEqual(conn.execute(select(models.duplicate_decision_table.c.status)).scalar(), "not_duplicate")

    def test_bulk_status_is_recorded_as_a_decision(self):
        dgs.bulk_update_duplicate_status(self.engine, [self.group_id], "review_required")
        with self.engine.connect() as conn:
            self.assertEqual(conn.execute(select(models.duplicate_decision_table.c.status)).scalar(), "review_required")

    def test_member_first_grouped_time_is_kept(self):
        with self.engine.connect() as conn:
            before = {r.report_id: r.created_at for r in conn.execute(select(models.duplicate_member_table))}
        dgs.refresh_duplicate_groups(self.engine)
        with self.engine.connect() as conn:
            after = {r.report_id: r.created_at for r in conn.execute(select(models.duplicate_member_table))}
        self.assertEqual(before, after)

    def test_migration_copies_existing_group_decisions(self):
        with self.engine.begin() as conn:
            conn.execute(models.duplicate_decision_table.delete())
            conn.execute(models.duplicate_group_table.update().values(status="not_duplicate", note="옛 판단"))
            database._migration_3_duplicate_decisions(conn)
        with self.engine.connect() as conn:
            row = conn.execute(select(models.duplicate_decision_table)).mappings().one()
        self.assertEqual((row["status"], row["note"]), ("not_duplicate", "옛 판단"))


if __name__ == "__main__":
    unittest.main()
