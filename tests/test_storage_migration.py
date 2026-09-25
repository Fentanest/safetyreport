"""구버전 서버 DB → 현재 스키마 업그레이드 (저장 계층 재설계 R0).

견본 `tests/fixtures/db/server-2026-05-16.schema.sql` 은 2026-05-16 운영 DB 의 표 구조만 뽑은 것이다(데이터 없음).
합성 행을 넣고 `upgrade_schema` 를 돌린 뒤: 계약과 스키마가 일치하는지, 기존 값이 그대로인지, 새 열은 비어 있는지 확인한다.
"""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

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


class ServerMigrationTests(unittest.TestCase):
    def setUp(self):
        logger.LoggerFactory.create_logger(mode="crawl")
        self._dir = tempfile.TemporaryDirectory()
        self.path = Path(self._dir.name) / "old.db"
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
        con.commit()
        con.close()
        self.engine = create_engine(f"sqlite:///{self.path}")

    def tearDown(self):
        self.engine.dispose()
        self._dir.cleanup()

    def _columns(self, table):
        con = sqlite3.connect(self.path)
        try:
            return [r[1] for r in con.execute(f'pragma table_info("{table}")')]
        finally:
            con.close()

    def test_old_db_is_backed_up_before_upgrade_and_only_once(self):
        """업데이트 직후 첫 기동: 스키마를 올리기 전 DB 사본(옛 버전 그대로)을 남기고, 다음 기동부터는 만들지 않는다."""
        backups = Path(self._dir.name) / "backups"
        database.upgrade_schema(self.engine, backup_dir=str(backups))
        files = sorted(backups.glob("before_schema_v0_*.db"))
        self.assertEqual(len(files), 1)
        con = sqlite3.connect(files[0])
        try:
            self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], 0)  # 바꾸기 전 그대로
            self.assertEqual(con.execute("SELECT 신고번호 FROM mysafety").fetchall(), [("SPP-2605-7000001",)])
            self.assertEqual(con.execute("SELECT count(*) FROM sqlite_master WHERE name = 'mysafety_report_override'").fetchone()[0], 0)
        finally:
            con.close()
        self.assertEqual(database.get_schema_version(self.engine), database.SCHEMA_VERSION)
        database.upgrade_schema(self.engine, backup_dir=str(backups))
        self.assertEqual(len(list(backups.glob("before_schema_v*.db"))), 1)

    def test_pre_upgrade_backups_keep_only_the_latest(self):
        backups = Path(self._dir.name) / "backups"
        backups.mkdir()
        for i in range(7):
            (backups / f"before_schema_v0_2026010{i}_000000.db").write_bytes(b"")
        (backups / "data_before_restore_20260101_000000.db").write_bytes(b"")  # 다른 백업은 건드리지 않는다
        database.upgrade_schema(self.engine, backup_dir=str(backups))
        kept = sorted(p.name for p in backups.glob("before_schema_v*.db"))
        self.assertEqual(len(kept), database.PRE_UPGRADE_BACKUP_KEEP)
        self.assertNotIn("before_schema_v0_20260100_000000.db", kept)
        self.assertTrue((backups / "data_before_restore_20260101_000000.db").exists())

    def test_new_empty_db_is_not_backed_up(self):
        backups = Path(self._dir.name) / "backups"
        engine = create_engine(f"sqlite:///{Path(self._dir.name) / 'new.db'}")
        database.upgrade_schema(engine, backup_dir=str(backups))
        self.assertFalse(backups.exists() and any(backups.iterdir()))
        engine.dispose()

    def test_fixture_is_really_old(self):
        self.assertNotIn("주소정규화", self._columns("mysafetydetail_traffic"))
        self.assertEqual(self._columns("mysafety_geocode_cache"), [])

    def test_upgrade_reaches_contract_and_keeps_values(self):
        database.upgrade_schema(self.engine)
        report = next(e for e in CONTRACT["entities"] if e["entity"] == "report")
        for placement, tables in (("detail", report["server_tables"]["detail"]), ("merge", report["server_tables"]["merge"])):
            expected = {c["name"] for c in report["columns"] if placement in c["server"]}
            for table in tables:
                with self.subTest(table=table):
                    self.assertEqual(set(self._columns(table)), expected)
        self.assertTrue(self._columns("mysafety_geocode_cache"))

        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        detail = dict(con.execute("SELECT * FROM mysafetydetail_traffic WHERE ID='70000001'").fetchone())
        merge = dict(con.execute("SELECT * FROM mysafetymerge_traffic WHERE ID='70000001'").fetchone())
        con.close()
        for key, value in DETAIL.items():
            with self.subTest(column=key):
                self.assertEqual(detail[key], value)
                self.assertEqual(merge[key], value)
        for key in ("상태", "신고번호", "만족도조사여부", "별점"):
            self.assertEqual(merge[key], TITLE[key])
        for key in ("위도", "경도", "사진_첫촬영", "사진_끝촬영", "사진_촬영수"):
            self.assertIsNone(detail[key], key)

    def test_upgrade_sets_schema_version_from_contract(self):
        self.assertEqual(database.get_schema_version(self.engine), 0)
        database.upgrade_schema(self.engine)
        self.assertEqual(database.get_schema_version(self.engine), CONTRACT["schema_version"]["server"])
        self.assertEqual(database.SCHEMA_VERSION, CONTRACT["schema_version"]["server"])
        database.upgrade_schema(self.engine)  # 두 번 돌려도 같다
        self.assertEqual(database.get_schema_version(self.engine), database.SCHEMA_VERSION)

    def test_newer_database_is_refused(self):
        con = sqlite3.connect(self.path)
        con.execute(f"PRAGMA user_version = {database.SCHEMA_VERSION + 1}")
        con.commit()
        con.close()
        with self.assertRaises(RuntimeError):
            database.upgrade_schema(self.engine)
        self.assertNotIn("주소정규화", self._columns("mysafetydetail_traffic"))  # 거부 전에 아무것도 바꾸지 않음

    def test_light_upgrade_for_crawler_applies_versions_and_indexes(self):
        database.upgrade_schema(self.engine, maintenance=False)
        self.assertEqual(database.get_schema_version(self.engine), database.SCHEMA_VERSION)
        con = sqlite3.connect(self.path)
        try:
            indexes = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        finally:
            con.close()
        self.assertTrue({"ix_mysafety_report_number", "ix_detail_traffic_closed", "ix_duplicate_member_report"} <= indexes)


if __name__ == "__main__":
    unittest.main()
