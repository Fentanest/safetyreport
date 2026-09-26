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

    def _add_legacy_duplicate(self, *, decisions=True):
        """앱 DB 에 같은 본문 신고 2건 + 옛(레거시) id 중복군 + 그 id 로 남긴 사용자 판단."""
        con = sqlite3.connect(self.upload)
        con.execute("INSERT INTO reports VALUES ('m2','수용','SPP-2609-9000012','신호위반','2026-09-01','참여 가능',NULL,NULL,'N','수용','기관','','서울 강서구 1','Y','traffic','자동차·교통위반-신호위반','',NULL)")
        con.executemany("INSERT INTO report_raw VALUES (?, ?, 'report_body', 1)", [("m1", "같은  본문\n"), ("m2", "같은 본문")])
        con.executescript(
            """
            CREATE TABLE duplicate_group (group_id TEXT PRIMARY KEY, fingerprint TEXT, match_type TEXT, status TEXT, representative_mode TEXT,
              representative_id TEXT, member_count INTEGER, apply_globally INTEGER, note TEXT, created_at INTEGER, updated_at INTEGER);
            CREATE TABLE duplicate_member (group_id TEXT, report_id TEXT, report_number TEXT, category TEXT, is_representative INTEGER,
              priority_score INTEGER, raw_match INTEGER, field_match INTEGER, created_at INTEGER, updated_at INTEGER);
            CREATE TABLE duplicate_decision (group_id TEXT PRIMARY KEY, status TEXT, representative_mode TEXT, representative_id TEXT,
              apply_globally INTEGER, note TEXT, updated_at INTEGER);
            INSERT INTO duplicate_group VALUES ('legacyfnv0001','legacyfnv0001','payload_exact','not_duplicate','manual','m2',2,0,'앱 메모',1,5);
            INSERT INTO duplicate_member VALUES ('legacyfnv0001','m1','SPP-2609-9000011','traffic',0,1,1,1,1,1),
                                                ('legacyfnv0001','m2','SPP-2609-9000012','traffic',1,2,1,1,1,1);
            """
        )
        if decisions:
            con.execute("INSERT INTO duplicate_decision VALUES ('legacyfnv0001','not_duplicate','manual','m2',0,'앱 메모',5)")
        con.commit()
        con.close()

    def test_mobile_decisions_follow_the_canonical_group_id(self):
        """G11-1: 옛 id 로 남은 앱 그룹의 판단도 서버 기준 id 로 옮겨져 그룹에 붙는다."""
        import hashlib

        _mobile_db(self.upload)
        self._add_legacy_duplicate()
        exchange.restore(str(self.upload), "mobile")
        canonical = hashlib.sha256("같은 본문".encode("utf-8")).hexdigest()
        with get_engine().connect() as conn:
            groups = {r.group_id: r for r in conn.execute(select(models.duplicate_group_table))}
            decisions = conn.execute(select(models.duplicate_decision_table)).mappings().all()
        self.assertIn(canonical, groups)
        self.assertEqual([(d["group_id"], d["status"], d["representative_id"]) for d in decisions], [(canonical, "not_duplicate", "m2")])

    def test_empty_decision_table_from_the_app_clears_server_decisions(self):
        """G11-2: 앱에 판단 표가 있고 비어 있으면 그것이 원천(서버 판단도 비움). 표가 없는 구앱은 서버 것 유지(위 테스트와 같은 규칙)."""
        with get_engine().begin() as conn:
            conn.execute(models.duplicate_decision_table.insert().values(
                group_id="server-only", status="confirmed_duplicate", representative_mode="auto", apply_globally=1, updated_at=1))
        _mobile_db(self.upload)
        self._add_legacy_duplicate(decisions=False)
        exchange.restore(str(self.upload), "mobile")
        self.assertEqual(self._count(models.duplicate_decision_table), 0)

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

    # ── 2026-09-26 감사 SOL-02·03·04 ──────────────────────────────────────────

    def test_known_mobile_columns_match_the_contract(self):
        import json
        contract = json.loads((Path(__file__).resolve().parents[1] / "contracts" / "storage-contract.json").read_text(encoding="utf-8"))
        expected = {e["mobile_table"]: {c["name"] for c in e["columns"] if c.get("mobile")}
                    for e in contract["entities"] if e.get("mobile_table")}
        self.assertEqual(exchange.known_mobile_columns(), expected)

    def test_unknown_column_with_values_refuses_before_touching_live_db(self):
        _mobile_db(self.upload)
        con = sqlite3.connect(self.upload)
        con.execute("ALTER TABLE reports ADD COLUMN 미래열 TEXT")
        con.execute("UPDATE reports SET 미래열 = '' WHERE ID = 'm1'")  # 빈 문자열도 값이다
        con.commit()
        con.close()
        before = self._count(models.title_table)
        with self.assertRaises(exchange.UnknownColumns) as ctx:
            exchange.restore(str(self.upload), "mobile")
        self.assertIn("reports.미래열(1행)", str(ctx.exception))
        self.assertIsInstance(ctx.exception, exchange.RestoreRefused)  # 라우트가 409 문장으로 보여 준다
        self.assertEqual(self._count(models.title_table), before)

    def test_unknown_column_that_is_all_null_loses_nothing_and_restores(self):
        _mobile_db(self.upload)
        con = sqlite3.connect(self.upload)
        con.execute("ALTER TABLE report_override ADD COLUMN 미래열 TEXT")
        con.commit()
        con.close()
        _, count = exchange.restore(str(self.upload), "mobile")
        self.assertEqual(count, 1)

    def _entry_value(self, report_id="m1"):
        with get_engine().connect() as conn:
            rows = conn.execute(select(models.entry_value_table.c.entry_value).where(models.entry_value_table.c.ID == report_id)).fetchall()
        return [r[0] for r in rows]

    def _set_server_entry(self, value):
        with get_engine().begin() as conn:
            conn.execute(models.entry_value_table.delete().where(models.entry_value_table.c.ID == "m1"))
            conn.execute(models.entry_value_table.insert().values(ID="m1", entry_value=value))

    def test_entry_value_follows_the_app_exactly_empty_null_and_old_app(self):
        # 앱의 빈 문자열은 서버의 이전 값을 덮는다
        self._set_server_entry("이전 값")
        _mobile_db(self.upload)
        con = sqlite3.connect(self.upload)
        con.execute("UPDATE reports SET entry_value = '' WHERE ID = 'm1'")
        con.commit()
        con.close()
        exchange.restore(str(self.upload), "mobile")
        self.assertEqual(self._entry_value(), [""])
        # 앱의 NULL(모름)은 서버 행 없음
        self._set_server_entry("이전 값")
        con = sqlite3.connect(self.upload)
        con.execute("UPDATE reports SET entry_value = NULL WHERE ID = 'm1'")
        con.commit()
        con.close()
        exchange.restore(str(self.upload), "mobile")
        self.assertEqual(self._entry_value(), [])
        # 열이 없는 구앱이면 서버 값 유지
        self._set_server_entry("이전 값")
        old = Path(self._tmp.name) / "old.db"
        con = sqlite3.connect(old)
        con.executescript("""
            CREATE TABLE reports (ID TEXT PRIMARY KEY, 신고번호 TEXT, 위반장소 TEXT, category TEXT);
            CREATE TABLE sync_meta (key TEXT PRIMARY KEY, value TEXT);
            INSERT INTO reports VALUES ('m1','SPP-2609-9000011','서울 강서구 1','traffic');
        """)
        con.commit()
        con.close()
        exchange.restore(str(old), "mobile")
        self.assertEqual(self._entry_value(), ["이전 값"])

    def test_write_committed_while_restore_waits_is_kept(self):
        """SOL-04: 복원이 시작될 때 이미 연결을 빌려 쓰던 요청의 커밋은 새 DB 에 남는다(장벽이 반납을 기다린다)."""
        import threading
        from core.database import write_barrier

        _mobile_db(self.upload)
        holding = threading.Event()
        release = threading.Event()
        errors = []

        def writer():
            try:
                with get_engine().begin() as conn:
                    holding.set()
                    release.wait(10)
                    conn.execute(models.api_keys_table.insert().values(key="k-during-restore", name="동시 쓰기", created_at="2026-09-26"))
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        t = threading.Thread(target=writer)
        t.start()
        self.assertTrue(holding.wait(5))
        result = {}
        r = threading.Thread(target=lambda: result.setdefault("v", exchange.restore(str(self.upload), "mobile")))
        r.start()
        # 복원은 연결 반납을 기다린다
        r.join(0.5)
        self.assertTrue(r.is_alive())
        self.assertEqual(write_barrier.active_connections(), 1)
        release.set()
        t.join(10)
        r.join(20)
        self.assertEqual(errors, [])
        self.assertFalse(r.is_alive())
        with get_engine().connect() as conn:
            keys = {row[0] for row in conn.execute(select(models.api_keys_table.c.key))}
        self.assertIn("k-during-restore", keys)

    def test_connections_opened_during_restore_wait_and_write_to_the_new_db(self):
        import threading
        from core.database import write_barrier

        _mobile_db(self.upload)
        entered = threading.Event()
        written = threading.Event()
        order = []
        real_swap = exchange._swap_in

        def slow_swap(staged, dst):
            entered.set()
            written.wait(0.5)  # 이 사이 다른 스레드가 연결을 얻으면 안 된다
            order.append("swap")
            real_swap(staged, dst)

        def late_writer():
            entered.wait(10)
            with get_engine().begin() as conn:
                order.append("write")
                conn.execute(models.api_keys_table.insert().values(key="k-after-restore", name="뒤 쓰기", created_at="2026-09-26"))
            written.set()

        w = threading.Thread(target=late_writer)
        w.start()
        with mock.patch.object(exchange, "_swap_in", slow_swap):
            exchange.restore(str(self.upload), "mobile")
        w.join(10)
        self.assertEqual(order, ["swap", "write"])
        with get_engine().connect() as conn:
            keys = {row[0] for row in conn.execute(select(models.api_keys_table.c.key))}
        self.assertIn("k-after-restore", keys)
        self.assertEqual(write_barrier.active_connections(), 0)

    def test_restore_refuses_when_a_connection_is_never_returned(self):
        import threading
        from core.database import write_barrier

        _mobile_db(self.upload)
        release = threading.Event()
        holding = threading.Event()

        def holder():
            with get_engine().connect():
                holding.set()
                release.wait(10)

        t = threading.Thread(target=holder)
        t.start()
        holding.wait(5)
        before = self._count(models.title_table)
        try:
            with mock.patch.object(write_barrier, "DRAIN_WAIT_SECONDS", 0.3):
                with self.assertRaises(exchange.RestoreRefused):
                    exchange.restore(str(self.upload), "mobile")
        finally:
            release.set()
            t.join(10)
        self.assertEqual(self._count(models.title_table), before)

    # ── Sol 재검증 SOL-04: 복원 ↔ 크롤러 시작 경쟁 ─────────────────────────────

    def test_crawler_cannot_start_while_a_restore_is_running(self):
        import threading
        from services.crawl_manager import CrawlBlockedByRestore, crawl_manager

        _mobile_db(self.upload)
        inside = threading.Event()
        proceed = threading.Event()
        real_swap = exchange._swap_in

        def slow_swap(staged, dst):
            inside.set()
            proceed.wait(10)
            real_swap(staged, dst)

        popen = mock.MagicMock()
        errors = []
        with mock.patch.object(exchange, "_swap_in", slow_swap), \
             mock.patch("services.crawl_manager.subprocess.Popen", popen), \
             mock.patch("services.crawl_manager.block_if_fixture"):
            r = threading.Thread(target=lambda: exchange.restore(str(self.upload), "mobile"))
            r.start()
            self.assertTrue(inside.wait(10))
            self.assertTrue(crawl_manager.restore_in_progress())
            try:
                crawl_manager.start_crawl(["crawler"], cwd=".", log_file=os.path.join(settings.datapath, "logs", "t.log"))
            except CrawlBlockedByRestore as exc:
                errors.append(exc)
            # 대기 큐 자동 시작이 겹치면 신고번호를 잃지 않고 큐로 되돌린다
            crawl_manager.launch_pending_crawl(["SPP-1", "SPP-2"])
            proceed.set()
            r.join(20)
            self.assertEqual(len(errors), 1)
            popen.assert_not_called()
            self.assertEqual(crawl_manager.pop_pending(), ["SPP-1", "SPP-2"])
            self.assertFalse(crawl_manager.restore_in_progress())
            # 복원이 끝나면 다시 시작할 수 있다
            self.assertTrue(crawl_manager.start_crawl(["crawler"], cwd=".", log_file=os.path.join(settings.datapath, "logs", "t.log")))
            popen.assert_called_once()
        crawl_manager.clear_process()

    def test_restore_is_refused_atomically_when_a_crawl_is_running(self):
        from services.crawl_manager import crawl_manager

        _mobile_db(self.upload)
        running = mock.MagicMock()
        running.poll.return_value = None
        crawl_manager._active_process = running
        before = self._count(models.title_table)
        try:
            # ensure_restore_allowed 의 사전 검사를 지나도(가짜로 False) hold 가 같은 잠금에서 다시 막는다
            with mock.patch.object(crawl_manager, "is_crawling", return_value=False):
                with self.assertRaises(exchange.RestoreRefused):
                    exchange.restore(str(self.upload), "mobile")
        finally:
            crawl_manager.clear_process()
        self.assertEqual(self._count(models.title_table), before)
        self.assertFalse(crawl_manager.restore_in_progress())


if __name__ == "__main__":
    unittest.main()
