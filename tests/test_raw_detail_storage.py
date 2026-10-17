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

import pandas as pd
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
        """서버→모바일→서버 원본 왕복. 서버→모바일 표 복사는 SQL 로 BLOB 바이트를 그대로 옮긴다
        (재인코딩·재압축 없음 — 실동작의 Dart importFromServerDb 대역. Dart 까지 포함한 전체 왕복은
        scripts/dev/db_roundtrip_check.py --mobile-repo 로 확인한다: flutter·모바일 레포가 필요해
        이 저장소 unittest 에서는 실행할 수 없다). 모바일→서버는 실제 exchange.restore 경로로 확인한다."""
        from services import db_backup

        for record_id, seed in (("90000001", ""), ("90000002", "바뀜")):
            with get_engine().begin() as conn:
                conn.execute(models.report_detail_raw_table.insert().values(
                    **_raw_row(record_id, _envelope(seed=seed))))
        exported = db_backup.export_clean_db()  # 실제 서버 내보내기 경로
        self.addCleanup(lambda: os.path.exists(exported) and os.remove(exported))
        con = sqlite3.connect(f"file:{exported}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            want = {r["ID"]: dict(r) for r in
                    con.execute("SELECT ID, raw_zlib, raw_sha256, raw_size, fetched_at, source"
                                " FROM mysafety_report_detail_raw")}
        finally:
            con.close()
        self.assertEqual(set(want), {"90000001", "90000002"})

        mobile = Path(self._tmp.name) / "m18.db"
        con = sqlite3.connect(mobile)
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
        con.execute("INSERT INTO reports VALUES ('90000001','답변완료','SPP-2512-9000001','신호위반','2025-12-30 23:40:00','답변 대기',5,NULL,'N','수용','기관','김담당','서울 강서구 1','Y','traffic','자동차·교통위반-신호위반','',NULL)")
        con.execute("INSERT INTO reports VALUES ('90000002','답변완료','SPP-2601-9000002','중앙선 침범','2026-01-03 08:10:00','답변 대기',4,NULL,'N','수용','기관','김담당','서울 강서구 2','Y','traffic','자동차·교통위반-신호위반','',NULL)")
        # 서버 내보내기 파일에서 원본 표를 SQL 로 그대로 복사 — BLOB 바이트를 파이썬에서 다시 만들지 않는다.
        con.execute("ATTACH DATABASE ? AS srv", (exported,))
        con.execute("INSERT INTO report_detail_raw (ID, raw_zlib, raw_sha256, raw_size, fetched_at, source)"
                    " SELECT ID, raw_zlib, raw_sha256, raw_size, fetched_at, source"
                    " FROM srv.mysafety_report_detail_raw")
        con.commit()
        con.close()
        exchange.restore(str(mobile), "mobile")  # 실제 모바일→서버 복원 경로
        got = self._server_rows()
        self.assertEqual(set(got), {"90000001", "90000002"})
        for record_id, expected in want.items():
            with self.subTest(record_id=record_id):
                row = got[record_id]
                for column in ("ID", "raw_zlib", "raw_sha256", "raw_size", "fetched_at", "source"):
                    self.assertEqual(row[column], expected[column])
                    self.assertIs(type(row[column]), type(expected[column]))
                # 해제+검증이 되면 BLOB·sha 가 원본 정규 바이트와 같은 것이다
                self.assertEqual(raw_detail.verify(row["raw_zlib"], row["raw_sha256"]),
                                 raw_detail.canonical_bytes(_envelope(seed="" if record_id == "90000001" else "바뀜")))

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


def _min_details():
    """build_detail_result 가 요구하는 최소 상세 dict(합성, 외부 요청 없음)."""
    return {
        "processing_status": "수용",
        "car_number": "",
        "violation_law": "",
        "penalty_amount": "",
        "penalty_points": "",
        "processing_agency": "",
        "person_in_charge": "",
        "response_date": "",
        "occurrence_date": "",
        "occurrence_time": "",
        "violation_location": "",
        "processing_finish": "Y",
        "report_content": "",
        "processing_content": "",
        "map_image": "",
        "attached_photos": "",
        "attachment_files": "",
        "entry_value": "자동차·교통위반-신호위반",
    }


class RawDetailFileSafetyTests(unittest.TestCase):
    """Sol 독립 검수 1: 신고 ID 검증 없이 파일 경로에 넣으면 raw_details 밖에 쓸 수 있다."""

    def setUp(self):
        logger.LoggerFactory.create_logger(mode="crawl")

    def test_traversal_ids_are_never_written(self):
        bad_ids = ["../../outside", "..\\outside", "/abs/path", "a/b", "a.json",
                   "", ".", "..", "90000001.json", "id with space", "한글/../..",
                   None, b"90000001", "a" * 65]
        for bad in bad_ids:
            with self.subTest(bad=bad):
                self.assertIsNone(raw_detail.write_raw_file(bad, {"result": {}}))
        self.assertFalse((Path(settings.datapath).parent / "outside").exists())
        self.assertFalse((Path(settings.datapath) / "outside").exists())

    def test_valid_id_writes_only_under_raw_details(self):
        target = Path(settings.raw_details_dir) / "90000001.json"
        if target.exists():
            target.unlink()
        try:
            path = raw_detail.write_raw_file("90000001", {"result": {"C_NO": 1}})
            self.assertIsNotNone(path)
            self.assertEqual(os.path.realpath(path), os.path.realpath(str(target)))
            self.assertEqual(json.loads(Path(path).read_text(encoding="utf-8")),
                             {"result": {"C_NO": 1}})
        finally:
            if target.exists():
                target.unlink()


#: makedirs 모의 실패 시험의 side_effect 가 위임할 진짜 함수(패치 전에 잡아둔다).
_REAL_MAKEDIRS = os.makedirs


class RawDetailFileFailureTests(unittest.TestCase):
    """Sol 독립 검수 2: 폴더 생성 실패 시 tmp 참조 UnboundLocalError → DB 는 저장됐는데 실패로 보고."""

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

    @staticmethod
    def _boom_raw_details_only(path, *args, **kwargs):
        if os.fspath(path).endswith("raw_details"):
            raise OSError("모의 폴더 생성 실패")
        return _REAL_MAKEDIRS(path, *args, **kwargs)

    def _crawled_record(self, record_id="90000001", envelope=None):
        envelope = envelope if envelope is not None else _envelope()
        canonical = raw_detail.canonical_bytes(envelope)
        base = {}
        with get_engine().connect() as conn:
            found = conn.execute(select(models.report_table)
                                 .where(models.report_table.c.ID == record_id)).mappings().first()
            if found:
                base = {c: found[c] for c in reports_repo.DETAIL_SITE_COLUMNS}
        return reports_repo.CrawledDetail(
            id=record_id, category="traffic", detail={"ID": record_id, **base},
            raw_canonical=canonical, raw_sha256=raw_detail.sha256_hex(canonical),
            raw_size=len(canonical), raw_source="crawl"), canonical

    def test_write_returns_none_when_directory_cannot_be_created(self):
        with mock.patch("os.makedirs", side_effect=self._boom_raw_details_only):
            self.assertIsNone(raw_detail.write_raw_file("90000001", {"result": {}}))

    def test_save_reports_success_despite_file_failure(self):
        rec, canonical = self._crawled_record()
        with mock.patch("os.makedirs", side_effect=self._boom_raw_details_only):
            result = reports_repo.save_crawled(get_engine(), [rec])
        self.assertEqual(result.saved, 1)
        self.assertEqual(result.failed, [])
        with get_engine().connect() as conn:
            row = conn.execute(select(models.report_detail_raw_table)
                               .where(models.report_detail_raw_table.c.ID == "90000001")).mappings().first()
        self.assertIsNotNone(row)
        self.assertEqual(row["raw_sha256"], raw_detail.sha256_hex(canonical))


def _fail_raw_table_create():
    """6→7 단계의 새 표 생성을 모의 실패시킨다(백업 뒤 표 만들기에서 실패)."""
    import sqlalchemy.schema

    real = sqlalchemy.schema.CreateTable

    def _boom(table, *args, **kwargs):
        if getattr(table, "name", "") == "mysafety_report_detail_raw":
            raise RuntimeError("모의 6→7 실패")
        return real(table, *args, **kwargs)

    return mock.patch.object(sqlalchemy.schema, "CreateTable", side_effect=_boom)


class RawDetailStepRetryTests(unittest.TestCase):
    """Sol 독립 검수 3: 5→6 커밋 뒤 6→7 실패 → 버전 6 유지 → 다음 기동에 6→7 재시도."""

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

    def _report_processing(self, engine, record_id="90000001"):
        with engine.connect() as conn:
            return conn.execute(select(models.report_table.c.처리내용)
                                .where(models.report_table.c.ID == record_id)).scalar()

    def test_6_to_7_step_failure_stays_at_6_and_retries(self):
        engine = self._seed_v6()
        with engine.begin() as conn:
            conn.execute(models.report_table.update().where(models.report_table.c.ID == "90000001")
                         .values(처리내용="6단계 실패 모의 보존값"))
        with _fail_raw_table_create():
            with self.assertLogs(logger.LoggerFactory.logbot, level="ERROR"):
                with self.assertRaises(RuntimeError):
                    database.migrate_schema(engine, str(self.backups))
        # 뒤 단계 실패: 마지막 완료 버전(6)에 머물고 자료는 그대로
        self.assertEqual(self._version(), 6)
        self.assertEqual(self._report_processing(engine), "6단계 실패 모의 보존값")
        con = sqlite3.connect(str(self.path))
        try:
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            con.close()
        self.assertNotIn("mysafety_report_detail_raw", tables)
        # 다음 기동 재시도: 6→7 성공, 자료 그대로
        info = database.migrate_schema(engine, str(self.backups))
        self.assertEqual(info["from_version"], 6)
        self.assertEqual(self._version(), 7)
        self.assertEqual(self._report_processing(engine), "6단계 실패 모의 보존값")

    def test_5_chain_failure_after_6_commit_stays_at_6(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "test_storage_migration",
            Path(__file__).resolve().parent / "test_storage_migration.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        path = Path(self._dir.name) / "v5.db"
        module._build_v5_db(path)
        engine = create_engine(f"sqlite:///{path}")
        self.addCleanup(engine.dispose)
        with _fail_raw_table_create():
            with self.assertLogs(logger.LoggerFactory.logbot, level="ERROR"):
                with self.assertRaises(RuntimeError):
                    database.migrate_schema(engine, str(self.backups))
        # 5→6 은 커밋됐고 6→7 만 실패: 버전 6, 옮긴 자료 그대로
        con = sqlite3.connect(str(path))
        try:
            version = con.execute("PRAGMA user_version").fetchone()[0]
            reports = con.execute("SELECT count(*) FROM mysafety_report").fetchone()[0]
        finally:
            con.close()
        self.assertEqual(version, 6)
        self.assertEqual(reports, 6)
        # 다음 기동 재시도: 6→7 성공
        info = database.migrate_schema(engine, str(self.backups))
        self.assertEqual(info["from_version"], 6)
        con = sqlite3.connect(str(path))
        try:
            version = con.execute("PRAGMA user_version").fetchone()[0]
            reports = con.execute("SELECT count(*) FROM mysafety_report").fetchone()[0]
        finally:
            con.close()
        self.assertEqual(version, 7)
        self.assertEqual(reports, 6)


class RawDetailSourceTests(unittest.TestCase):
    """Sol 독립 검수 4: raw_source 가 항상 'crawl' 로 기록된다."""

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

    def _save_with_source(self, record_id="90000001", source="crawl", *, omit_source=False):
        from core.crawler import detail_pipeline

        extra = {} if omit_source else {"raw_source": source}
        tup = detail_pipeline.build_detail_result(
            record_id, _min_details(), progress_status="답변완료",
            satisfaction_client=None, satisfaction_fetcher=lambda *a: (None, None),
            raw_envelope=_envelope(), **extra)
        rec = reports_repo.CrawledDetail.from_legacy_tuple(tup)
        return reports_repo.save_crawled(get_engine(), [rec])

    def _row_source(self, record_id="90000001"):
        with get_engine().connect() as conn:
            return conn.execute(select(models.report_detail_raw_table.c.source)
                                .where(models.report_detail_raw_table.c.ID == record_id)).scalar()

    def test_rebuild_source_is_recorded(self):
        result = self._save_with_source(source="rebuild")
        self.assertEqual(result.saved, 1)
        self.assertEqual(self._row_source(), "rebuild")

    def test_notification_source_is_recorded(self):
        result = self._save_with_source(source="notification")
        self.assertEqual(result.saved, 1)
        self.assertEqual(self._row_source(), "notification")

    def test_default_source_is_crawl(self):
        result = self._save_with_source(omit_source=True)
        self.assertEqual(result.saved, 1)
        self.assertEqual(self._row_source(), "crawl")

    def test_crawl_details_forwards_raw_source(self):
        from core.crawler import crawldetail_api

        details = _min_details()
        for source, expected in (("rebuild", "rebuild"), (None, "crawl")):
            with self.subTest(source=source):
                kwargs = {} if source is None else {"raw_source": source}
                with mock.patch.object(crawldetail_api.direct_login, "make_authorized_session",
                                       return_value=(object(), None)), \
                        mock.patch.object(crawldetail_api, "get_authorized_json",
                                          return_value=(_envelope(), object())), \
                        mock.patch("services.parser.parse_json_details", return_value=dict(details)):
                    item = next(crawldetail_api.crawl_details(report_ids=["90000001"], **kwargs))
                self.assertEqual(item[9]["raw_source"], expected)

    def _capturing_crawl_details(self, captured):
        from core.crawler import detail_pipeline

        def fake(driver=None, report_ids=None, browser_fallback=False, status_sink=None,
                 raw_source="crawl", **kwargs):
            captured["raw_source"] = raw_source
            for rid in report_ids or []:
                yield detail_pipeline.build_detail_result(
                    rid, _min_details(), progress_status="답변완료",
                    satisfaction_client=None, satisfaction_fetcher=lambda *a: (None, None),
                    raw_envelope=_envelope(), raw_source=raw_source)

        return fake

    def test_queue_crawl_records_notification_source(self):
        import start

        captured = {}
        queue = Path(tempfile.mkdtemp()) / "mobile_queue.txt"
        queue.write_text("SPP-2512-9000001\n", encoding="utf-8")
        self.addCleanup(lambda: queue.exists() and queue.unlink())
        with mock.patch.object(start.crawldetail_api, "crawl_details",
                               side_effect=self._capturing_crawl_details(captured)):
            start._run_crawling_process(
                None, get_engine(),
                start.CrawlOptions(queue_file=str(queue), page_range=None, force=False, rebuild=None), False)
        self.assertEqual(captured.get("raw_source"), "notification")
        self.assertEqual(self._row_source(), "notification")

    def test_normal_crawl_records_crawl_source(self):
        import start

        captured = {}
        titles = [pd.DataFrame({"ID": ["90000001"], "신고번호": ["SPP-2512-9000001"],
                                "상태": ["진행"], "신고명": ["신호위반"],
                                "신고일": ["2025-12-30 23:40:00"]})]

        def fake_titles(driver=None, browser_fallback=False, progress=None, **kwargs):
            progress["list_ok"] = True
            return titles, 1

        with mock.patch.object(start.crawltitle_api, "crawl_titles", side_effect=fake_titles), \
                mock.patch.object(start.crawldetail_api, "crawl_details",
                                  side_effect=self._capturing_crawl_details(captured)), \
                mock.patch.object(start.database, "get_pending_detail_ids", return_value=["90000001"]):
            start._run_crawling_process(
                None, get_engine(),
                start.CrawlOptions(queue_file=None, page_range=None, force=True, rebuild=None), False)
        self.assertEqual(captured.get("raw_source"), "crawl")
        self.assertEqual(self._row_source(), "crawl")

    def test_rebuild_crawl_records_rebuild_source(self):
        import start

        captured = {}
        titles = [pd.DataFrame({"ID": ["90000001"], "신고번호": ["SPP-2512-9000001"],
                                "상태": ["진행"], "신고명": ["신호위반"],
                                "신고일": ["2025-12-30 23:40:00"]})]

        def fake_titles(driver=None, browser_fallback=False, progress=None, **kwargs):
            progress["list_ok"] = True
            return titles, 1

        with mock.patch.object(start.crawltitle_api, "crawl_titles", side_effect=fake_titles), \
                mock.patch.object(start.crawldetail_api, "crawl_details",
                                  side_effect=self._capturing_crawl_details(captured)), \
                mock.patch("services.community_rebuild._get_job", return_value={"state": "running"}), \
                mock.patch("services.community_rebuild.register_list"), \
                mock.patch("services.community_rebuild.pending_detail_ids", return_value=["90000001"]), \
                mock.patch("services.community_rebuild.record_item"), \
                mock.patch("services.community_rebuild.note_counts", create=True), \
                mock.patch("services.community_rebuild.mark_paused_auth", create=True):
            start._run_rebuild_process(None, get_engine(), {"rebuild": "run-test"}, False)
        self.assertEqual(captured.get("raw_source"), "rebuild")
        self.assertEqual(self._row_source(), "rebuild")


if __name__ == "__main__":
    unittest.main()
