"""구버전 서버 DB 처리 — 2026-09-26 초기화 크롤링 릴리스(이전 DB 업데이트 로직 비활성).

견본 `tests/fixtures/db/server-2026-05-16.schema.sql` 은 2026-05-16 운영 DB 의 표 구조만 뽑은 것이다(데이터 없음).
이번 릴리스는 이전 DB 를 새 구조로 옮기지 않는다: upgrade_schema 는 이전 DB 를 건드리지 않고 멈추고,
reset_legacy_database 가 통째로 백업한 뒤 신고 자료를 비우고(관리자·API 키·감시목록·지오코딩 캐시만 남김) 지금 스키마로 다시 만든다.
"""
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sqlalchemy import create_engine

from core.database import database
from core.utils import logger

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "tests" / "fixtures" / "db" / "server-2026-05-16.schema.sql"
CONTRACT = json.loads((ROOT / "contracts" / "storage-contract.json").read_text(encoding="utf-8"))

TITLE = {"ID": "70000001", "상태": "수용", "신고번호": "SPP-2605-7000001", "신고명": "신호위반", "신고일": "2026-05-01", "만족도조사여부": "참여 완료", "별점": 4, "별점사유": "", "감시목록": "N"}
DETAIL = {"ID": "70000001", "처리상태": "수용", "차량번호": "12가3456", "위반법규": "도로교통법 제5조", "범칙금_과태료": "과태료", "벌점": None,
          "처리기관": "서울특별시 강서경찰서 교통과", "담당자": "김담당", "답변일": "2026-05-10", "발생일자": "2026-05-01", "발생시각": "08:00",
          "위반장소": "서울특별시 강서구 마곡동 1", "종결여부": "Y", "신고내용": "본문\n둘째 줄", "처리내용": "처리 내용", "지도": "", "첨부사진": "", "첨부파일": "",
          "synced_at": 1778400000000, "보완횟수": 0, "보완_미응답": "N"}
REPORT_TABLES = ("mysafety", "mysafetydetail_traffic", "mysafetydetail_parking", "mysafetydetail_other",
                 "mysafetymerge_traffic", "mysafetymerge_parking", "mysafetymerge_other")


class LegacyServerDbTests(unittest.TestCase):
    def setUp(self):
        logger.LoggerFactory.create_logger(mode="crawl")
        self._dir = tempfile.TemporaryDirectory()
        self.path = Path(self._dir.name) / "old.db"
        self.backups = Path(self._dir.name) / "backups"
        con = sqlite3.connect(self.path)
        con.executescript(SCHEMA.read_text(encoding="utf-8"))
        cols = [r[1] for r in con.execute('pragma table_info("mysafetydetail_traffic")')]
        detail = {k: v for k, v in DETAIL.items() if k in cols}
        con.execute(f'INSERT INTO mysafety ({",".join(TITLE)}) VALUES ({",".join("?" * len(TITLE))})', list(TITLE.values()))
        con.execute(f'INSERT INTO mysafetydetail_traffic ({",".join(detail)}) VALUES ({",".join("?" * len(detail))})', list(detail.values()))
        mcols = [r[1] for r in con.execute('pragma table_info("mysafetymerge_traffic")')]
        merged = {k: v for k, v in {**TITLE, **DETAIL}.items() if k in mcols}
        con.execute(f'INSERT INTO mysafetymerge_traffic ({",".join(merged)}) VALUES ({",".join("?" * len(merged))})', list(merged.values()))
        con.execute("INSERT INTO mysafety_watchlist (신고번호) VALUES ('SPP-2605-7000001')")
        con.execute("INSERT INTO admin_users VALUES ('admin', 'hash', 'salt')")
        con.execute("INSERT INTO api_keys VALUES ('k-1', '내 폰', '2026-05-01')")
        con.commit()
        con.close()
        self.engine = create_engine(f"sqlite:///{self.path}")

    def tearDown(self):
        self.engine.dispose()
        self._dir.cleanup()

    def _q(self, sql, path=None):
        con = sqlite3.connect(path or self.path)
        try:
            return con.execute(sql).fetchall()
        finally:
            con.close()

    def _columns(self, table, path=None):
        return [r[1] for r in self._q(f'pragma table_info("{table}")', path)]

    def _version(self, path=None):
        return self._q("PRAGMA user_version", path)[0][0]

    def test_fixture_is_really_old(self):
        self.assertNotIn("주소정규화", self._columns("mysafetydetail_traffic"))
        self.assertEqual(self._columns("mysafety_geocode_cache"), [])
        self.assertTrue(database.is_legacy_database(self.engine))

    def test_upgrade_refuses_an_old_db_and_changes_nothing(self):
        """이전 DB 업데이트 로직은 꺼져 있다: 열 추가·마이그레이션·업그레이드 전 백업 없이 멈춘다."""
        with self.assertRaises(database.LegacyDatabase):
            database.upgrade_schema(self.engine, backup_dir=str(self.backups))
        with self.assertRaises(database.LegacyDatabase):
            database.upgrade_schema(self.engine, maintenance=False)
        self.assertEqual(self._version(), 0)
        self.assertNotIn("주소정규화", self._columns("mysafetydetail_traffic"))
        self.assertEqual(self._columns("mysafety_report_override"), [])
        self.assertEqual(self._q("SELECT count(*) FROM mysafety")[0][0], 1)
        self.assertFalse(self.backups.exists())

    def test_reset_backs_up_the_whole_old_db_then_empties_reports_and_keeps_account_tables(self):
        seen = {}

        def before_reset():
            backups = sorted(self.backups.glob(f"{database.LEGACY_BACKUP_PREFIX}0_*.db"))
            seen["backups"] = backups
            seen["rows_still_there"] = self._q("SELECT count(*) FROM mysafety")[0][0]

        info = database.reset_legacy_database(self.engine, str(self.backups), before_reset=before_reset)
        # 백업: 비우기 전에, 이전 DB 그대로(버전·행)
        self.assertEqual(len(seen["backups"]), 1)
        self.assertEqual(seen["rows_still_there"], 1)
        backup = str(seen["backups"][0])
        self.assertEqual(info["backup"], backup)
        self.assertEqual(self._version(backup), 0)
        self.assertEqual(self._q("SELECT 신고번호 FROM mysafety", backup), [("SPP-2605-7000001",)])
        self.assertEqual(self._q("SELECT username FROM admin_users", backup), [("admin",)])
        # 비운 DB: 지금 스키마·계약 버전, 신고 자료 없음
        self.assertEqual(self._version(), CONTRACT["schema_version"]["server"])
        self.assertFalse(database.is_legacy_database(self.engine))
        for table in REPORT_TABLES:
            with self.subTest(table=table):
                self.assertEqual(self._q(f'SELECT count(*) FROM "{table}"')[0][0], 0)
        report = next(e for e in CONTRACT["entities"] if e["entity"] == "report")
        for placement, tables in (("detail", report["server_tables"]["detail"]), ("merge", report["server_tables"]["merge"])):
            expected = {c["name"] for c in report["columns"] if placement in c["server"]}
            for table in tables:
                with self.subTest(table=table):
                    self.assertEqual(set(self._columns(table)), expected)
        # 남긴 표: 관리자·API 키·감시목록(구조 그대로라 옮기는 코드 없음). 지오코딩 캐시는 옛 DB 에 없어 새로 만듦.
        self.assertEqual(self._q("SELECT username, password_hash, salt FROM admin_users"), [("admin", "hash", "salt")])
        self.assertEqual(self._q("SELECT key, name, created_at FROM api_keys"), [("k-1", "내 폰", "2026-05-01")])
        self.assertEqual(self._q("SELECT 신고번호 FROM mysafety_watchlist"), [("SPP-2605-7000001",)])
        self.assertEqual(info["kept"], ["admin_users", "api_keys", "mysafety_watchlist"])
        self.assertIn("mysafety", info["dropped"])
        self.assertTrue(self._columns("mysafety_geocode_cache"))
        for table in ("mysafety_report_override", "mysafety_duplicate_decision", "mysafety_change_log", "mysafety_change_cursor"):
            self.assertTrue(self._columns(table), table)
        indexes = {r[0] for r in self._q("SELECT name FROM sqlite_master WHERE type='index'")}
        self.assertTrue({"ix_mysafety_report_number", "ix_detail_traffic_closed", "ix_merge_other_report_number",
                         "ix_duplicate_member_report"} <= indexes)
        # 안내 화면용 기록
        stored = database.legacy_reset_info(self.engine)
        self.assertEqual((stored["from_version"], stored["backup"]), (0, backup))
        # 이제 평소 시작 경로가 돈다. 다시 불러도 아무 일 없음.
        database.upgrade_schema(self.engine)
        self.assertIsNone(database.reset_legacy_database(self.engine, str(self.backups)))
        self.assertEqual(len(list(self.backups.glob("*.db"))), 1)
        self.assertEqual(self._q("SELECT username FROM admin_users"), [("admin",)])

    def test_a_kept_table_with_another_structure_is_rebuilt_empty(self):
        con = sqlite3.connect(self.path)
        con.execute("CREATE TABLE mysafety_geocode_cache (주소 TEXT PRIMARY KEY, 위도 REAL)")
        con.execute("INSERT INTO mysafety_geocode_cache VALUES ('옛 주소', 37.0)")
        con.commit()
        con.close()
        info = database.reset_legacy_database(self.engine, str(self.backups))
        self.assertNotIn("mysafety_geocode_cache", info["kept"])
        self.assertIn("주소정규화", self._columns("mysafety_geocode_cache"))
        self.assertEqual(self._q("SELECT count(*) FROM mysafety_geocode_cache")[0][0], 0)

    def test_a_compatible_geocode_cache_is_kept(self):
        con = sqlite3.connect(self.path)
        con.execute("CREATE TABLE mysafety_geocode_cache (주소정규화 VARCHAR NOT NULL, 원본주소 VARCHAR, 행정구역 VARCHAR, 위도 FLOAT, 경도 FLOAT,"
                    " 상태 VARCHAR NOT NULL, source VARCHAR NOT NULL, error_message VARCHAR, updated_at INTEGER, PRIMARY KEY (주소정규화))")
        con.execute("INSERT INTO mysafety_geocode_cache VALUES ('서울 강서구 1', NULL, '서울 강서구', 37.5, 126.8, 'ok', 'kakao', NULL, 1)")
        con.commit()
        con.close()
        info = database.reset_legacy_database(self.engine, str(self.backups))
        self.assertIn("mysafety_geocode_cache", info["kept"])
        self.assertEqual(self._q("SELECT 주소정규화, 위도 FROM mysafety_geocode_cache"), [("서울 강서구 1", 37.5)])

    def test_an_admin_table_with_another_structure_stops_without_touching_anything(self):
        con = sqlite3.connect(self.path)
        con.execute("DROP TABLE admin_users")
        con.execute("CREATE TABLE admin_users (username VARCHAR NOT NULL PRIMARY KEY, password VARCHAR)")
        con.commit()
        con.close()
        with self.assertRaises(database.LegacyDatabase):
            database.reset_legacy_database(self.engine, str(self.backups))
        self.assertEqual(self._version(), 0)
        self.assertEqual(self._q("SELECT count(*) FROM mysafety")[0][0], 1)
        self.assertFalse(self.backups.exists())

    def test_a_failure_inside_the_reset_rolls_everything_back(self):
        with mock.patch.object(database, "_index_statements", return_value=["CREATE INDEX broken ON no_such_table (x)"]):
            with self.assertRaises(sqlite3.OperationalError):
                database.reset_legacy_database(self.engine, str(self.backups))
        self.assertEqual(self._version(), 0)
        self.assertEqual(self._q("SELECT count(*) FROM mysafety")[0][0], 1)
        self.assertEqual(self._q("SELECT count(*) FROM mysafetymerge_traffic")[0][0], 1)
        self.assertEqual(self._columns("mysafety_report_override"), [])
        self.assertEqual(self._q("SELECT count(*) FROM sqlite_master WHERE name='mysafety_sync_meta'")[0][0], 1)
        self.assertEqual(len(list(self.backups.glob("*.db"))), 1)  # 백업은 남는다

    def test_a_failing_before_reset_hook_drops_nothing(self):
        with self.assertRaises(RuntimeError):
            database.reset_legacy_database(self.engine, str(self.backups), before_reset=mock.Mock(side_effect=RuntimeError("x")))
        self.assertEqual(self._version(), 0)
        self.assertEqual(self._q("SELECT count(*) FROM mysafety")[0][0], 1)

    def test_newer_database_is_refused(self):
        con = sqlite3.connect(self.path)
        con.execute(f"PRAGMA user_version = {database.SCHEMA_VERSION + 1}")
        con.commit()
        con.close()
        with self.assertRaises(RuntimeError):
            database.upgrade_schema(self.engine)
        with self.assertRaises(RuntimeError):
            database.reset_legacy_database(self.engine, str(self.backups))
        self.assertNotIn("주소정규화", self._columns("mysafetydetail_traffic"))  # 거부 전에 아무것도 바꾸지 않음
        self.assertFalse(self.backups.exists())

    def test_every_version_below_the_current_one_is_legacy(self):
        for version in range(0, database.SCHEMA_VERSION):
            con = sqlite3.connect(self.path)
            con.execute(f"PRAGMA user_version = {version}")
            con.commit()
            con.close()
            with self.subTest(version=version):
                self.assertTrue(database.is_legacy_database(self.engine))


class NewServerDbTests(unittest.TestCase):
    def setUp(self):
        logger.LoggerFactory.create_logger(mode="crawl")
        self._dir = tempfile.TemporaryDirectory()
        self.path = Path(self._dir.name) / "new.db"
        self.engine = create_engine(f"sqlite:///{self.path}")

    def tearDown(self):
        self.engine.dispose()
        self._dir.cleanup()

    def _indexes(self):
        con = sqlite3.connect(self.path)
        try:
            return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        finally:
            con.close()

    def test_new_db_is_created_at_the_contract_version_without_backup_or_reset(self):
        backups = Path(self._dir.name) / "backups"
        self.assertIsNone(database.reset_legacy_database(self.engine, str(backups)))
        database.upgrade_schema(self.engine, backup_dir=str(backups))
        self.assertEqual(database.get_schema_version(self.engine), CONTRACT["schema_version"]["server"])
        self.assertEqual(database.SCHEMA_VERSION, CONTRACT["schema_version"]["server"])
        self.assertFalse(database.is_legacy_database(self.engine))
        self.assertIsNone(database.legacy_reset_info(self.engine))
        self.assertFalse(backups.exists() and any(backups.iterdir()))
        database.upgrade_schema(self.engine)  # 두 번 돌려도 같다
        self.assertEqual(database.get_schema_version(self.engine), database.SCHEMA_VERSION)

    def test_light_upgrade_for_crawler_creates_tables_version_and_indexes(self):
        database.upgrade_schema(self.engine, maintenance=False)
        self.assertEqual(database.get_schema_version(self.engine), database.SCHEMA_VERSION)
        self.assertTrue({"ix_mysafety_report_number", "ix_detail_traffic_closed", "ix_duplicate_member_report"} <= self._indexes())

    def test_dropped_report_tables_come_back_with_indexes_on_a_current_db(self):
        """start.py --reset 처럼 지금 버전 DB 의 신고 표를 지운 뒤에도 표·인덱스가 돌아온다(버전은 그대로)."""
        database.upgrade_schema(self.engine)
        con = sqlite3.connect(self.path)
        con.execute("DROP TABLE mysafetymerge_traffic")
        con.commit()
        con.close()
        database.upgrade_schema(self.engine, maintenance=False)
        self.assertIn("ix_merge_traffic_report_number", self._indexes())
        self.assertEqual(database.get_schema_version(self.engine), database.SCHEMA_VERSION)

    def test_the_old_update_steps_are_not_called(self):
        """주석 처리한 업데이트 로직(번호 붙은 마이그레이션·열 추가·옛 자료 정리·업그레이드 전 백업)이 다시 불리지 않는다."""
        names = ("_apply_versioned_migrations", "backup_before_upgrade", "migrate_by_entry_value",
                 "backfill_synced_at", "_normalize_processing_layers")
        patches = [mock.patch.object(database, name) for name in names]
        mocks = [p.start() for p in patches]
        try:
            database.upgrade_schema(self.engine, backup_dir=str(Path(self._dir.name) / "backups"))
            database.upgrade_schema(self.engine)
        finally:
            for p in patches:
                p.stop()
        for name, m in zip(names, mocks):
            with self.subTest(step=name):
                m.assert_not_called()


if __name__ == "__main__":
    unittest.main()
