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

from sqlalchemy import create_engine, select, text

from core.database import database, models
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
        for table in ("mysafety_report", "mysafety_report_effective", "mysafety_report_body"):
            with self.subTest(table=table):
                self.assertEqual(self._q(f'SELECT count(*) FROM "{table}"')[0][0], 0)
        for old in REPORT_TABLES:  # 스키마 6: 옛 표는 되살리지 않는다(옮기는 코드 없음) — 아예 없다
            with self.subTest(dropped=old):
                self.assertEqual(self._q(
                    f"SELECT count(*) FROM sqlite_master WHERE type='table' AND name='{old}'")[0][0], 0)
        report = next(e for e in CONTRACT["entities"] if e["entity"] == "report")
        for placement, table in report["server_tables"].items():
            expected = {c["name"] for c in report["columns"] if placement in c["server"]}
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
        self.assertTrue({"ix_report_number", "ix_report_category_closed", "ix_effective_report_number",
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

    def test_a_second_start_that_decided_before_the_first_finished_changes_nothing(self):
        """Sol 검토 1: 두 프로세스가 모두 '이전 버전' 으로 판정한 뒤 A 가 먼저 비우고 수집을 시작했으면, B 는 잠금 안에서 다시 확인해 아무것도 하지 않는다."""
        database.reset_legacy_database(self.engine, str(self.backups))  # A
        con = sqlite3.connect(self.path)
        con.execute("INSERT INTO mysafety_report (ID, 신고번호) VALUES ('90000001', 'SPP-NEW')")  # A 가 그 뒤 수집한 신고
        con.commit()
        con.close()
        rotated = mock.Mock()
        with mock.patch.object(database, "is_legacy_database", return_value=True):  # B 의 낡은 판정
            self.assertIsNone(database.reset_legacy_database(self.engine, str(self.backups), before_reset=rotated))
        rotated.assert_not_called()
        self.assertEqual(self._q("SELECT ID FROM mysafety_report"), [("90000001",)])
        self.assertEqual(len(list(self.backups.glob("*.db"))), 1, "B 는 백업도 만들지 않는다")

    def test_a_reset_waits_for_the_one_already_running_and_then_does_nothing(self):
        import threading

        holder = sqlite3.connect(self.path, isolation_level=None)
        holder.execute("BEGIN IMMEDIATE")  # A 가 비우는 중(쓰기 잠금)
        result = {}

        def second():
            try:
                result["info"] = database.reset_legacy_database(self.engine, str(self.backups))
            except BaseException as exc:  # pragma: no cover - 실패 원인 보고용
                result["error"] = exc

        with mock.patch.object(database, "LEGACY_RESET_LOCK_TIMEOUT", 30.0):
            t = threading.Thread(target=second)
            t.start()
            t.join(0.5)
            self.assertTrue(t.is_alive(), "B 는 A 의 잠금을 기다린다")
            holder.execute("DELETE FROM mysafety")  # A 가 비우고 버전을 올린 것처럼
            holder.execute(f"PRAGMA user_version = {database.SCHEMA_VERSION}")
            holder.execute("INSERT INTO mysafety (ID, 신고번호) VALUES ('90000002', 'SPP-AFTER')")
            holder.execute("COMMIT")
            holder.close()
            t.join(30)
        self.assertNotIn("error", result)
        self.assertIsNone(result["info"])
        self.assertEqual(self._q("SELECT ID FROM mysafety"), [("90000002",)])
        self.assertFalse(self.backups.exists() and any(self.backups.iterdir()))

    def test_crawler_reset_refuses_an_old_db_before_deleting_anything(self):
        """Sol 검토 3: start.py --reset 이 이전 버전 DB 의 신고를 백업 없이 지우지 않는다."""
        import start

        with self.assertRaises(database.LegacyDatabase):
            start._prepare_database(self.engine, reset=True)
        self.assertEqual(self._version(), 0)
        self.assertEqual(self._q("SELECT count(*) FROM mysafety")[0][0], 1)
        self.assertEqual(self._q("SELECT count(*) FROM mysafetymerge_traffic")[0][0], 1)
        with self.assertRaises(database.LegacyDatabase):
            start._prepare_database(self.engine, reset=False)

    def test_crawler_reset_on_a_reset_db_keeps_the_legacy_record(self):
        import start

        database.reset_legacy_database(self.engine, str(self.backups))
        start._prepare_database(self.engine, reset=True)
        self.assertIsNotNone(database.legacy_reset_info(self.engine))
        self.assertEqual(self._q("SELECT username FROM admin_users"), [("admin",)])

    def test_an_old_db_with_virtual_tables_is_backed_up_and_emptied(self):
        """Sol 재검증 6: FTS 가상 표를 지우면 그 보조 표도 지워진다 — 뒤이은 보조 표 DROP 이 'no such table' 로 멈추지 않는다."""
        con = sqlite3.connect(self.path)
        modules = []
        for module in ("fts4", "fts5"):
            try:
                con.execute(f"CREATE VIRTUAL TABLE old_{module} USING {module}(body)")
                con.execute(f"INSERT INTO old_{module} (body) VALUES ('옛 검색 색인')")
                modules.append(module)
            except sqlite3.OperationalError:
                pass
        con.commit()
        con.close()
        self.assertTrue(modules, "이 SQLite 에 FTS 모듈이 없음")
        info = database.reset_legacy_database(self.engine, str(self.backups))
        self.assertEqual(self._version(), database.SCHEMA_VERSION)
        self.assertEqual(self._q("SELECT name FROM sqlite_master WHERE name LIKE 'old_fts%'"), [])
        for module in modules:
            self.assertEqual(self._q(f"SELECT body FROM old_{module} WHERE old_{module} MATCH '검색'", info["backup"]), [("옛 검색 색인",)])
        self.assertEqual(self._q("SELECT username FROM admin_users"), [("admin",)])

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
        self.assertTrue({"ix_report_number", "ix_report_category_closed", "ix_duplicate_member_report"} <= self._indexes())

    def test_dropped_report_tables_come_back_with_indexes_on_a_current_db(self):
        """start.py --reset 처럼 지금 버전 DB 의 신고 표를 지운 뒤에도 표·인덱스가 돌아온다(버전은 그대로)."""
        database.upgrade_schema(self.engine)
        con = sqlite3.connect(self.path)
        con.execute("DROP TABLE mysafety_report_effective")
        con.commit()
        con.close()
        database.upgrade_schema(self.engine, maintenance=False)
        self.assertIn("ix_effective_report_number", self._indexes())
        self.assertEqual(database.get_schema_version(self.engine), database.SCHEMA_VERSION)

    def test_the_old_update_steps_are_not_called(self):
        """스키마 6: 번호 붙은 버전별 마이그레이션의 보조 함수(migrate_by_entry_value·backfill_synced_at·
        _normalize_processing_layers·category_from_entry_value·_migration_1~4·_apply_versioned_migrations·
        backup_before_upgrade)는 이름조차 남지 않았다. upgrade_schema 는 표 만들기만 하고, 실제 버전 전환(migrate_schema)·
        이전 DB 비우기(reset_legacy_database)도 스스로 부르지 않는다(호출 순서는 main.py 가 정한다)."""
        removed = ("migrate_by_entry_value", "backfill_synced_at", "_normalize_processing_layers",
                  "category_from_entry_value", "_migration_1", "_migration_2", "_migration_3", "_migration_4",
                  "_apply_versioned_migrations", "backup_before_upgrade")
        for name in removed:
            with self.subTest(removed=name):
                self.assertFalse(hasattr(database, name), name)
        names = ("migrate_schema", "reset_legacy_database")
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


# ── 스키마 5 → 6 제자리 변환(migrate_schema) ──────────────────────────────────
# v5 표 구조는 tests/fixtures 가 아니라 여기서 직접 만든다(/home/better0101/projects/worktree/v5_models_reference.py 참고).
# 그 파일은 settings.table_title 같은 지금은 없는 settings 속성을 쓰므로 그대로 import 하지 않는다.

_V5_DETAIL_COLUMNS = (
    "ID", "처리상태", "차량번호", "위반법규", "범칙금_과태료", "벌점", "처리기관", "처리기관코드", "담당자",
    "답변일", "발생일자", "발생시각", "위반장소", "주소정규화", "행정구역", "위도", "경도", "지오코딩상태",
    "종결여부", "신고내용", "처리내용", "지도", "첨부사진", "첨부파일", "synced_at", "보완횟수", "보완_미응답",
    "보완_요청자", "보완_요청일시", "보완_완료일시", "보완_요청_내용", "보완_신고자_의견",
    "사진_첫촬영", "사진_끝촬영", "사진_촬영수",
)

# (ID, 상태, 신고번호, 신고명, 신고일, 만족도조사여부, 별점, 별점사유, 감시목록)
_V5_TITLES = [
    ("v5-list-1", "접수", "SPP-V5-LIST-1", "목록만 1", "2026-01-01", "", None, "", "N"),
    ("v5-list-2", "접수", "SPP-V5-LIST-2", "목록만 2", "2026-01-02", "", None, "", "N"),
    ("v5-traffic-1", "수용", "SPP-V5-TRAFFIC-1", "신호위반", "2026-02-01", "참여 완료", 5, "친절하고 빠름", "N"),
    ("v5-parking-1", "수용", "SPP-V5-PARKING-1", "불법주정차", "2026-03-01", "", None, "", "N"),
    ("v5-other-1", "수용", "SPP-V5-OTHER-1", "생활불편", "2026-04-01", "", None, "", "N"),
    ("v5-other-2", "수용", "SPP-V5-OTHER-2", "생활불편(쌍)", "2026-04-02", "", None, "", "N"),
]

# {old_category: [(ID, 처리상태, 차량번호, 위반법규, 범칙금_과태료, 벌점, 처리기관, 처리기관코드, 담당자, 답변일,
#                  발생일자, 발생시각, 위반장소, 주소정규화, 행정구역, 위도, 경도, 지오코딩상태, 종결여부, 신고내용,
#                  처리내용, 지도, 첨부사진, 첨부파일, synced_at, 보완횟수, 보완_미응답, 보완_요청자, 보완_요청일시,
#                  보완_완료일시, 보완_요청_내용, 보완_신고자_의견, 사진_첫촬영, 사진_끝촬영, 사진_촬영수), ...]}
_V5_DETAILS = {
    "traffic": [("v5-traffic-1", "수용", "12가3456", "도로교통법 제5조", "과태료 40,000원", "", "서울경찰서", "S001",
                "김담당", "2026-02-10", "2026-02-01", "08:00", "서울 강서구 1", "서울 강서구 1", "서울 강서구",
                37.5, 126.8, "ok", "Y", "본문\n둘째 줄", "처리 결과", "", "", "", 1700000000000, 0, "N", "", "",
                "", "", "", None, None, None)],
    "parking": [("v5-parking-1", "수용", "34나5678", "", "과태료", "", "강서구청", "P001", "정담당", "2026-03-05",
                "2026-03-01", "10:00", "서울 강서구 2", "서울 강서구 2", "서울 강서구", 37.55, 126.82, "ok", "Y",
                "본문2", "처리 결과2", "", "", "", 1700000001000, 0, "N", "", "", "", "", "", None, None, None)],
    # 같은 본문(payload_hash 동일)의 두 건 — 재분류·중복군 재계산이 실제로 돌 때 기대값으로 쓴다.
    "other": [("v5-other-1", "수용", "", "", "", "", "강서구청", "O001", "한담당", "2026-04-10", "2026-04-01",
              "14:00", "서울 강서구 3", "서울 강서구 3", "서울 강서구", 37.56, 126.83, "ok", "Y", "본문3",
              "처리 결과3", "", "", "", 1700000002000, 0, "N", "", "", "", "", "", None, None, None),
             ("v5-other-2", "수용", "", "", "", "", "강서구청", "O002", "한담당", "2026-04-11", "2026-04-02",
              "15:00", "서울 강서구 4", "서울 강서구 4", "서울 강서구", 37.57, 126.84, "ok", "Y", "본문4",
              "처리 결과4", "", "", "", 1700000003000, 0, "N", "", "", "", "", "", None, None, None)],
}

_V5_ENTRY_VALUES = {
    "v5-traffic-1": "자동차·교통위반-신호위반",
    "v5-parking-1": "불법주정차신고-인도",
    # CM-05 후계 분류: living(기존 "other" 분류에서 바뀐다). 과제 명세의 공백 섞인 표기를 그대로 쓴다(정규화가 지운다).
    "v5-other-1": "생활불편 신고-쓰레기, 폐기물",
    "v5-other-2": "생활불편 신고-쓰레기, 폐기물",
}
# v5-other-1·v5-other-2 는 같은 본문 → migrate_schema 뒤 rebuild_effective 가 실제 중복군으로 다시 묶는다.
_V5_DUPLICATE_BODY = "(v5 검수) 중복 확인용 같은 본문입니다."


def _build_v5_db(path: Path) -> None:
    """목록만 본 신고 2건 + 세 분류 상세 각 1건 + entry_value + 본문 + 수정값 + 중복 판단·멤버 + 감시목록이 있는
    버전 5 DB(스키마 5)를 만든다."""
    con = sqlite3.connect(path)
    try:
        con.execute(f"PRAGMA user_version = {5}")
        con.execute("CREATE TABLE mysafety (ID TEXT PRIMARY KEY, 상태 TEXT, 신고번호 TEXT, 신고명 TEXT, 신고일 TEXT, "
                    "만족도조사여부 TEXT, 별점 INTEGER, 별점사유 TEXT, 감시목록 TEXT)")
        detail_cols_sql = ", ".join(f'"{c}" {"INTEGER" if c in ("synced_at", "보완횟수", "사진_촬영수") else ("REAL" if c in ("위도", "경도") else "TEXT")}'
                                    for c in _V5_DETAIL_COLUMNS[1:])
        for category in ("traffic", "parking", "other"):
            con.execute(f'CREATE TABLE mysafetydetail_{category} (ID TEXT PRIMARY KEY, {detail_cols_sql})')
        con.execute("CREATE TABLE mysafety_entry_value (ID TEXT PRIMARY KEY, entry_value TEXT NOT NULL)")
        con.execute("CREATE TABLE mysafety_raw_content (ID TEXT PRIMARY KEY, raw_content TEXT NOT NULL DEFAULT '', "
                    "raw_type TEXT NOT NULL DEFAULT '', saved_at INTEGER)")
        con.execute("CREATE TABLE mysafety_report_override (ID TEXT NOT NULL, column_name TEXT NOT NULL, value TEXT, "
                    "updated_at INTEGER NOT NULL, PRIMARY KEY (ID, column_name))")
        con.execute("CREATE TABLE mysafety_duplicate_group (group_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, "
                    "match_type TEXT NOT NULL, status TEXT NOT NULL, representative_mode TEXT NOT NULL DEFAULT 'auto', "
                    "representative_id TEXT, member_count INTEGER NOT NULL DEFAULT 0, "
                    "apply_globally INTEGER NOT NULL DEFAULT 1, note TEXT, created_at INTEGER, updated_at INTEGER)")
        con.execute("CREATE TABLE mysafety_duplicate_member (group_id TEXT NOT NULL, report_id TEXT NOT NULL, "
                    "report_number TEXT NOT NULL, category TEXT NOT NULL, is_representative INTEGER NOT NULL DEFAULT 0, "
                    "priority_score INTEGER NOT NULL DEFAULT 0, raw_match INTEGER NOT NULL DEFAULT 0, "
                    "field_match INTEGER NOT NULL DEFAULT 0, created_at INTEGER, updated_at INTEGER, "
                    "PRIMARY KEY (group_id, report_id))")
        con.execute("CREATE TABLE mysafety_duplicate_decision (group_id TEXT PRIMARY KEY, status TEXT NOT NULL, "
                    "representative_mode TEXT NOT NULL, representative_id TEXT, apply_globally INTEGER NOT NULL, "
                    "note TEXT, updated_at INTEGER NOT NULL)")
        con.execute("CREATE TABLE mysafety_watchlist (신고번호 TEXT PRIMARY KEY)")
        con.execute("CREATE TABLE mysafety_sync_meta (key TEXT PRIMARY KEY, value TEXT)")
        # admin_users·api_keys 는 reset_legacy_database 의 _keepable() 이 열 모양까지 정확히 대조한다(VARCHAR·PK 분리 선언).
        con.execute('CREATE TABLE admin_users (username VARCHAR NOT NULL, password_hash VARCHAR NOT NULL, '
                    'salt VARCHAR NOT NULL, PRIMARY KEY (username))')
        con.execute('CREATE TABLE api_keys ("key" VARCHAR NOT NULL, name VARCHAR NOT NULL, '
                    'created_at VARCHAR NOT NULL, PRIMARY KEY ("key"))')

        con.executemany(f'INSERT INTO mysafety ({",".join("ID 상태 신고번호 신고명 신고일 만족도조사여부 별점 별점사유 감시목록".split())}) '
                        f'VALUES ({",".join("?" * 9)})', _V5_TITLES)
        cols = ",".join(f'"{c}"' for c in _V5_DETAIL_COLUMNS)
        for category, rows in _V5_DETAILS.items():
            for row in rows:
                con.execute(f'INSERT INTO mysafetydetail_{category} ({cols}) VALUES ({",".join("?" * len(_V5_DETAIL_COLUMNS))})', row)
        con.executemany("INSERT INTO mysafety_entry_value (ID, entry_value) VALUES (?, ?)", list(_V5_ENTRY_VALUES.items()))
        con.execute("INSERT INTO mysafety_raw_content (ID, raw_content, raw_type, saved_at) VALUES "
                    "('v5-traffic-1', '본문\n둘째 줄', 'report_body', 1700000000500)")
        con.executemany("INSERT INTO mysafety_raw_content (ID, raw_content, raw_type, saved_at) VALUES (?, ?, 'report_body', ?)",
                        [("v5-other-1", _V5_DUPLICATE_BODY, 1700000004000), ("v5-other-2", _V5_DUPLICATE_BODY, 1700000005000)])
        con.execute("INSERT INTO mysafety_report_override (ID, column_name, value, updated_at) VALUES "
                    "('v5-traffic-1', '처리내용', '사용자 수정', 1)")
        # 이미 버전 5 시절에 (지금은 중복이 아닌) 다른 쌍을 중복으로 판단해 둔 기록 — 판단 표는 사용자 소유라
        # 재계산 뒤에도 그 값 그대로 남아야 한다(group_id 가 재계산 결과와 맞지 않아도 지워지지 않는다, 결정 D-6).
        con.execute("INSERT INTO mysafety_duplicate_group (group_id, fingerprint, match_type, status, "
                    "representative_mode, representative_id, member_count, apply_globally, note, created_at, updated_at) "
                    "VALUES ('legacy-orphan-group', 'fp-legacy', 'payload_exact', 'not_duplicate', 'manual', "
                    "'v5-traffic-1', 1, 0, '검수 메모', 1, 5)")
        con.execute("INSERT INTO mysafety_duplicate_member (group_id, report_id, report_number, category, "
                    "is_representative, priority_score, raw_match, field_match, created_at, updated_at) VALUES "
                    "('legacy-orphan-group', 'v5-traffic-1', 'SPP-V5-TRAFFIC-1', 'traffic', 1, 1, 1, 1, 1, 1)")
        con.execute("INSERT INTO mysafety_duplicate_decision (group_id, status, representative_mode, "
                    "representative_id, apply_globally, note, updated_at) VALUES "
                    "('legacy-orphan-group', 'not_duplicate', 'manual', 'v5-traffic-1', 0, '검수 메모', 5)")
        con.execute("INSERT INTO mysafety_watchlist (신고번호) VALUES ('SPP-V5-TRAFFIC-1')")
        con.execute("INSERT INTO admin_users VALUES ('admin', 'hash', 'salt')")
        con.execute("INSERT INTO api_keys VALUES ('k-1', '내 폰', '2026-01-01')")
        con.commit()
    finally:
        con.close()


class MigrateSchemaTests(unittest.TestCase):
    def setUp(self):
        logger.LoggerFactory.create_logger(mode="crawl")
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.path = Path(self._dir.name) / "v5.db"
        self.backups = Path(self._dir.name) / "backups"
        _build_v5_db(self.path)
        self.engine = create_engine(f"sqlite:///{self.path}")
        self.addCleanup(self.engine.dispose)

    def _q(self, sql, path=None):
        con = sqlite3.connect(path or self.path)
        try:
            return con.execute(sql).fetchall()
        finally:
            con.close()

    def _version(self, path=None):
        return self._q("PRAGMA user_version", path)[0][0]

    def _tables(self, path=None):
        return {r[0] for r in self._q("SELECT name FROM sqlite_master WHERE type='table'", path)}

    def _report_row(self, record_id):
        with self.engine.connect() as conn:
            row = conn.execute(select(models.report_table).where(models.report_table.c.ID == record_id)).mappings().first()
        return dict(row) if row is not None else None

    def test_migrates_rows_categories_body_and_user_tables(self):
        info = database.migrate_schema(self.engine, str(self.backups))
        self.assertEqual(self._version(), 6)
        self.assertEqual(info["from_version"], 5)
        self.assertEqual(info["reports"], 6)
        self.assertEqual(info["detailed"], 4)  # 목록만 본 2건은 상세가 없다
        self.assertEqual(info["moved_categories"], 2)  # other → living 두 건(v5-other-1·2)
        self.assertTrue(Path(info["backup"]).is_file())

        # 목록만 본 신고: category NULL, 화면용 표에 없음
        for record_id in ("v5-list-1", "v5-list-2"):
            row = self._report_row(record_id)
            self.assertIsNotNone(row, record_id)
            self.assertIsNone(row["category"], record_id)
            with self.engine.connect() as conn:
                self.assertIsNone(conn.execute(
                    select(models.report_effective_table.c.ID).where(models.report_effective_table.c.ID == record_id)
                ).scalar(), record_id)

        # 상세가 있던 신고: 모든 사이트 열 값이 그대로 옮겨졌다(카테고리 제외)
        traffic = self._report_row("v5-traffic-1")
        self.assertEqual(traffic["category"], "traffic")  # 그대로(자동차·교통위반)
        self.assertEqual(traffic["ssnpc_cd"], None)
        self.assertEqual(traffic["ssnpc_type"], None)
        self.assertEqual(traffic["entry_value"], "자동차·교통위반-신호위반")
        self.assertEqual((traffic["차량번호"], traffic["위반법규"], traffic["처리기관"], traffic["처리기관코드"],
                          traffic["담당자"], traffic["답변일"], traffic["위도"], traffic["경도"], traffic["종결여부"],
                          traffic["신고내용"], traffic["synced_at"]),
                         ("12가3456", "도로교통법 제5조", "서울경찰서", "S001", "김담당", "2026-02-10", 37.5, 126.8,
                          "Y", "본문\n둘째 줄", 1700000000000))
        self.assertEqual((traffic["상태"], traffic["신고번호"], traffic["신고명"], traffic["신고일"],
                          traffic["만족도조사여부"], traffic["별점"], traffic["별점사유"]),
                         ("수용", "SPP-V5-TRAFFIC-1", "신호위반", "2026-02-01", "참여 완료", 5, "친절하고 빠름"))

        parking = self._report_row("v5-parking-1")
        self.assertEqual(parking["category"], "parking")  # 그대로(불법주정차신고-인도)
        self.assertEqual(parking["entry_value"], "불법주정차신고-인도")

        other = self._report_row("v5-other-1")
        self.assertEqual(other["category"], "living")  # (구) 기타 분류 → 생활불편(CM-05 후계)
        self.assertEqual(other["entry_value"], "생활불편 신고-쓰레기, 폐기물")
        self.assertEqual(self._report_row("v5-other-2")["category"], "living")

        # 본문은 그대로 옮긴다
        with self.engine.connect() as conn:
            body = conn.execute(select(models.report_body_table).where(
                models.report_body_table.c.ID == "v5-traffic-1")).mappings().one()
        self.assertEqual((body["raw_content"], body["raw_type"], body["saved_at"]),
                         ("본문\n둘째 줄", "report_body", 1700000000500))

        # 사용자 소유 표는 그대로: 수정값, 감시목록
        with self.engine.connect() as conn:
            override = conn.execute(select(models.report_override_table)).mappings().one()
            self.assertEqual((override["ID"], override["column_name"], override["value"]),
                             ("v5-traffic-1", "처리내용", "사용자 수정"))
            self.assertEqual(conn.execute(select(models.watchlist_table.c.신고번호)).scalar(), "SPP-V5-TRAFFIC-1")
            # 중복 판단은 그대로(사용자 소유 — 재계산 결과와 group_id 가 맞지 않아도 지워지지 않는다, 결정 D-6)
            decision = conn.execute(select(models.duplicate_decision_table)).mappings().one()
            self.assertEqual((decision["group_id"], decision["status"], decision["note"]),
                             ("legacy-orphan-group", "not_duplicate", "검수 메모"))
            # v5-other-1·2 는 같은 본문이라 재계산이 실제 중복군으로 다시 묶고, 멤버의 category 는 새 분류(living)다
            members = {m["report_id"]: m["category"] for m in conn.execute(
                select(models.duplicate_member_table)).mappings() if m["report_id"] in ("v5-other-1", "v5-other-2")}
            self.assertEqual(members, {"v5-other-1": "living", "v5-other-2": "living"})

        # 옛 표는 지운다
        tables = self._tables()
        for old in ("mysafety", "mysafetydetail_traffic", "mysafetydetail_parking", "mysafetydetail_other",
                   "mysafety_entry_value", "mysafety_raw_content"):
            self.assertNotIn(old, tables, old)
        # 지우지 않는 사용자 소유 표
        for kept in ("mysafety_report_override", "mysafety_duplicate_decision", "mysafety_duplicate_member",
                    "mysafety_duplicate_group", "mysafety_watchlist", "admin_users", "api_keys"):
            self.assertIn(kept, tables, kept)

        # 두 번째 호출은 할 일이 없다(이미 버전 6)
        self.assertIsNone(database.migrate_schema(self.engine, str(self.backups)))

    def test_verification_mismatch_rolls_back_and_keeps_the_backup(self):
        before_tables = self._tables()
        before_rows = self._q("SELECT ID FROM mysafety ORDER BY ID")
        with mock.patch.object(database, "_v5_source_checks", return_value=["mysafetydetail_traffic"]):
            with self.assertRaises(database.MigrationVerifyFailed):
                database.migrate_schema(self.engine, str(self.backups))
        # DB 는 그대로: 버전 5, 옛 표·행 그대로
        self.assertEqual(self._version(), 5)
        self.assertEqual(self._tables(), before_tables)
        self.assertEqual(self._q("SELECT ID FROM mysafety ORDER BY ID"), before_rows)
        # 그래도 대조 전에 만든 백업은 남는다(실패해도 사본은 있다)
        backups = list(self.backups.glob("*.db"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(self._q("SELECT ID FROM mysafety ORDER BY ID", str(backups[0])), before_rows)

    def _assert_untouched_after(self, before_tables, before_rows):
        self.assertEqual(self._version(), 5)
        self.assertEqual(self._tables(), before_tables)
        self.assertEqual(self._q("SELECT ID FROM mysafety ORDER BY ID"), before_rows)

    def test_source_value_lost_in_conversion_is_caught_by_the_original_table_check(self):
        """합친 결과끼리가 아니라 원본 표 기준으로 대조한다(Astra 2026-10-11 지적 2): 변환 중 상세 값이 바뀌면 되돌린다."""
        before_tables, before_rows = self._tables(), self._q("SELECT ID FROM mysafety ORDER BY ID")
        real_rows = database._v5_rows

        def corrupt(conn):
            rows = real_rows(conn)
            for row in rows:
                if row.get("_old_category") == "traffic":
                    row["처리내용"] = "변환 중 바뀐 값"
                    break
            return rows

        with mock.patch.object(database, "_v5_rows", side_effect=corrupt):
            with self.assertRaises(database.MigrationVerifyFailed) as ctx:
                database.migrate_schema(self.engine, str(self.backups))
        self.assertIn("mysafetydetail_traffic", str(ctx.exception))
        self._assert_untouched_after(before_tables, before_rows)

    def test_same_id_in_two_detail_tables_stops_before_converting(self):
        con = sqlite3.connect(self.path)
        traffic_id = con.execute("SELECT ID FROM mysafetydetail_traffic LIMIT 1").fetchone()[0]
        cols = [r[1] for r in con.execute('PRAGMA table_info("mysafetydetail_parking")')]
        con.execute(f'INSERT INTO mysafetydetail_parking ({", ".join(chr(34)+c+chr(34) for c in cols)}) '
                    f'SELECT {", ".join(chr(34)+c+chr(34) for c in cols)} FROM mysafetydetail_traffic WHERE ID = ?', (traffic_id,))
        con.commit()
        con.close()
        before_tables, before_rows = self._tables(), self._q("SELECT ID FROM mysafety ORDER BY ID")
        with self.assertRaises(database.MigrationVerifyFailed) as ctx:
            database.migrate_schema(self.engine, str(self.backups))
        self.assertIn("두 분류 상세 표에 같은 신고", str(ctx.exception))
        self._assert_untouched_after(before_tables, before_rows)

    def test_unknown_column_with_values_stops_but_an_all_null_one_does_not(self):
        con = sqlite3.connect(self.path)
        con.execute('ALTER TABLE mysafetydetail_other ADD COLUMN "옛_열" TEXT')
        con.commit()
        con.close()
        # 값이 모두 NULL 인 모르는 열은 잃을 값이 없으므로 통과
        self.assertIsNotNone(database.migrate_schema(self.engine, str(self.backups)))

    def test_unknown_column_holding_a_value_stops_before_converting(self):
        con = sqlite3.connect(self.path)
        con.execute('ALTER TABLE mysafetydetail_other ADD COLUMN "옛_열" TEXT')
        con.execute('UPDATE mysafetydetail_other SET "옛_열" = \'남은 값\'')
        con.commit()
        con.close()
        before_tables, before_rows = self._tables(), self._q("SELECT ID FROM mysafety ORDER BY ID")
        with self.assertRaises(database.MigrationVerifyFailed) as ctx:
            database.migrate_schema(self.engine, str(self.backups))
        self.assertIn("mysafetydetail_other.옛_열", str(ctx.exception))
        self._assert_untouched_after(before_tables, before_rows)

    def test_orphan_menu_rows_are_preserved_in_sync_meta(self):
        """신고가 없는 옛 메뉴 행은 옮길 신고 행이 없지만 버리지 않는다 — sync_meta 에 그대로 남는다(Astra 재검수 #2)."""
        con = sqlite3.connect(self.path)
        con.execute("INSERT INTO mysafety_entry_value (ID, entry_value) VALUES ('ORPHAN-1', '생활불편 신고-쓰레기, 폐기물')")
        con.execute("INSERT INTO mysafety_entry_value (ID, entry_value) VALUES ('ORPHAN-2', '안전신고-기타 안전신고')")
        con.commit()
        con.close()
        info = database.migrate_schema(self.engine, str(self.backups))
        self.assertEqual(info["orphan_entry_values"], 2)
        kept = self._q(f"SELECT value FROM mysafety_sync_meta WHERE key = '{database.ORPHAN_ENTRY_VALUES_META_KEY}'")
        self.assertEqual(json.loads(kept[0][0]), [["ORPHAN-1", "생활불편 신고-쓰레기, 폐기물"], ["ORPHAN-2", "안전신고-기타 안전신고"]])
        self.assertEqual(self._q("SELECT count(*) FROM mysafety_report WHERE ID LIKE 'ORPHAN-%'")[0][0], 0,
                         "신고 행을 지어내지 않는다")

    def test_no_orphans_leaves_sync_meta_untouched(self):
        database.migrate_schema(self.engine, str(self.backups))
        self.assertEqual(self._q(f"SELECT count(*) FROM mysafety_sync_meta "
                                 f"WHERE key = '{database.ORPHAN_ENTRY_VALUES_META_KEY}'")[0][0], 0)

    def test_v4_db_is_reset_but_v5_is_left_for_migrate_schema(self):
        """버전 5 미만은 reset_legacy_database(백업 뒤 비움) 대상, 버전 5는 그 대상이 아니다(migrate_schema 가 처리)."""
        with self.engine.begin() as conn:
            conn.execute(text("PRAGMA user_version = 4"))
        self.assertTrue(database.needs_legacy_reset(self.engine))
        info = database.reset_legacy_database(self.engine, str(self.backups))
        self.assertIsNotNone(info)
        self.assertEqual(info["from_version"], 4)
        self.assertEqual(database.get_schema_version(self.engine), database.SCHEMA_VERSION)

        path5 = Path(self._dir.name) / "v5_only.db"
        _build_v5_db(path5)
        engine5 = create_engine(f"sqlite:///{path5}")
        self.addCleanup(engine5.dispose)
        self.assertFalse(database.needs_legacy_reset(engine5))
        backups5 = Path(self._dir.name) / "backups5"
        self.assertIsNone(database.reset_legacy_database(engine5, str(backups5)))
        self.assertEqual(database.get_schema_version(engine5), 5, "버전 5는 reset_legacy_database 가 손대지 않는다")
        self.assertFalse(backups5.exists() and any(backups5.iterdir()), "버전 5는 reset 가 백업도 만들지 않는다")


if __name__ == "__main__":
    unittest.main()
