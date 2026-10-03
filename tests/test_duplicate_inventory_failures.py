"""읽기/쓰기 실패 때 중복군과 사용자 판단이 보존되는지 검사한다."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import OperationalError

from core.database import models
from core.utils import logger
from scripts.dev import fixture_server
from services import duplicate_group_service as dgs


SOURCES = (
    models.merge_traffic_table,
    models.merge_parking_table,
    models.merge_other_table,
    models.entry_value_table,
    models.raw_content_table,
)
DERIVED = (
    models.duplicate_group_table,
    models.duplicate_member_table,
    models.duplicate_decision_table,
)


class DuplicateInventoryFailureTests(unittest.TestCase):
    def setUp(self):
        logger.LoggerFactory.create_logger(mode="crawl")
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.engine = create_engine(f"sqlite:///{Path(self.directory.name) / 'data.db'}")
        self.addCleanup(self.engine.dispose)
        fixture_server.seed_engine(self.engine)
        with self.engine.connect() as conn:
            self.group_id = conn.execute(select(models.duplicate_group_table.c.group_id)).scalar_one()
        dgs.update_duplicate_group(
            self.engine, self.group_id, duplicate_status="not_duplicate",
            representative_mode="manual", representative_id="90000012", note="수동 판단\n보존",
        )

    def snapshot(self):
        with self.engine.connect() as conn:
            return {table.name: [tuple(row) for row in conn.execute(select(table).order_by(*table.primary_key.columns))]
                    for table in SOURCES + DERIVED}

    def fail_read(self, table):
        original = dgs.pd.read_sql_query

        def read(query, conn, *args, **kwargs):
            if query.get_final_froms()[0].name == table.name:
                raise OperationalError("SELECT", {}, RuntimeError("source unavailable"))
            return original(query, conn, *args, **kwargs)

        return mock.patch.object(dgs.pd, "read_sql_query", side_effect=read)

    def test_each_required_read_failure_preserves_all_values(self):
        before = self.snapshot()
        for table in SOURCES:
            with self.subTest(source=table.name), self.fail_read(table):
                with self.assertRaises(RuntimeError) as caught:
                    dgs.refresh_duplicate_groups(self.engine, track_changes=True)
                self.assertEqual(caught.exception.source, table.name)
                self.assertIsInstance(caught.exception.__cause__, OperationalError)
            self.assertEqual(self.snapshot(), before)

    def test_empty_reports_still_require_entry_and_raw_reads(self):
        with self.engine.begin() as conn:
            for table in SOURCES[:3]:
                conn.execute(table.delete())
        before = self.snapshot()
        for table in SOURCES[3:]:
            with self.subTest(source=table.name), self.fail_read(table):
                with self.assertRaises(RuntimeError):
                    dgs.refresh_duplicate_groups(self.engine)
            self.assertEqual(self.snapshot(), before)

    def test_actual_empty_inventory_removes_derived_groups_but_keeps_decisions(self):
        with self.engine.begin() as conn:
            for table in SOURCES:
                conn.execute(table.delete())
        decisions = self.snapshot()[models.duplicate_decision_table.name]
        self.assertEqual(dgs.refresh_duplicate_groups(self.engine),
                         {"group_count": 0, "member_count": 0, "changes": []})
        after = self.snapshot()
        self.assertEqual(after[models.duplicate_group_table.name], [])
        self.assertEqual(after[models.duplicate_member_table.name], [])
        self.assertEqual(after[models.duplicate_decision_table.name], decisions)

    def test_missing_source_table_is_not_a_successful_empty_read(self):
        models.raw_content_table.drop(self.engine)
        with self.engine.connect() as conn:
            before = [tuple(r) for r in conn.execute(select(models.duplicate_group_table))]
        with self.assertRaises(RuntimeError):
            dgs.refresh_duplicate_groups(self.engine)
        with self.engine.connect() as conn:
            self.assertEqual([tuple(r) for r in conn.execute(select(models.duplicate_group_table))], before)

    def test_failure_after_deletes_rolls_back_all_values(self):
        before = self.snapshot()

        def fail_insert(conn, cursor, statement, parameters, context, executemany):
            if statement.startswith(f"INSERT INTO {models.duplicate_member_table.name}"):
                raise RuntimeError("injected write failure")

        event.listen(self.engine, "before_cursor_execute", fail_insert)
        try:
            with self.assertRaisesRegex(RuntimeError, "injected write failure"):
                dgs.refresh_duplicate_groups(self.engine)
        finally:
            event.remove(self.engine, "before_cursor_execute", fail_insert)
        self.assertEqual(self.snapshot(), before)
