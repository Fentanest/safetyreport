"""현재 스키마지만 기관코드 복사 전 빌드가 만든 화면용 표(mysafety_report_effective)의 기동/복원 회귀(스키마 6)."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sqlalchemy import create_engine, select, update

from core.database import database, models
from core.storage import reports_repo
from core.utils import logger

# 스키마 6: 카테고리별 표가 아니라 category 열 값이다. 세 분류를 한 표에 넣는다.
CATEGORIES = ("traffic", "parking", "other")


class StartupMergeRepairTests(unittest.TestCase):
    def setUp(self):
        logger.LoggerFactory.create_logger(mode="crawl")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.engine = create_engine(f"sqlite:///{Path(self.tmp.name) / 'data.db'}")
        self.addCleanup(self.engine.dispose)
        database.upgrade_schema(self.engine)
        with self.engine.begin() as conn:
            for category in CATEGORIES:
                conn.execute(models.report_table.insert().values(
                    ID=category, 신고번호=f"fixture-{category}", 신고일="2020-01-01", category=category,
                    처리기관코드="fixture-code", 처리내용="원본", 첨부사진="https://example.invalid/photo.jpg"))
            conn.execute(models.report_override_table.insert().values(
                ID="traffic", column_name="처리내용", value="사용자 수정", updated_at=1))
            conn.execute(models.watchlist_table.insert().values(신고번호="fixture-traffic"))
        database.rebuild_effective(self.engine)

    def stale(self):
        with self.engine.begin() as conn:
            conn.execute(update(models.report_effective_table).values(처리기관코드=""))

    def _row(self, conn, record_id):
        return conn.execute(select(models.report_effective_table)
                            .where(models.report_effective_table.c.ID == record_id)).mappings().one()

    def test_startup_repairs_old_effective_once_and_preserves_projection_rules(self):
        self.stale()
        with mock.patch.object(database, "rebuild_effective", wraps=database.rebuild_effective) as rebuild:
            self.assertTrue(database.repair_stale_effective(self.engine))
            self.assertFalse(database.repair_stale_effective(self.engine))
            rebuild.assert_called_once_with(self.engine)
        with self.engine.connect() as conn:
            for category in CATEGORIES:
                row = self._row(conn, category)
                self.assertEqual(row["처리기관코드"], "fixture-code")
                self.assertEqual(row["첨부사진"], reports_repo.EXPIRED_ATTACHMENT)
            row = self._row(conn, "traffic")
            self.assertEqual(row["처리내용"], "사용자 수정")
            self.assertEqual(row["감시목록"], "Y")
            self.assertEqual(conn.execute(select(models.report_table.c.처리내용)
                                         .where(models.report_table.c.ID == "traffic")).scalar_one(), "원본")

    def test_healthy_database_does_not_rebuild(self):
        with mock.patch.object(database, "rebuild_effective") as rebuild:
            self.assertFalse(database.repair_stale_effective(self.engine))
            rebuild.assert_not_called()

    def test_empty_database_does_not_rebuild(self):
        with self.engine.begin() as conn:
            conn.execute(models.report_effective_table.delete())
            conn.execute(models.report_table.delete())
        with mock.patch.object(database, "rebuild_effective") as rebuild:
            self.assertFalse(database.repair_stale_effective(self.engine))
            rebuild.assert_not_called()

    def test_missing_extra_or_different_ids_are_detected(self):
        effective = models.report_effective_table
        for mutation in (effective.delete().where(effective.c.ID == "traffic"),
                         effective.insert().values(ID="extra", category="traffic"),
                         update(effective).where(effective.c.ID == "traffic").values(ID="different")):
            with self.subTest(mutation=str(mutation)):
                with self.engine.begin() as conn:
                    conn.execute(mutation)
                self.assertTrue(database.repair_stale_effective(self.engine))
                self.assertFalse(database.repair_stale_effective(self.engine))

    def test_code_value_mismatch_is_detected_even_with_equal_counts(self):
        with self.engine.begin() as conn:
            conn.execute(update(models.report_effective_table)
                        .where(models.report_effective_table.c.ID == "traffic").values(처리기관코드="wrong-code"))
        self.assertTrue(database.repair_stale_effective(self.engine))
        self.assertFalse(database.repair_stale_effective(self.engine))

    def test_code_overrides_and_orphan_details_do_not_trigger_repeated_rebuild(self):
        for value in ("", None, "user-code"):
            with self.subTest(value=value):
                with self.engine.begin() as conn:
                    conn.execute(models.report_override_table.delete())
                    conn.execute(models.report_override_table.insert().values(
                        ID="traffic", column_name="처리기관코드", value=value, updated_at=1))
                database.rebuild_effective(self.engine)
                with mock.patch.object(database, "rebuild_effective") as rebuild:
                    self.assertFalse(database.repair_stale_effective(self.engine))
                    rebuild.assert_not_called()
        # 목록만 본 신고(category NULL)는 화면용 표 대조에서 빠진다 — 반복 재생성을 일으키지 않는다.
        with self.engine.begin() as conn:
            conn.execute(models.report_table.insert().values(ID="no-title", 신고번호="orphan-number", 처리기관코드="orphan"))
        self.assertFalse(database.repair_stale_effective(self.engine))

    def test_check_and_rebuild_errors_warn_without_stopping_startup(self):
        self.stale()
        for target in ("core.storage.reports_repo.effective_drift", "core.database.database.rebuild_effective"):
            with self.subTest(target=target), mock.patch(target, side_effect=RuntimeError("private value")):
                with mock.patch.object(logger.LoggerFactory.logbot, "warning") as warning:
                    self.assertFalse(database.repair_stale_effective(self.engine))
                    self.assertNotIn("private value", warning.call_args.args[0])
                    warning.assert_called_once()
                with self.assertRaises(RuntimeError):
                    database.repair_stale_effective(self.engine, raise_on_error=True)


if __name__ == "__main__":
    unittest.main()
