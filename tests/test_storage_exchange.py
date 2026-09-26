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
from services.crawl_manager import CrawlManager

real_run_after_crawl = CrawlManager.run_after_crawl  # 테스트가 완료 훅을 가짜로 바꿔도 직접 부를 수 있게
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
        # 복원 뒤 재개 스레드가 실제 게이트 검사(네트워크 대기)를 하며 다음 테스트로 새지 않게 기본은 즉시 거부.
        # 대기 큐 시작을 시험하는 테스트는 _launch_env 가 이 위에 다시 패치한다.
        default_gate = mock.patch("services.crawl_control._check_crawl_allowed",
                                  side_effect=RuntimeError("test default: crawl not allowed"))
        default_gate.start()
        self.addCleanup(default_gate.stop)
        self.addCleanup(self._join_crawl_threads)
        get_engine().dispose()
        for ext in ("", "-wal", "-shm"):
            if os.path.exists(settings.db_path + ext):
                os.remove(settings.db_path + ext)
        fixture_server.seed_engine(get_engine())
        with get_engine().begin() as conn:
            conn.execute(text("INSERT INTO mysafety_geocode_cache(주소정규화, 상태, source) VALUES ('서버 전용 주소', 'ok', 'kakao')"))
        self._tmp = tempfile.TemporaryDirectory()
        self.upload = Path(self._tmp.name) / "mobile.db"

    @staticmethod
    def _join_crawl_threads():
        import threading
        for t in threading.enumerate():
            if t.name in ("crawl-resume-after-restore", "crawl-pending-after"):
                t.join(15)

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

    def _crawl_reset(self):
        from services.crawl_manager import crawl_manager
        timer = getattr(crawl_manager, "_retry_timer", None)
        if timer is not None:
            timer.cancel()
        crawl_manager._retry_timer = None
        crawl_manager._retry_delay = crawl_manager.RETRY_FIRST_SECONDS
        crawl_manager.clear_process()
        crawl_manager.pop_pending()

    def test_crawler_cannot_start_while_a_restore_is_running(self):
        import threading
        from services.crawl_manager import CrawlBlockedByRestore, crawl_manager

        self.addCleanup(self._crawl_reset)
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
        log = os.path.join(settings.datapath, "logs", "t.log")
        gate = {"ok": False}

        def check():
            if not gate["ok"]:
                raise RuntimeError("COMMUNITY_REBUILD_REQUIRED")

        with mock.patch.object(exchange, "_swap_in", slow_swap), \
             mock.patch("services.crawl_manager.subprocess.Popen", popen), \
             mock.patch("services.crawl_manager.block_if_fixture"), \
             mock.patch("services.crawl_control._check_crawl_allowed", side_effect=check):
            r = threading.Thread(target=lambda: exchange.restore(str(self.upload), "mobile"))
            r.start()
            try:
                self.assertTrue(inside.wait(10))
                self.assertTrue(crawl_manager.restore_in_progress())
                try:
                    crawl_manager.start_crawl(["crawler"], cwd=".", log_file=log)
                except CrawlBlockedByRestore as exc:
                    errors.append(exc)
                # 대기 큐 자동 시작이 복원과 겹치면(검사는 통과해도) 시작하지 않고 큐를 그대로 둔다
                crawl_manager.append_to_pending("SPP-1")
                crawl_manager.append_to_pending("SPP-2")
                gate["ok"] = True
                self.assertFalse(crawl_manager.launch_pending_crawl())
                gate["ok"] = False
                self.assertEqual(crawl_manager.pending_items(), ["SPP-1", "SPP-2"])
            finally:
                proceed.set()
                r.join(20)
            self.assertFalse(r.is_alive())
            self.assertEqual(len(errors), 1)
            self.assertEqual(popen.call_count, 0)  # 호출 인자(환경 변수)를 실패 메시지에 찍지 않는다(R4-04)
            self.assertFalse(crawl_manager.restore_in_progress())
            # 복원 뒤 재개는 크롤 허용 검사(여기서는 초기화 필요)에 막혀 큐에 그대로 남는다
            time_limit = __import__("time").monotonic() + 5
            while __import__("time").monotonic() < time_limit and crawl_manager.pending_count() != 2:
                __import__("time").sleep(0.05)
            self.assertEqual(crawl_manager.pop_pending(), ["SPP-1", "SPP-2"])
            # 복원이 끝나면 다시 시작할 수 있다
            self.assertTrue(crawl_manager.start_crawl(["crawler"], cwd=".", log_file=log))
            self.assertEqual(popen.call_count, 1)  # 호출 인자(환경 변수)를 실패 메시지에 찍지 않는다(R4-04)

    def test_restore_is_refused_atomically_when_a_crawl_is_running(self):
        from services.crawl_manager import crawl_manager

        self.addCleanup(self._crawl_reset)
        _mobile_db(self.upload)
        running = mock.MagicMock()
        running.poll.return_value = None
        crawl_manager._active_process = running
        before = self._count(models.title_table)
        # ensure_restore_allowed 의 사전 검사를 지나도(가짜로 False) hold 가 같은 잠금에서 다시 막는다
        with mock.patch.object(crawl_manager, "is_crawling", return_value=False):
            with self.assertRaises(exchange.RestoreRefused):
                exchange.restore(str(self.upload), "mobile")
        self.assertEqual(self._count(models.title_table), before)
        self.assertFalse(crawl_manager.restore_in_progress())

    def test_a_stopped_crawler_that_is_still_alive_keeps_blocking_restore(self):
        """감사 R2-01: 종료 요청만으로 참조를 지우지 않는다 — 실제로 끝날 때까지 복원은 거부."""
        import subprocess
        from services.crawl_manager import crawl_manager

        self.addCleanup(self._crawl_reset)
        _mobile_db(self.upload)
        state = {"alive": True, "terminated": 0, "killed": 0}

        class Stubborn:
            def poll(self):
                return None if state["alive"] else 0

            def terminate(self):
                state["terminated"] += 1

            def kill(self):
                state["killed"] += 1

            def wait(self, timeout=None):
                if state["alive"]:
                    raise subprocess.TimeoutExpired("crawler", timeout)
                return 0

        proc = Stubborn()
        crawl_manager._active_process = proc
        with mock.patch.object(crawl_manager, "STOP_WAIT_SECONDS", 0.01), mock.patch.object(crawl_manager, "KILL_WAIT_SECONDS", 0.01):
            self.assertTrue(crawl_manager.stop_crawl())
        self.assertEqual((state["terminated"], state["killed"]), (1, 1))
        self.assertIs(crawl_manager.get_process(), proc, "살아 있으면 참조를 지우지 않는다")
        self.assertTrue(crawl_manager.is_crawling())
        before = self._count(models.title_table)
        with self.assertRaises(exchange.RestoreRefused):
            exchange.restore(str(self.upload), "mobile")
        self.assertEqual(self._count(models.title_table), before)
        # 실제로 끝나면 복원할 수 있다
        state["alive"] = False
        _, count = exchange.restore(str(self.upload), "mobile")
        self.assertEqual(count, 1)

    def test_stop_clears_the_reference_only_after_the_crawler_exits(self):
        from services.crawl_manager import crawl_manager

        self.addCleanup(self._crawl_reset)
        state = {"alive": True}

        class Polite:
            def poll(self):
                return None if state["alive"] else 0

            def terminate(self):
                state["alive"] = False

            def wait(self, timeout=None):
                return 0

        crawl_manager._active_process = Polite()
        self.assertTrue(crawl_manager.stop_crawl())
        self.assertIsNone(crawl_manager.get_process())

    class _Child:
        """가짜 크롤러 자식: finish(code) 전까지 실행 중."""

        def __init__(self, queue_file=None):
            import threading
            self.done = threading.Event()
            self.code = None
            self.args = []
            self.queue_file = queue_file

        def poll(self):
            return self.code if self.done.is_set() else None

        def wait(self, timeout=None):
            if not self.done.wait(timeout if timeout is not None else 10):
                import subprocess
                raise subprocess.TimeoutExpired("crawler", timeout)
            return self.code

        def finish(self, code):
            self.code = code
            self.done.set()

        def finish_processed(self, processed, not_found=()):
            """실제 크롤러(start.py)처럼 번호별 결과 보고를 쓰고 0 으로 끝난다."""
            from services import crawl_queue_report
            crawl_queue_report.write(self.queue_file, list(processed), list(not_found))
            self.finish(0)

        def terminate(self):
            self.finish(-15)

        kill = terminate

    def _launch_env(self, gate_ok=True, gate=None):
        """실제 launch_pending_crawl·start_crawl 경로를 돌리되 자식 프로세스만 가짜(_Child)로. 자식이 읽을 큐 파일 내용은
        시작 순간 기록한다(실제 자식은 나중에 읽지만, 여기서는 파일이 무엇을 담았는지만 본다)."""
        self._launched = []
        self._children = []

        def fake_popen(cmd, **kwargs):
            path = cmd[cmd.index("--queue") + 1] if "--queue" in cmd else None
            if path:
                with open(path, encoding="utf-8") as f:
                    self._launched.append((path, f.read().split("\n")))
            child = self._Child(path)
            if getattr(self, "_children_exit_immediately", False):
                with open(path, encoding="utf-8") as f:
                    child.finish_processed(f.read().split("\n"))
            self._children.append(child)
            return child

        popen = mock.MagicMock(side_effect=fake_popen)
        check = gate if gate is not None else (None if gate_ok else RuntimeError("COMMUNITY_REBUILD_REQUIRED"))
        patches = [
            mock.patch("services.crawl_manager.subprocess.Popen", popen),
            mock.patch("services.crawl_manager.block_if_fixture"),
            mock.patch("services.crawl_manager.CrawlManager.run_after_crawl"),  # 완료 훅의 나머지(로그·방송)는 돌리지 않는다
            mock.patch("services.crawl_control._check_crawl_allowed", side_effect=check),
            mock.patch("services.ws_manager.ws_manager.broadcast_from_thread"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        def finish_children():  # 패치가 살아 있는 동안 자식을 끝내고 후처리 스레드가 끝나길 기다린다(다음 테스트로 새지 않게)
            from services.crawl_manager import crawl_manager
            for c in self._children:
                if not c.done.is_set():
                    c.finish(0)
            self._join_crawl_threads()
            self._until(lambda: not crawl_manager._reserved)
            import time
            time.sleep(0.1)
        self.addCleanup(finish_children)
        return popen

    def _queue_arg(self, call):
        cmd = call.args[0]
        path = cmd[cmd.index("--queue") + 1]
        return next((p, items) for p, items in self._launched if p == path)

    @staticmethod
    def _until(cond, seconds=5):
        import time
        deadline = time.monotonic() + seconds
        while not cond() and time.monotonic() < deadline:
            time.sleep(0.02)
        return cond()

    def test_pending_queue_resumes_once_after_restore_and_survives_a_restart(self):
        """감사 R2-02: 복원이 끝나면 허용 검사 뒤 대기 큐를 한 번 이어서 처리하고, 큐는 파일로 남아 재시작에도 유지된다."""
        from services.crawl_manager import crawl_manager

        self.addCleanup(self._crawl_reset)
        crawl_manager.append_to_pending("SPP-7")
        crawl_manager._pending_queue.clear()  # 재시작 흉내: 메모리 큐를 비우고 파일에서 다시 읽는다
        crawl_manager._pending_loaded = False
        self.assertEqual(crawl_manager.pending_count(), 1)
        _mobile_db(self.upload)
        popen = self._launch_env(gate_ok=True)
        exchange.restore(str(self.upload), "mobile")
        self.assertTrue(self._until(lambda: popen.call_count == 1))
        self.assertEqual(self._queue_arg(popen.call_args)[1], ["SPP-7"])
        self._children[0].finish_processed(["SPP-7"])
        self.assertTrue(self._until(lambda: crawl_manager.pending_items() == []), "자식이 처리했다고 보고한 뒤에만 큐에서 뺀다")
        self.assertFalse(os.path.exists(os.path.join(settings.datapath, crawl_manager.PENDING_FILE)))

    def test_pending_items_stay_when_the_start_fails_and_each_run_gets_its_own_queue_file(self):
        """감사 R3-01: 다른 크롤이 먼저 시작해 이번 시작이 실패하면 항목·파일을 그대로 둔다. 실행마다 큐 파일이 다르다."""
        from services.crawl_manager import crawl_manager

        self.addCleanup(self._crawl_reset)
        popen = self._launch_env(gate_ok=True)
        crawl_manager.append_to_pending("SPP-RACE")
        busy = mock.MagicMock()
        busy.poll.return_value = None
        crawl_manager._active_process = busy  # 다른 요청이 먼저 크롤러를 시작함
        self.assertFalse(crawl_manager.launch_pending_crawl())
        self.assertEqual(popen.call_count, 0)  # 호출 인자(환경 변수)를 실패 메시지에 찍지 않는다(R4-04)
        self.assertEqual(crawl_manager.pending_items(), ["SPP-RACE"])
        self.assertTrue(os.path.exists(os.path.join(settings.datapath, crawl_manager.PENDING_FILE)))
        crawl_manager.clear_process()
        self.assertTrue(crawl_manager.launch_pending_crawl())
        first_path, first = self._queue_arg(popen.call_args)
        self.assertEqual(first, ["SPP-RACE"])
        self._children[0].finish_processed(["SPP-RACE"])
        self.assertTrue(self._until(lambda: crawl_manager.pending_items() == []))
        crawl_manager.clear_process()
        crawl_manager.append_to_pending("SPP-SECOND")
        self.assertTrue(crawl_manager.launch_pending_crawl())
        second_path, second = self._queue_arg(popen.call_args)
        self.assertNotEqual(first_path, second_path)
        self.assertEqual(second, ["SPP-SECOND"])
        self.assertEqual(self._launched[0], (first_path, ["SPP-RACE"]), "앞 실행의 큐 파일을 덮어쓰지 않는다")

    def test_items_stay_queued_until_the_child_exits_cleanly(self):
        """감사 R4-01: 자식이 큐를 읽기 전에(또는 처리 중) 실패하면 번호가 큐에 남는다. 실행 중에는 다시 맡기지 않는다."""
        from services.crawl_manager import crawl_manager

        self.addCleanup(self._crawl_reset)
        popen = self._launch_env(gate_ok=True)
        crawl_manager.append_to_pending("SPP-A")
        self.assertTrue(crawl_manager.launch_pending_crawl())
        self.assertEqual(crawl_manager.pending_count(), 0, "실행이 맡은 번호는 다시 맡기지 않는다")
        self.assertEqual(crawl_manager.pending_items(), ["SPP-A"], "자식이 끝나기 전에는 큐(파일)에 남아 있다")
        crawl_manager.append_to_pending("SPP-B")  # 실행 중에 들어온 번호
        self._children[0].finish(1)  # 로그인·DB 준비 중 실패 등
        self.assertTrue(self._until(lambda: crawl_manager.pending_count() == 2))
        self.assertEqual(crawl_manager.pending_items(), ["SPP-A", "SPP-B"])
        self.assertEqual(popen.call_count, 1)  # 실패한 번호로 곧바로 다시 돌지 않는다(다음 계기에)
        with open(os.path.join(settings.datapath, crawl_manager.PENDING_FILE), encoding="utf-8") as f:
            self.assertEqual(__import__("json").load(f), ["SPP-A", "SPP-B"])

    def test_concurrent_launches_start_once_and_keep_the_running_log(self):
        """감사 R4-02: 동시에 두 번 불러도 시작은 한 번, 두 번째가 실행 중인 크롤의 로그를 지우지 않는다."""
        import threading
        from services import crawl_control
        from services.crawl_manager import crawl_manager

        self.addCleanup(self._crawl_reset)
        popen = self._launch_env(gate_ok=True)
        crawl_manager.append_to_pending("SPP-DUPE")
        barrier = threading.Barrier(2)
        results = []

        def go():
            barrier.wait()
            results.append(crawl_manager.launch_pending_crawl())

        ts = [threading.Thread(target=go) for _ in range(2)]
        [t.start() for t in ts]
        [t.join(10) for t in ts]
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(popen.call_count, 1)
        self.assertEqual(len({p for p, _ in self._launched}), 1)
        log = os.path.join(settings.datapath, "logs", "current_crawl.log")
        with open(log, encoding="utf-8") as f:
            head = f.read()
        self.assertIn("대기 큐 자동 시작", head)
        # 실행 중에 사용자가 번호를 요청해도 로그는 그대로, 번호는 대기 큐로
        with mock.patch.object(crawl_manager, "is_crawling", return_value=False):  # 사전 검사를 지나 실제 시작 경쟁까지 가게
            self.assertEqual(crawl_control.enqueue_report("SPP-USER")["status"], "queued")
        with open(log, encoding="utf-8") as f:
            self.assertEqual(f.read(), head, "실행 중인 크롤의 로그를 지우지 않는다")
        self.assertIn("SPP-USER", crawl_manager.pending_items())

    def test_two_launches_never_hand_the_same_numbers_to_two_crawls(self):
        """감사 R4-02: 첫 자식이 곧바로 끝나도(다음 시작이 가능해짐) 동시에 불린 둘째 launch 가 같은 번호로 또 시작하지 않는다."""
        import threading
        import time
        from services.crawl_manager import crawl_manager

        self.addCleanup(self._crawl_reset)
        self._children_exit_immediately = True
        self.addCleanup(setattr, self, "_children_exit_immediately", False)
        # 두 호출이 모두 번호를 읽은 뒤 시작하도록 허용 검사를 늦춘다
        popen = self._launch_env(gate=lambda: time.sleep(0.3))
        crawl_manager.append_to_pending("SPP-ONCE")
        barrier = threading.Barrier(2)
        ts = [threading.Thread(target=lambda: (barrier.wait(), crawl_manager.launch_pending_crawl())) for _ in range(2)]
        [t.start() for t in ts]
        [t.join(10) for t in ts]
        self.assertEqual([items for _, items in self._launched].count(["SPP-ONCE"]), 1, "같은 번호를 두 크롤이 맡지 않는다")
        self.assertLessEqual(popen.call_count, 1)

    def test_only_numbers_the_child_reports_as_done_leave_the_queue(self):
        """감사 R5-01: 자식이 0 으로 끝나도 보고에 없는 번호는 남는다. 보고의 처리·없음 번호만 빠진다."""
        from services.crawl_manager import crawl_manager

        self.addCleanup(self._crawl_reset)
        self._launch_env(gate_ok=True)
        for n in ("SPP-OK", "SPP-FAIL", "SPP-NONE"):
            crawl_manager.append_to_pending(n)
        self.assertTrue(crawl_manager.launch_pending_crawl())
        self._children[0].finish_processed(["SPP-OK"], not_found=["SPP-NONE"])
        self.assertTrue(self._until(lambda: crawl_manager.pending_count() == 1))
        self.assertEqual(crawl_manager.pending_items(), ["SPP-FAIL"])
        # 보고 없이 0 으로 끝나면(로그인 실패 등) 아무것도 빼지 않는다
        crawl_manager.clear_process()
        self._join_crawl_threads()
        if crawl_manager._retry_timer is not None:
            crawl_manager._retry_timer.cancel()
            crawl_manager._retry_timer = None
        self.assertTrue(crawl_manager.launch_pending_crawl())
        self._children[1].finish(0)
        self._join_crawl_threads()
        self.assertEqual(crawl_manager.pending_items(), ["SPP-FAIL"])
        self.assertIsNotNone(crawl_manager._retry_timer, "남은 번호는 늘어나는 간격으로 다시 시도한다(R5-02)")

    def _queue_run(self, items, *, titles, details=None):
        """실제 start._run_crawling_process 큐 경로를 돌린다. titles(page_range, progress) 가 목록 탐색 결과·경과를 흉내 낸다."""
        import pandas as pd
        import start
        from services import crawl_queue_report

        queue_file = os.path.join(settings.datapath, f"pending_queue_test_{len(items)}.txt")
        crawl_queue_report.remove_files(queue_file)
        with open(queue_file, "w", encoding="utf-8") as f:
            f.write("\n".join(items))
        self.addCleanup(crawl_queue_report.remove_files, queue_file)

        def fake_titles(driver=None, page_range=None, browser_fallback=False, progress=None):
            return titles(page_range, progress if progress is not None else {})

        def fake_details(driver=None, report_ids=None, browser_fallback=False):
            for rid in report_ids:
                if details is not None and rid not in details:
                    raise ConnectionError("네트워크 끊김")
                yield (pd.DataFrame([{"ID": rid, "처리상태": "수용", "처리내용": "큐 저장", "종결여부": "Y"}]), "traffic", "자동차·교통위반-신호위반")

        args = {"queue_file": queue_file, "page_range": None, "force": False, "rebuild": None}
        with mock.patch.object(start.crawltitle_api, "crawl_titles", side_effect=fake_titles), \
             mock.patch.object(start.crawldetail_api, "crawl_details", side_effect=fake_details):
            start._run_crawling_process(None, get_engine(), args)
        return queue_file, crawl_queue_report.read(queue_file)

    @staticmethod
    def _list_ok(pages):
        def titles(page_range, progress):
            progress.update(total=pages * 200, pages_expected=pages, pages_ok=list(page_range), pages_failed=[],
                            first_error=None, list_ok=True)
            return [], pages
        return titles

    def _two_numbers(self):
        with get_engine().connect() as conn:
            return conn.execute(select(models.title_table.c.ID, models.title_table.c["신고번호"]).limit(2)).all()

    def test_the_real_crawler_reports_saved_and_missing_numbers_only(self):
        """감사 R5-01·R6-01: start.py 큐 경로 — 저장까지 끝난 번호와 목록 전체를 **성공적으로** 훑어도 없는 번호만 보고한다.
        중간에 멈춘 번호는 보고하지 않는다. 로그인 실패로 끝나면 보고가 없다."""
        import start
        from services import crawl_queue_report
        from services.crawl_manager import crawl_manager

        self.addCleanup(self._crawl_reset)
        (id1, num1), (id2, num2) = self._two_numbers()
        queue_file, (done, not_found, ambiguous) = self._queue_run(
            [num1, num2, "SPP-0000-NOPE"], titles=self._list_ok(1), details={id1})
        self.assertEqual(done, {num1, "SPP-0000-NOPE"})
        self.assertEqual((not_found, ambiguous), (["SPP-0000-NOPE"], []))
        for n in (num1, num2, "SPP-0000-NOPE"):
            crawl_manager.append_to_pending(n)
        left = crawl_manager._settle_pending([num1, num2, "SPP-0000-NOPE"], crawl_manager._read_queue_report(queue_file))
        self.assertEqual(left, [num2])
        self.assertEqual(crawl_manager.pending_items(), [num2])

        # 로그인(직접·대체) 모두 실패: 오류를 기록하고 정상 반환하지만 보고는 없다 → 전부 남는다
        crawl_queue_report.remove_files(queue_file)
        with open(queue_file, "w", encoding="utf-8") as f:
            f.write(num2)
        args = {"queue_file": queue_file, "page_range": None, "force": False, "rebuild": None, "reset": False}
        with mock.patch.object(start, "_parse_args", return_value=args), \
             mock.patch.object(start, "_validate_settings"), mock.patch.object(start, "_prepare_database"), \
             mock.patch("core.crawler.direct_login.get_valid_token", side_effect=RuntimeError("login failed")), \
             mock.patch.object(start.driv, "create_driver", side_effect=RuntimeError("no browser")), \
             mock.patch.object(start, "_process_and_save_results"):
            start.main()
        self.assertEqual(crawl_queue_report.read(queue_file), (set(), [], []))
        self.assertEqual(crawl_manager._settle_pending([num2], crawl_manager._read_queue_report(queue_file)), [num2])

    def test_a_failed_or_incomplete_list_search_never_marks_a_number_missing(self):
        """감사 R6-01: 목록 호출 실패(예외·오류 응답)나 탐색 상한 도달은 '없음'으로 확정하지 않는다 — 번호는 다시 찾는다."""
        def failing(page_range, progress):
            progress.update(total=None, pages_expected=0, pages_ok=[], pages_failed=[1], first_error="network: down", list_ok=False)
            return [], 0

        def raising(page_range, progress):
            raise ConnectionError("down")

        for titles in (failing, raising, self._list_ok(250)):  # 250 페이지 > 탐색 상한 100
            _, (done, not_found, ambiguous) = self._queue_run(["SPP-0000-NOPE"], titles=titles)
            self.assertEqual((done, not_found, ambiguous), (set(), [], []), titles)

    def test_an_ambiguous_partial_number_is_never_completed_with_an_arbitrary_report(self):
        """감사 R6-02: 목록 탐색 뒤 재해석도 '정확 → 접두어 → 유일한 부분 일치' 규칙 — 여러 건에 걸리면 임의 신고로 처리하지 않는다."""
        with get_engine().connect() as conn:
            numbers = [r[0] for r in conn.execute(select(models.title_table.c["신고번호"]))]
        token = next(t for t in ("SPP-", "SPP-26", "SPP-2609") if sum(t in n for n in numbers) > 1)
        saved = []
        import start
        real_save = start._save_details_as_they_arrive

        def spy(engine, stream, saved_ids=None):
            result = real_save(engine, stream, saved_ids)
            saved.extend(saved_ids or [])
            return result

        with mock.patch.object(start, "_save_details_as_they_arrive", side_effect=spy):
            _, (done, not_found, ambiguous) = self._queue_run([token], titles=self._list_ok(1))
        self.assertEqual(saved, [], "모호한 번호로 어떤 신고도 저장하지 않는다")
        self.assertEqual((not_found, ambiguous), ([], [token]))
        self.assertEqual(done, {token}, "목록 전체를 훑은 뒤에도 모호하면 사용자에게 알리고 큐에서 뺀다")

    def test_a_blocked_retry_is_rescheduled_and_a_restart_schedules_one(self):
        """감사 R6-03: 재시도가 게이트·초기화에 막혀도 다시 걸리고, 서버 기동 때 남은 번호가 있으면 타이머 하나를 건다."""
        from services.crawl_manager import crawl_manager

        self.addCleanup(self._crawl_reset)
        popen = self._launch_env(gate_ok=False)
        crawl_manager.append_to_pending("SPP-WAIT")
        with mock.patch.object(crawl_manager, "RETRY_FIRST_SECONDS", 3600.0):
            crawl_manager._retry_delay = 3600.0
            crawl_manager._retry_fire()
            self.assertEqual(popen.call_count, 0)
            self.assertIsNotNone(crawl_manager._retry_timer, "막혀도 번호가 남으면 다시 건다")
            crawl_manager._retry_timer.cancel()
            crawl_manager._retry_timer = None
            crawl_manager.schedule_retry_if_pending()
            self.assertIsNotNone(crawl_manager._retry_timer, "기동 때 남은 번호가 있으면 타이머를 건다")

    def test_many_launch_requests_use_a_single_worker(self):
        """감사 R6-04: 시작 요청이 몰려도 작업자는 하나 — 요청은 합쳐지고 마지막 요청 뒤에도 한 번 더 시도한다."""
        import threading
        from services.crawl_manager import crawl_manager

        self.addCleanup(self._crawl_reset)
        release = threading.Event()
        calls = []

        def slow_launch():
            calls.append(1)
            release.wait(5)
            return False

        with mock.patch.object(crawl_manager, "launch_pending_crawl", side_effect=slow_launch):
            for _ in range(25):
                crawl_manager.request_pending_launch()
            alive = [t for t in threading.enumerate() if t.name == "crawl-pending-request"]
            self.assertLessEqual(len(alive), 1)
            release.set()
            self.assertTrue(self._until(lambda: not crawl_manager._request_worker_active))
        self.assertEqual(len(calls), 2, "진행 중 요청과, 그동안 들어온 요청들을 합친 한 번")

    def test_a_malformed_report_or_hook_error_still_releases_the_reservation(self):
        """감사 R6-05: 형식이 틀린 보고(유효한 JSON)나 완료 훅 예외에도 예약이 풀리고 실행 파일이 지워진다."""
        import glob
        from services import crawl_queue_report
        from services.crawl_manager import CrawlManager, crawl_manager

        self.addCleanup(self._crawl_reset)
        self._launch_env(gate_ok=True)
        crawl_manager.append_to_pending("SPP-BAD")
        with mock.patch.object(CrawlManager, "run_after_crawl", side_effect=RuntimeError("hook failed")):
            self.assertTrue(crawl_manager.launch_pending_crawl())
            child = self._children[0]
            with open(crawl_queue_report.report_path(child.queue_file), "w", encoding="utf-8") as f:
                f.write('{"processed": 7}')
            try:
                parsed = crawl_queue_report.read(child.queue_file)
            except TypeError:
                parsed = "TypeError"
            self.assertEqual(parsed, (set(), [], []), "형식이 틀린 보고는 빈 보고(아무것도 끝나지 않음)로 본다")
            child.finish(0)
            self._until(lambda: not crawl_manager._reserved)
            self._join_crawl_threads()
        self.assertEqual(crawl_manager._reserved, set())
        self.assertEqual(crawl_manager.pending_items(), ["SPP-BAD"])
        self.assertEqual(glob.glob(os.path.join(settings.datapath, "pending_queue_*")), [])

    def test_a_failed_notification_or_preparation_does_not_leak_reservations_or_files(self):
        """감사 R5-03·R5-04: 시작 알림이 실패해도 감시가 예약을 풀고, 준비(prepare)가 실패하면 번호 파일을 남기지 않는다."""
        import glob
        from services.crawl_manager import crawl_manager

        self.addCleanup(self._crawl_reset)
        self._launch_env(gate_ok=True)
        crawl_manager.append_to_pending("SPP-NOTE")
        with mock.patch("services.ws_manager.ws_manager.broadcast_from_thread", side_effect=RuntimeError("ws down")):
            try:
                started = crawl_manager.launch_pending_crawl()
            except RuntimeError:
                started = None  # 알림 예외가 새어 나와도 아래 단언(예약 누수 없음)이 판정한다
        self._children[0].finish_processed(["SPP-NOTE"])
        self._until(lambda: not crawl_manager._reserved and crawl_manager.pending_items() == [])
        self.assertEqual(crawl_manager._reserved, set(), "알림이 실패해도 자식이 끝나면 예약이 풀린다(R5-03)")
        self.assertEqual(crawl_manager.pending_items(), [])
        self.assertTrue(started, "알림 실패는 시작 결과를 바꾸지 않는다")
        crawl_manager.clear_process()
        crawl_manager.append_to_pending("SPP-PREP")
        with mock.patch("services.crawl_manager.rotate_crawl_log", side_effect=OSError("log dir gone")):
            with self.assertRaises(OSError):
                crawl_manager.launch_pending_crawl()
        self.assertEqual(crawl_manager._reserved, set())
        self.assertEqual(crawl_manager.pending_count(), 1)
        self.assertEqual(glob.glob(os.path.join(settings.datapath, "pending_queue_*.txt")), [])

    def test_a_number_queued_after_the_completion_hook_passed_still_starts(self):
        """감사 R5-02: 사용자가 '크롤 중'이라 대기열에 넣었는데 그 크롤의 완료 훅이 이미 지나간 경우에도 번호가 시작된다."""
        from services import crawl_control
        from services.crawl_manager import crawl_manager

        self.addCleanup(self._crawl_reset)
        popen = self._launch_env(gate_ok=True)
        with mock.patch.object(crawl_manager, "is_crawling", return_value=True):  # 검사 시점엔 크롤 중, 곧 끝남(훅은 이미 지나감)
            self.assertEqual(crawl_control.enqueue_report("SPP-LATE")["status"], "queued")
        self.assertTrue(self._until(lambda: popen.call_count == 1), "번호를 넣은 쪽이 한 번 더 시작을 시도한다")
        self.assertEqual(self._queue_arg(popen.call_args)[1], ["SPP-LATE"])

    def test_a_restore_between_the_check_and_the_start_blocks_the_start(self):
        """감사 R4-03: 허용 검사를 통과한 뒤 복원이 끝나면(데이터셋 교체) 그 검사로는 시작하지 않는다 — 대기 큐·사용자 시작 모두."""
        from services import crawl_control
        from services.crawl_manager import crawl_manager

        self.addCleanup(self._crawl_reset)

        def check_then_restore():
            with mock.patch.object(threading_mod.Thread, "start"):  # 복원 뒤 재개 스레드는 띄우지 않는다
                with crawl_manager.hold_for_restore():
                    pass

        import threading as threading_mod
        popen = self._launch_env(gate=check_then_restore)
        crawl_manager.append_to_pending("SPP-GEN")
        self.assertFalse(crawl_manager.launch_pending_crawl())
        self.assertEqual(popen.call_count, 0)
        self.assertEqual(crawl_manager.pending_count(), 1)
        with self.assertRaises(RuntimeError):
            crawl_control.enqueue_report("SPP-USER2")
        self.assertEqual(popen.call_count, 0)

    def test_crawl_completion_does_not_start_the_queue_while_a_rebuild_is_required(self):
        """감사 R3-02: 완료 훅의 대기 큐 시작도 게이트·초기화 검사를 거친다 — 막히면 시작 0, 큐 보존."""
        from services.crawl_manager import crawl_manager

        self.addCleanup(self._crawl_reset)
        popen = self._launch_env(gate_ok=False)
        crawl_manager.append_to_pending("SPP-9")
        finished = mock.MagicMock()
        finished.wait.return_value = 0
        finished.poll.return_value = 0
        finished.args = []
        with mock.patch("time.sleep"), mock.patch.object(crawl_manager, "_resume_geocode_backfill"):
            real_run_after_crawl(crawl_manager, finished, os.path.join(settings.datapath, "logs", "none.log"))
        self.assertEqual(popen.call_count, 0)  # 호출 인자(환경 변수)를 실패 메시지에 찍지 않는다(R4-04)
        self.assertEqual(crawl_manager.pending_items(), ["SPP-9"])

    def test_a_queue_that_cannot_be_saved_is_reported_not_silently_kept_in_memory(self):
        """감사 R3-03: 파일에 남기지 못하면 대기열에 넣었다고 답하지 않는다."""
        from services import crawl_control
        from services.crawl_manager import crawl_manager

        self.addCleanup(self._crawl_reset)
        with mock.patch("services.crawl_manager.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(RuntimeError):
                crawl_manager.append_to_pending("SPP-LOST")
            self.assertEqual(crawl_manager.pending_items(), [])
            running = mock.MagicMock()
            running.poll.return_value = None
            crawl_manager._active_process = running
            with mock.patch("services.crawl_control._check_crawl_allowed"):
                with self.assertRaises(RuntimeError):
                    crawl_control.enqueue_report("SPP-LOST")
        self.assertEqual(crawl_manager.pending_items(), [])


if __name__ == "__main__":
    unittest.main()
