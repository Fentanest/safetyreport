"""상세 원본 저장·스키마 7 업그레이드·예전 DB 가져오기·교환 (2026-10-17 명세 1·2절).

합성 원본에는 신고자 키 5개를 넣어 제거를 확인한다. 실데이터·운영 DB·외부 요청 없음.
"""
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sqlalchemy import create_engine, select

import settings.settings as settings
from core.database import database, models
from core.database.engine import get_engine
from core.storage import exchange, reports_repo
from core.utils import logger, raw_detail


def _envelope(seed=""):
    return {
        "resultCode": "0000",
        "result": {
            "C_NO": 90000001,
            "C_NAME": "홍길동" + seed,
            "C_EMAIL": "a@b.c",
            "C_PHONE": "01012345678",
            "C_PHONE2": "010-1234-5678",
            "C_ID": "fixtureid1",
            "C_NOW": 10,
            "VHRNO": "12가3456",
            "C_A_CONTENTS": "불법주정차 신고합니다." + seed,
            "answers": [
                {
                    "C_MANAGER_TYPE_NM": "수용",
                    "C_MANAGE_CONTENTS": "과태료 40,000원을 부과하였습니다.",
                    "C_MANAGE_MAN": "김담당",
                    "C_MANAGE_MAN_PHONE": "02-123-4567",
                }
            ],
        },
    }


def _raw_row(report_id, envelope, source="crawl", fetched_at="2026-10-17T12:00:00.000Z"):
    canonical = raw_detail.canonical_bytes(envelope)
    return {"ID": report_id, "raw_zlib": raw_detail.compress(canonical),
            "raw_sha256": raw_detail.sha256_hex(canonical), "raw_size": len(canonical),
            "fetched_at": fetched_at, "source": source}


def _downgrade_to_v6(path):
    """지금 스키마로 만든 DB 를 버전 6 모양으로 되돌린다(원본 표 삭제 + 버전 6)."""
    con = sqlite3.connect(path)
    try:
        con.execute("DROP TABLE IF EXISTS mysafety_report_detail_raw")
        con.execute("PRAGMA user_version = 6")
        con.commit()
    finally:
        con.close()


class RawDetailMigrationTests(unittest.TestCase):
    def setUp(self):
        logger.LoggerFactory.create_logger(mode="crawl")
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.path = Path(self._dir.name) / "v6.db"
        self.backups = Path(self._dir.name) / "backups"

    def _seed_v6(self):
        from scripts.dev import fixture_server

        engine = create_engine(f"sqlite:///{self.path}")
        self.addCleanup(engine.dispose)
        fixture_server.seed_engine(engine)
        engine.dispose()
        _downgrade_to_v6(self.path)
        return create_engine(f"sqlite:///{self.path}")

    def _version(self, path=None):
        con = sqlite3.connect(str(path or self.path))
        try:
            return con.execute("PRAGMA user_version").fetchone()[0]
        finally:
            con.close()

    def _tables(self, path=None):
        con = sqlite3.connect(str(path or self.path))
        try:
            return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            con.close()

    def test_6_to_7_keeps_data_creates_backup_and_table(self):
        engine = self._seed_v6()
        with engine.begin() as conn:
            conn.execute(models.report_table.update().where(models.report_table.c.ID == "90000001")
                         .values(처리내용="보존 확인용 처리내용"))
        info = database.migrate_schema(engine, str(self.backups))
        self.assertEqual(self._version(), 7)
        self.assertEqual(info["from_version"], 6)
        backups = sorted(self.backups.glob("before_schema_v7_*.db"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(info["backup"], str(backups[0]))
        # 백업은 올리기 전 그대로(버전 6, 자료 포함)
        self.assertEqual(self._version(backups[0]), 6)
        con = sqlite3.connect(str(backups[0]))
        try:
            kept = con.execute("SELECT 처리내용 FROM mysafety_report WHERE ID = '90000001'").fetchone()[0]
        finally:
            con.close()
        self.assertEqual(kept, "보존 확인용 처리내용")
        # 올린 DB: 자료 그대로, 원본 표는 비어 있음
        with engine.connect() as conn:
            self.assertEqual(conn.execute(select(models.report_table.c.처리내용)
                                           .where(models.report_table.c.ID == "90000001")).scalar(), "보존 확인용 처리내용")
            self.assertEqual(conn.execute(select(models.report_detail_raw_table)).fetchall(), [])
        self.assertIn("mysafety_report_detail_raw", self._tables())
        self.assertIsNone(database.migrate_schema(engine, str(self.backups)))

    def test_6_to_7_failure_changes_nothing(self):
        engine = self._seed_v6()
        before_tables, before_version = self._tables(), self._version()
        with mock.patch.object(database, "_backup_file_db", side_effect=RuntimeError("백업 실패")):
            with self.assertRaises(RuntimeError):
                database.migrate_schema(engine, str(self.backups))
        self.assertEqual(self._version(), before_version)
        self.assertEqual(self._tables(), before_tables)
        self.assertNotIn("mysafety_report_detail_raw", self._tables())

    def test_5_to_6_to_7_chain(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "test_storage_migration",
            Path(__file__).resolve().parent / "test_storage_migration.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _build_v5_db = module._build_v5_db

        path = Path(self._dir.name) / "v5.db"
        _build_v5_db(path)
        engine = create_engine(f"sqlite:///{path}")
        self.addCleanup(engine.dispose)
        info = database.migrate_schema(engine, str(self.backups))
        self.assertEqual(info["from_version"], 5)
        self.assertIn("raw_upgrade", info)
        self.assertIsNotNone(info["raw_upgrade"])
        con = sqlite3.connect(str(path))
        try:
            version = con.execute("PRAGMA user_version").fetchone()[0]
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            reports = con.execute("SELECT count(*) FROM mysafety_report").fetchone()[0]
        finally:
            con.close()
        self.assertEqual(version, 7)
        self.assertIn("mysafety_report_detail_raw", tables)
        self.assertEqual(reports, 6)
        self.assertTrue(sorted(self.backups.glob("before_schema_v7_*.db")))


class RawDetailSaveTests(unittest.TestCase):
    def setUp(self):
        logger.LoggerFactory.create_logger(mode="crawl")
        get_engine().dispose()
        for ext in ("", "-wal", "-shm"):
            if os.path.exists(settings.db_path + ext):
                os.remove(settings.db_path + ext)
        from scripts.dev import fixture_server

        fixture_server.seed_engine(get_engine())

    def tearDown(self):
        get_engine().dispose()

    def _row(self, record_id):
        with get_engine().connect() as conn:
            found = conn.execute(select(models.report_detail_raw_table)
                                 .where(models.report_detail_raw_table.c.ID == record_id)).mappings().first()
            return dict(found) if found else None

    def _save_with_envelope(self, record_id="90000001", envelope=None, **detail):
        envelope = envelope if envelope is not None else _envelope()
        canonical = raw_detail.canonical_bytes(envelope)
        base = {}
        with get_engine().connect() as conn:
            found = conn.execute(select(models.report_table)
                                 .where(models.report_table.c.ID == record_id)).mappings().first()
            if found:
                base = {c: found[c] for c in reports_repo.DETAIL_SITE_COLUMNS}
        base.update(detail)
        rec = reports_repo.CrawledDetail(
            id=record_id, category="traffic", detail={"ID": record_id, **base},
            raw_canonical=canonical, raw_sha256=raw_detail.sha256_hex(canonical),
            raw_size=len(canonical), raw_source="crawl")
        return reports_repo.save_crawled(get_engine(), [rec]), canonical

    def test_crawled_detail_upserts_raw_and_writes_file(self):
        _, canonical = self._save_with_envelope(처리내용="원본 저장 확인")
        row = self._row("90000001")
        self.assertIsNotNone(row)
        self.assertEqual(row["raw_sha256"], raw_detail.sha256_hex(canonical))
        self.assertEqual(row["raw_size"], len(canonical))
        self.assertEqual(row["source"], "crawl")
        self.assertTrue(row["fetched_at"].endswith("Z"))
        # BLOB 해제+검증이 되고 신고자 키 5개는 빠져 있다
        payload = raw_detail.verify(row["raw_zlib"], row["raw_sha256"])
        self.assertEqual(payload, canonical)
        stored = json.loads(payload.decode("utf-8"))
        for key in raw_detail.REPORTER_KEYS:
            self.assertNotIn(key, stored["result"])
        self.assertEqual(stored["result"]["answers"][0]["C_MANAGE_MAN"], "김담당")
        # 원본 파일: 들여쓰기 2칸 JSON, 신고자 키 없음
        path = Path(settings.raw_details_dir) / "90000001.json"
        self.assertTrue(path.is_file())
        text = path.read_text(encoding="utf-8")
        self.assertEqual(json.loads(text), stored)
        self.assertIn('\n  "result"', text)

    def test_second_save_overwrites_raw(self):
        self._save_with_envelope()
        first = self._row("90000001")
        self._save_with_envelope(envelope=_envelope(seed="바뀜"))
        second = self._row("90000001")
        self.assertNotEqual(first["raw_sha256"], second["raw_sha256"])
        self.assertEqual(second["raw_sha256"], raw_detail.sha256_hex(raw_detail.canonical_bytes(_envelope(seed="바뀜"))))
        with get_engine().connect() as conn:
            count = conn.execute(select(models.report_detail_raw_table)
                                 .where(models.report_detail_raw_table.c.ID == "90000001")).fetchall()
        self.assertEqual(len(count), 1)

    def test_save_without_raw_leaves_the_table_untouched(self):
        base = {}
        with get_engine().connect() as conn:
            found = conn.execute(select(models.report_table)
                                 .where(models.report_table.c.ID == "90000001")).mappings().first()
            base = {c: found[c] for c in reports_repo.DETAIL_SITE_COLUMNS}
        rec = reports_repo.CrawledDetail(id="90000001", category="traffic",
                                         detail={"ID": "90000001", **base})
        reports_repo.save_crawled(get_engine(), [rec])
        self.assertIsNone(self._row("90000001"))

    def test_reset_drops_raw_rows(self):
        import start

        self._save_with_envelope()
        self.assertIsNotNone(self._row("90000001"))
        start._prepare_database(get_engine(), reset=True)
        self.assertIsNone(self._row("90000001"))


class RawDetailRestoreTests(unittest.TestCase):
    def setUp(self):
        logger.LoggerFactory.create_logger(mode="crawl")
        default_gate = mock.patch("services.crawl_control._check_crawl_allowed",
                                  side_effect=RuntimeError("test default: crawl not allowed"))
        default_gate.start()
        self.addCleanup(default_gate.stop)
        get_engine().dispose()
        for ext in ("", "-wal", "-shm"):
            if os.path.exists(settings.db_path + ext):
                os.remove(settings.db_path + ext)
        from scripts.dev import fixture_server

        fixture_server.seed_engine(get_engine())
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.upload = Path(self._tmp.name) / "upload.db"
        for target in ("core.storage.exchange._current_kakao_id", "services.account_data.file_owner"):
            owner_patch = mock.patch(target, return_value="910001")
            owner_patch.start()
            self.addCleanup(owner_patch.stop)

    def tearDown(self):
        get_engine().dispose()

    def _server_rows(self):
        with get_engine().connect() as conn:
            return {r["ID"]: dict(r) for r in conn.execute(
                select(models.report_detail_raw_table)).mappings()}

    def test_server_v6_backup_is_upgraded_on_restore(self):
        exchange._copy_sqlite(settings.db_path, str(self.upload))
        _downgrade_to_v6(self.upload)
        backup, _ = exchange.restore(str(self.upload), "server")
        self.assertTrue(backup and os.path.exists(backup))
        self.assertEqual(database.get_schema_version(get_engine()), 7)
        # 올려 받은 DB 의 원본 표는 비어 있다
        self.assertEqual(self._server_rows(), {})

    def test_mobile_v17_is_upgraded_with_empty_raw(self):
        con = sqlite3.connect(self.upload)
        con.execute("PRAGMA user_version = 17")
        con.executescript(
            """
            CREATE TABLE reports (ID TEXT PRIMARY KEY, 상태 TEXT, 신고번호 TEXT, 신고명 TEXT, 신고일 TEXT, 만족도조사여부 TEXT, 별점 INTEGER,
              별점사유 TEXT, 감시목록 TEXT, 처리상태 TEXT, 처리기관 TEXT, 담당자 TEXT, 위반장소 TEXT, 종결여부 TEXT, category TEXT, entry_value TEXT,
              raw_content TEXT, synced_at INTEGER);
            CREATE TABLE sync_meta (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE report_raw (ID TEXT PRIMARY KEY, raw_content TEXT NOT NULL DEFAULT '', raw_type TEXT NOT NULL DEFAULT '', saved_at INTEGER);
            """
        )
        con.execute("INSERT INTO reports VALUES ('m1','수용','SPP-2609-9000011','신호위반','2026-09-01','참여 완료',NULL,NULL,'N','수용','기관','','서울 강서구 1','Y','traffic','자동차·교통위반-신호위반','',NULL)")
        con.commit()
        con.close()
        _, count = exchange.restore(str(self.upload), "mobile")
        self.assertEqual(count, 1)
        self.assertEqual(self._server_rows(), {})

    def test_mobile_v18_raw_round_trips_byte_identical(self):
        envelope = _envelope()
        want = _raw_row("m1", envelope, source="crawl")
        con = sqlite3.connect(self.upload)
        con.execute("PRAGMA user_version = 18")
        con.executescript(
            """
            CREATE TABLE reports (ID TEXT PRIMARY KEY, 상태 TEXT, 신고번호 TEXT, 신고명 TEXT, 신고일 TEXT, 만족도조사여부 TEXT, 별점 INTEGER,
              별점사유 TEXT, 감시목록 TEXT, 처리상태 TEXT, 처리기관 TEXT, 담당자 TEXT, 위반장소 TEXT, 종결여부 TEXT, category TEXT, entry_value TEXT,
              raw_content TEXT, synced_at INTEGER);
            CREATE TABLE sync_meta (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE report_raw (ID TEXT PRIMARY KEY, raw_content TEXT NOT NULL DEFAULT '', raw_type TEXT NOT NULL DEFAULT '', saved_at INTEGER);
            CREATE TABLE report_detail_raw (ID TEXT PRIMARY KEY, raw_zlib BLOB NOT NULL, raw_sha256 TEXT NOT NULL,
              raw_size INTEGER NOT NULL, fetched_at TEXT NOT NULL, source TEXT NOT NULL);
            """
        )
        con.execute("INSERT INTO reports VALUES ('m1','수용','SPP-2609-9000011','신호위반','2026-09-01','참여 완료',NULL,NULL,'N','수용','기관','','서울 강서구 1','Y','traffic','자동차·교통위반-신호위반','',NULL)")
        con.execute("INSERT INTO report_detail_raw VALUES (?, ?, ?, ?, ?, ?)",
                    (want["ID"], want["raw_zlib"], want["raw_sha256"], want["raw_size"], want["fetched_at"], want["source"]))
        con.commit()
        con.close()
        exchange.restore(str(self.upload), "mobile")
        got = self._server_rows()["m1"]
        for column in ("ID", "raw_zlib", "raw_sha256", "raw_size", "fetched_at", "source"):
            with self.subTest(column=column):
                self.assertEqual(got[column], want[column])
                self.assertIs(type(got[column]), type(want[column]))
        # 서버→모바일 방향(같은 열 이름 그대로)도 바이트까지 같다
        mobile_row = {c: got[c] for c in ("ID", "raw_zlib", "raw_sha256", "raw_size", "fetched_at", "source")}
        self.assertEqual(mobile_row, want)

    def test_versions_outside_the_supported_range_are_refused(self):
        for version in (0, 1, 5):
            with self.subTest(kind="server", version=version):
                path = Path(self._tmp.name) / f"server_v{version}.db"
                exchange._copy_sqlite(settings.db_path, str(path))
                con = sqlite3.connect(path)
                con.execute(f"PRAGMA user_version = {version}")
                con.commit()
                con.close()
                with self.assertRaises(exchange.LegacyDatabaseRefused):
                    exchange.restore(str(path), "server")
        for version in (exchange.MOBILE_SCHEMA_VERSION + 1,):
            with self.subTest(kind="server-newer", version=version):
                path = Path(self._tmp.name) / f"server_v{version}.db"
                exchange._copy_sqlite(settings.db_path, str(path))
                con = sqlite3.connect(path)
                con.execute(f"PRAGMA user_version = {version}")
                con.commit()
                con.close()
                with self.assertRaises(exchange.RestoreRefused):
                    exchange.restore(str(path), "server")
        for version in (0, 11, 16, exchange.MOBILE_SCHEMA_VERSION + 1):
            with self.subTest(kind="mobile", version=version):
                path = Path(self._tmp.name) / f"mobile_v{version}.db"
                con = sqlite3.connect(path)
                con.execute(f"PRAGMA user_version = {version}")
                con.execute("CREATE TABLE reports (ID TEXT PRIMARY KEY, category TEXT)")
                con.execute("CREATE TABLE sync_meta (key TEXT PRIMARY KEY, value TEXT)")
                con.commit()
                con.close()
                with self.assertRaises(exchange.LegacyDatabaseRefused):
                    exchange.restore(str(path), "mobile")
        self.assertEqual([p for p in os.listdir(settings.datapath) if p.startswith(".restore_staging_")], [])


if __name__ == "__main__":
    unittest.main()
