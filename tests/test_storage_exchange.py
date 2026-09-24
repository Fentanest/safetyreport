"""교체 방식 복원과 모바일 → 서버 변환 (저장 계층 재설계 R1, core/storage/exchange.py).

settings.db_path 는 SAFETYREPORT_DATA_DIR 아래 임시 DB 다(테스트 실행 명령이 지정). 운영 data/ 는 건드리지 않는다.
"""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sqlalchemy import select, text

import settings.settings as settings
from core.database import database, models
from core.database.engine import get_engine
from core.storage import exchange
from core.utils import logger
from scripts.dev import fixture_server


def _mobile_db(path: Path, *, watchlist="SPP-2609-9000011", with_override=True):
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE reports (ID TEXT PRIMARY KEY, 상태 TEXT, 신고번호 TEXT, 신고명 TEXT, 신고일 TEXT, 만족도조사여부 TEXT, 별점 INTEGER,
          별점사유 TEXT, 감시목록 TEXT, 처리상태 TEXT, 처리기관 TEXT, 담당자 TEXT, 위반장소 TEXT, 종결여부 TEXT, category TEXT, entry_value TEXT,
          raw_content TEXT, synced_at INTEGER);
        CREATE TABLE sync_meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE report_raw (ID TEXT PRIMARY KEY, raw_content TEXT NOT NULL DEFAULT '', raw_type TEXT NOT NULL DEFAULT '', saved_at INTEGER);
        CREATE TABLE geocode_cache (주소정규화 TEXT PRIMARY KEY, 원본주소 TEXT, 행정구역 TEXT, 위도 REAL, 경도 REAL, 상태 TEXT NOT NULL DEFAULT '',
          source TEXT NOT NULL DEFAULT 'kakao', error_message TEXT, updated_at INTEGER);
        """
    )
    con.execute("INSERT INTO reports VALUES ('m1','수용','SPP-2609-9000011','신호위반','2026-09-01','참여 완료',NULL,NULL,'N','수용','기관','','서울 강서구 1','Y','traffic','자동차·교통위반-신호위반','',NULL)")
    con.execute("INSERT INTO sync_meta VALUES ('last_sync','2026-09-24T10:00:00'), ('watchlist', ?)", (watchlist,))
    con.execute("INSERT INTO geocode_cache VALUES ('앱 주소 1', NULL, NULL, 37.1, 127.1, 'ok', 'kakao', NULL, NULL)")
    if with_override:
        con.execute("CREATE TABLE report_override (ID TEXT NOT NULL, column_name TEXT NOT NULL, value TEXT, updated_at INTEGER NOT NULL, PRIMARY KEY (ID, column_name))")
        con.execute("INSERT INTO report_override VALUES ('m1','처리내용','앱에서 고침',1790000000000)")
    con.commit()
    con.close()


class ExchangeRestoreTests(unittest.TestCase):
    def setUp(self):
        logger.LoggerFactory.create_logger(mode="crawl")
        get_engine().dispose()
        for ext in ("", "-wal", "-shm"):
            if os.path.exists(settings.db_path + ext):
                os.remove(settings.db_path + ext)
        fixture_server.seed_engine(get_engine())
        with get_engine().begin() as conn:
            conn.execute(text("INSERT INTO mysafety_geocode_cache(주소정규화, 상태, source) VALUES ('서버 전용 주소', 'ok', 'kakao')"))
        self._tmp = tempfile.TemporaryDirectory()
        self.upload = Path(self._tmp.name) / "mobile.db"

    def tearDown(self):
        get_engine().dispose()
        self._tmp.cleanup()

    def _count(self, table):
        with get_engine().connect() as conn:
            return len(conn.execute(select(table)).fetchall())

    def test_refused_while_crawling_and_live_db_untouched(self):
        _mobile_db(self.upload)
        before = self._count(models.title_table)
        with mock.patch("services.crawl_manager.crawl_manager.is_crawling", return_value=True):
            with self.assertRaises(exchange.RestoreRefused):
                exchange.restore(str(self.upload), "mobile")
        self.assertEqual(self._count(models.title_table), before)

    def test_broken_upload_leaves_live_db_untouched(self):
        con = sqlite3.connect(self.upload)
        con.execute("CREATE TABLE something_else (x)")
        con.commit()
        con.close()
        before = self._count(models.title_table)
        with self.assertRaises(ValueError):
            exchange.restore(str(self.upload), "mobile")
        self.assertEqual(self._count(models.title_table), before)
        leftovers = [p for p in os.listdir(settings.datapath) if p.startswith(".restore_staging_")]
        self.assertEqual(leftovers, [])

    def test_mobile_restore_keeps_server_only_data_and_derives_watch_flag(self):
        _mobile_db(self.upload)
        admins, keys = self._count(models.admin_users_table), self._count(models.api_keys_table)
        backup, count = exchange.restore(str(self.upload), "mobile")
        self.assertEqual(count, 1)
        self.assertTrue(backup and os.path.exists(backup))
        self.assertEqual(self._count(models.admin_users_table), admins)
        self.assertEqual(self._count(models.api_keys_table), keys)
        with get_engine().connect() as conn:
            caches = {r[0] for r in conn.execute(select(models.geocode_cache_table.c["주소정규화"]))}
            self.assertTrue({"서버 전용 주소", "앱 주소 1"} <= caches)  # 서버 캐시 유지 + 앱 캐시 합침(S-13)
            self.assertEqual(conn.execute(select(models.merge_traffic_table.c["감시목록"])).scalar(), "Y")  # 계산값
            self.assertIsNone(conn.execute(select(models.merge_traffic_table.c["별점사유"])).scalar())  # NULL 보존
            keys_meta = {r[0] for r in conn.execute(select(models.sync_meta_table.c.key))}
            self.assertNotIn("watchlist", keys_meta)  # 서버에는 감시목록 사본을 두지 않음
            self.assertEqual(conn.execute(select(models.report_override_table.c.value)).scalar(), "앱에서 고침")

    def test_old_app_without_override_table_keeps_server_overrides(self):
        with get_engine().begin() as conn:
            conn.execute(models.report_override_table.insert().values(ID="m1", column_name="처리내용", value="서버에서 고침", updated_at=1))
        _mobile_db(self.upload, with_override=False)
        exchange.restore(str(self.upload), "mobile")
        with get_engine().connect() as conn:
            self.assertEqual(conn.execute(select(models.report_override_table.c.value)).scalar(), "서버에서 고침")

    def test_server_restore_refuses_newer_schema_without_touching_live_db(self):
        newer = Path(self._tmp.name) / "newer.db"
        exchange._copy_sqlite(settings.db_path, str(newer))
        con = sqlite3.connect(newer)
        con.execute(f"PRAGMA user_version = {database.SCHEMA_VERSION + 1}")
        con.commit()
        con.close()
        before = self._count(models.title_table)
        with self.assertRaises(RuntimeError):
            exchange.restore(str(newer), "server")
        self.assertEqual(self._count(models.title_table), before)


if __name__ == "__main__":
    unittest.main()
