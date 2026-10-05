"""현재 스키마지만 기관코드 복사 전 빌드가 만든 merge의 기동/복원 회귀."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sqlalchemy import create_engine, select, update

from core.database import database, models
from core.storage import reports_repo
from core.utils import logger


class StartupMergeRepairTests(unittest.TestCase):
    def setUp(self):
        logger.LoggerFactory.create_logger(mode="crawl")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.engine = create_engine(f"sqlite:///{Path(self.tmp.name) / 'data.db'}")
        self.addCleanup(self.engine.dispose)
        database.upgrade_schema(self.engine)
        with self.engine.begin() as conn:
            for category, detail in reports_repo.DETAIL_TABLES.items():
                conn.execute(models.title_table.insert().values(
                    ID=category, 신고번호=f"fixture-{category}", 신고일="2020-01-01"))
                conn.execute(detail.insert().values(
                    ID=category, 처리기관코드="fixture-code", 처리내용="원본",
                    첨부사진="https://example.invalid/photo.jpg"))
            conn.execute(models.report_override_table.insert().values(
                ID="traffic", column_name="처리내용", value="사용자 수정", updated_at=1))
            conn.execute(models.watchlist_table.insert().values(신고번호="fixture-traffic"))
        database.merge_final(self.engine)

    def stale(self):
        with self.engine.begin() as conn:
            for merge in reports_repo.MERGE_TABLES.values():
                conn.execute(update(merge).values(처리기관코드=""))

    def test_startup_repairs_old_merge_once_and_preserves_projection_rules(self):
        self.stale()
        with mock.patch.object(database, "merge_final", wraps=database.merge_final) as rebuild:
            self.assertTrue(database.repair_stale_merge(self.engine))
            self.assertFalse(database.repair_stale_merge(self.engine))
            rebuild.assert_called_once_with(self.engine)
        with self.engine.connect() as conn:
            for merge in reports_repo.MERGE_TABLES.values():
                row = conn.execute(select(merge)).mappings().one()
                self.assertEqual(row["처리기관코드"], "fixture-code")
                self.assertEqual(row["첨부사진"], reports_repo.EXPIRED_ATTACHMENT)
            row = conn.execute(select(models.merge_traffic_table)).mappings().one()
            self.assertEqual(row["처리내용"], "사용자 수정")
            self.assertEqual(row["감시목록"], "Y")
            self.assertEqual(conn.execute(select(models.detail_traffic_table.c.처리내용)).scalar_one(), "원본")

    def test_healthy_database_does_not_rebuild(self):
        with mock.patch.object(database, "merge_final") as rebuild:
            self.assertFalse(database.repair_stale_merge(self.engine))
            rebuild.assert_not_called()

    def test_empty_database_does_not_rebuild(self):
        with self.engine.begin() as conn:
            for table in (*reports_repo.MERGE_TABLES.values(), *reports_repo.DETAIL_TABLES.values()):
                conn.execute(table.delete())
        with mock.patch.object(database, "merge_final") as rebuild:
            self.assertFalse(database.repair_stale_merge(self.engine))
            rebuild.assert_not_called()

    def test_missing_extra_or_different_ids_are_detected(self):
        merge = models.merge_traffic_table
        for mutation in (merge.delete(), merge.insert().values(ID="extra"),
                         update(merge).values(ID="different")):
            with self.subTest(mutation=str(mutation)):
                with self.engine.begin() as conn:
                    conn.execute(mutation)
                self.assertTrue(database.repair_stale_merge(self.engine))
                self.assertFalse(database.repair_stale_merge(self.engine))

    def test_code_value_mismatch_is_detected_even_with_equal_counts(self):
        with self.engine.begin() as conn:
            conn.execute(update(models.merge_traffic_table).values(처리기관코드="wrong-code"))
        self.assertTrue(database.repair_stale_merge(self.engine))
        self.assertFalse(database.repair_stale_merge(self.engine))

    def test_code_overrides_and_orphan_details_do_not_trigger_repeated_rebuild(self):
        for value in ("", None, "user-code"):
            with self.subTest(value=value):
                with self.engine.begin() as conn:
                    conn.execute(models.report_override_table.delete())
                    conn.execute(models.report_override_table.insert().values(
                        ID="traffic", column_name="처리기관코드", value=value, updated_at=1))
                database.merge_final(self.engine)
                with mock.patch.object(database, "merge_final") as rebuild:
                    self.assertFalse(database.repair_stale_merge(self.engine))
                    rebuild.assert_not_called()
        with self.engine.begin() as conn:
            conn.execute(models.detail_traffic_table.insert().values(ID="no-title", 처리기관코드="orphan"))
        self.assertFalse(database.repair_stale_merge(self.engine))

    def test_check_and_rebuild_errors_warn_without_stopping_startup(self):
        self.stale()
        for target in ("core.storage.reports_repo.merge_drift", "core.database.database.merge_final"):
            with self.subTest(target=target), mock.patch(target, side_effect=RuntimeError("private value")):
                with mock.patch.object(logger.LoggerFactory.logbot, "warning") as warning:
                    self.assertFalse(database.repair_stale_merge(self.engine))
                    self.assertNotIn("private value", warning.call_args.args[0])
                    warning.assert_called_once()
                with self.assertRaises(RuntimeError):
                    database.repair_stale_merge(self.engine, raise_on_error=True)
