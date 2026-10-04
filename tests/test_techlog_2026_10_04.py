"""2026-10-04 기술일지(서버·auth·모바일 정밀 점검) 서버 결함 회귀.

항목 번호는 기술일지 카드 번호다. 네트워크·운영 데이터 없이 fixture 시드 DB 와 가짜 객체로만 확인한다.
"""
from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from sqlalchemy import create_engine, func, select

from core.database import database, models
from core.storage import exchange
from core.utils import logger
from scripts.dev import fixture_server


def _seeded_engine(test: unittest.TestCase):
    logger.LoggerFactory.create_logger(mode="crawl")
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    engine = create_engine(f"sqlite:///{Path(tmp.name) / 'data.db'}")
    test.addCleanup(engine.dispose)
    fixture_server.seed_engine(engine)
    return engine


class ExchangeIntegrityTests(unittest.TestCase):
    def test_a1_03_unknown_category_is_refused_with_ids(self):
        exchange._refuse_unknown_categories([{"ID": "1", "category": "traffic"}, {"ID": "2", "category": "parking"},
                                             {"ID": "3", "category": "other"}])
        for bad in ("bicycle", None, "", "Traffic", " other"):
            with self.subTest(bad=bad), self.assertRaises(exchange.InvalidDatabaseRefused) as ctx:
                exchange._refuse_unknown_categories([{"ID": "1", "category": "traffic"}, {"ID": "X9", "category": bad}])
            self.assertIn("X9", str(ctx.exception))

    def test_a1_04_restores_in_the_same_second_keep_every_backup(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        live = os.path.join(tmp.name, "data.db")
        con = __import__("sqlite3").connect(live)
        con.execute("CREATE TABLE t (v TEXT)")
        con.execute("INSERT INTO t VALUES ('A')")
        con.commit()
        con.close()
        with mock.patch.object(exchange.settings, "datapath", tmp.name), \
                mock.patch.object(exchange, "datetime") as fake_dt:
            fake_dt.now.return_value.strftime.return_value = "20261005_010203"
            first = exchange._backup_live(live)
            con = __import__("sqlite3").connect(live)
            con.execute("UPDATE t SET v='B'")
            con.commit()
            con.close()
            second = exchange._backup_live(live)
        self.assertNotEqual(first, second)
        read = lambda p: __import__("sqlite3").connect(p).execute("SELECT v FROM t").fetchone()[0]  # noqa: E731
        self.assertEqual((read(first), read(second)), ("A", "B"))

    def test_b09_entry_value_restore_keeps_null_and_empty_meaning(self):
        engine = _seeded_engine(self)
        with engine.connect() as conn:
            ids = [r[0] for r in conn.execute(select(models.title_table.c.ID)).fetchall()][:3]
        snapshot = mock.Mock(report_columns={"entry_value"}, reports=[
            {"ID": ids[0], "entry_value": "자동차·교통위반-신호위반"}, {"ID": ids[1], "entry_value": ""},
            {"ID": ids[2], "entry_value": None}])
        with engine.begin() as conn:
            # 복원 함수의 entry_value 부분과 같은 순서로 실행(교체 의미 확인)
            for i in range(0, len(ids), exchange.BATCH):
                conn.execute(models.entry_value_table.delete().where(models.entry_value_table.c.ID.in_(ids[i:i + exchange.BATCH])))
            exchange._insert(conn, models.entry_value_table, [
                {"ID": r["ID"], "entry_value": r["entry_value"]} for r in snapshot.reports if r.get("entry_value") is not None])
            rows = dict(conn.execute(select(models.entry_value_table.c.ID, models.entry_value_table.c.entry_value)
                                     .where(models.entry_value_table.c.ID.in_(ids))).fetchall())
        self.assertEqual(rows, {ids[0]: "자동차·교통위반-신호위반", ids[1]: ""})


class PhotoBackfillTests(unittest.TestCase):
    def test_a2_02_capture_times_are_written_only_for_the_attachment_that_was_read(self):
        from services import photo_capture_time

        engine = _seeded_engine(self)
        with engine.begin() as conn:
            conn.execute(models.detail_parking_table.insert().values(ID="PX1", 처리상태="수용", 첨부사진="https://x/new.jpg"))
        times = lambda url: "2026-09-22 13:19:43"  # noqa: E731
        self.assertFalse(photo_capture_time.fill_one(engine, "PX1", "https://x/old.jpg", fetch=times))
        with engine.connect() as conn:
            row = conn.execute(select(models.detail_parking_table.c["사진_촬영수"]).where(
                models.detail_parking_table.c.ID == "PX1")).scalar()
        self.assertIsNone(row, "복원·재크롤링으로 바뀐 첨부에 이전 사진 정보를 쓰지 않는다")
        self.assertTrue(photo_capture_time.fill_one(engine, "PX1", "https://x/new.jpg", fetch=times))


class ExternalPayloadTests(unittest.TestCase):
    def test_a2_06_score_payload_vectors_match_mobile(self):
        from services.satisfaction_fetcher import _classify_score_payload

        vectors = [
            ({"result": None}, "empty"), ({"result": {}}, "empty"),
            ({"result": {"STSFDG_SCORE": 5, "STSFDG_CAUSE": "빠른 처리"}}, "result"),
            ({}, "unknown"), ({"error": "session_expired"}, "unknown"), ({"error": "x", "result": None}, "unknown"),
            ({"result": []}, "unknown"), ({"result": "oops"}, "unknown"), ([], "unknown"), (None, "unknown"),
        ]
        for payload, kind in vectors:
            with self.subTest(payload=payload):
                self.assertEqual(_classify_score_payload(payload)[0], kind)

    def test_a2_08_sunwi_missing_result_is_a_failure_not_zero(self):
        from services import sunwi_fetcher

        class Resp:
            def __init__(self, body):
                self.body = body

            def raise_for_status(self):
                return None

            def json(self):
                return self.body

        class Session:
            def __init__(self, body):
                self.body = body

            def get(self, *a, **k):
                return Resp(self.body)

        ok = sunwi_fetcher.fetch_stats(Session({"result": []}), "11", "110", logger_fn=lambda *_: None, max_attempts=1)
        self.assertEqual(ok, [])
        for bad in ({}, {"error": "x"}, {"result": None}, []):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                sunwi_fetcher.fetch_stats(Session(bad), "11", "110", logger_fn=lambda *_: None, max_attempts=1)


class StatsPopulationTests(unittest.TestCase):
    def test_a1_01_empty_projection_returns_empty_frames(self):
        from services import duplicate_group_service as dgs
        from services import report_stats_service as rss

        engine = _seeded_engine(self)
        # 투영 단계가 모든 행을 뺀 경우(EO R-03 뒤로 투영은 ProjectionContext.project_frame)
        with mock.patch.object(dgs.ProjectionContext, "project_frame", lambda self, df: df.iloc[0:0]):
            _, df_t, df_p, df_o = rss._load_stats_frames(engine, {}, "canonical")
        self.assertEqual((len(df_t), len(df_p), len(df_o)), (0, 0, 0))

    def test_a1_06_exclude_police_keeps_reports_without_agency(self):
        from services import report_stats_service as rss

        engine = _seeded_engine(self)
        table = database.merge_other_table
        with engine.begin() as conn:
            conn.execute(table.insert().values(ID="NOAGENCY1", 신고번호="SPP-NA-1", 처리상태="수용", 처리기관=None))
        query = rss._build_stats_query(table, {"excludePolice": True}, column_names=["ID"])
        with engine.connect() as conn:
            ids = {r[0] for r in conn.execute(query)}
        self.assertIn("NOAGENCY1", ids)

    def test_a1_05_note_only_update_keeps_manual_representative(self):
        from services import duplicate_group_service as dgs

        engine = _seeded_engine(self)
        with engine.connect() as conn:
            group_id = conn.execute(select(models.duplicate_group_table.c.group_id)).scalar()
            members = [r[0] for r in conn.execute(select(models.duplicate_member_table.c.report_id)
                                                  .where(models.duplicate_member_table.c.group_id == group_id))]
        chosen = sorted(members)[-1]
        self.assertTrue(dgs.update_duplicate_group(engine, group_id, duplicate_status="confirmed_duplicate",
                                                   representative_mode="manual", representative_id=chosen))
        self.assertTrue(dgs.update_duplicate_group(engine, group_id, note="메모만 수정"))
        with engine.connect() as conn:
            g = conn.execute(select(models.duplicate_group_table).where(
                models.duplicate_group_table.c.group_id == group_id)).mappings().one()
        self.assertEqual((g["representative_mode"], g["representative_id"], g["note"]), ("manual", chosen, "메모만 수정"))

    def test_b04_page_projection_map_matches_the_full_map_for_its_groups(self):
        from services import duplicate_group_service as dgs

        engine = _seeded_engine(self)
        with engine.connect() as conn:
            _, full = dgs.build_projection_map(conn)
            some = sorted(full)[:2]
            _, scoped = dgs.build_projection_map(conn, some)
            self.assertEqual(dgs.build_projection_map(conn, []), ({}, {}))
        groups = {full[i]["group_id"] for i in some}
        self.assertEqual(scoped, {rid: meta for rid, meta in full.items() if meta["group_id"] in groups})

    def test_b03_missing_summary_matches_the_full_missing_list(self):
        from services import report_stats_service as rss

        engine = _seeded_engine(self)
        for category in ("all", "traffic", "parking", "other"):
            with self.subTest(category=category):
                full = rss.get_report_map_missing_groups(engine, category=category)["meta"]
                summary = rss.get_report_map_missing_summary(engine, category=category)
                self.assertEqual(summary, {"group_count": full["group_count"], "report_count": full["report_count"]})

    def test_b05_date_range_helper_predicates_keep_the_same_rows(self):
        from services import report_stats_service as rss

        engine = _seeded_engine(self)
        table = database.merge_traffic_table
        filters = {"responseDateStart": "2026-01-01", "responseDateEnd": "2026-08-31"}
        with engine.connect() as conn:
            got = {r[0] for r in conn.execute(rss._build_stats_query(table, filters, column_names=["ID"]))}
            value = func.substr(table.c["답변일"], 1, 10)
            expected = {r[0] for r in conn.execute(select(table.c.ID).where(
                value >= "2026-01-01", value <= "2026-08-31", func.length(value) == 10))}
        self.assertEqual(got, expected)
        self.assertTrue(got, "fixture 에 기간 안 답변이 있어야 의미가 있다")

    def test_b08_agency_stats_api_equals_page_records(self):
        from services import report_stats_service as rss

        engine = _seeded_engine(self)
        records, _ = rss.get_stats_page(engine, {}, "canonical")
        self.assertEqual(rss.get_agency_stats(engine, {}, "canonical"), records)


class EditorAndSetupTests(unittest.TestCase):
    def test_a1_10_editor_rejects_non_scalar_values(self):
        from services import db_editor_service

        engine = _seeded_engine(self)
        with engine.connect() as conn:
            record_id = conn.execute(select(models.detail_traffic_table.c.ID)).scalar()
        for bad in ({"x": 1}, [1], True):
            with self.subTest(bad=bad), self.assertRaises(db_editor_service.InvalidEditorValue):
                db_editor_service.update_record(engine, "traffic", record_id, {"처리기관": bad})

    def test_a1_08_concurrent_first_setup_creates_one_admin(self):
        engine = _seeded_engine(self)
        with engine.begin() as conn:
            conn.execute(models.admin_users_table.delete())
        results = []
        barrier = threading.Barrier(4)

        def worker(i):
            barrier.wait()
            results.append(database.create_first_admin_user(engine, f"admin{i}", "pass1234"))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        with engine.connect() as conn:
            count = conn.execute(select(func.count()).select_from(models.admin_users_table)).scalar()
        self.assertEqual((results.count(True), count), (1, 1))


class CacheFlightTests(unittest.TestCase):
    def test_b06_waiters_share_a_result_too_large_to_cache(self):
        from services import report_cache

        engine = _seeded_engine(self)
        calls = []
        started = threading.Event()
        release = threading.Event()

        @report_cache.cached
        def big(engine_):
            calls.append(1)
            started.set()
            release.wait(5)
            return {"rows": list(range(10))}

        out = []
        with mock.patch.object(report_cache, "_byte_limit", 1):
            first = threading.Thread(target=lambda: out.append(big(engine)))
            first.start()
            started.wait(5)
            waiters = [threading.Thread(target=lambda: out.append(big(engine))) for _ in range(3)]
            for w in waiters:
                w.start()
            import time
            time.sleep(0.2)
            release.set()
            for t in [first, *waiters]:
                t.join(10)
        report_cache.clear()
        self.assertEqual(len(calls), 1, "이미 기다리던 요청은 같은 결과를 나눠 받는다")
        self.assertEqual(len(out), 4)


class SessionAndTokenTests(unittest.TestCase):
    def test_a2_01_token_is_bound_to_its_username(self):
        from core.crawler import direct_login

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        import settings.settings as settings
        with mock.patch.object(settings, "datapath", tmp.name), mock.patch.object(settings, "username", "user-a"):
            future = 4102444800.0  # 2100-01-01
            direct_login.save_token({"access_token": "tok-a", "expires_at": future, "username": "user-a"})
            self.assertTrue(direct_login.is_token_valid())
            with mock.patch.object(settings, "username", "user-b"):
                self.assertFalse(direct_login.is_token_valid(), "계정을 바꾸면 이전 계정 토큰은 무효")
            direct_login.save_token({"access_token": "legacy", "expires_at": future})
            self.assertFalse(direct_login.is_token_valid(), "아이디가 기록되지 않은 옛 토큰은 한 번 다시 로그인")
            direct_login.invalidate_token()
            self.assertIsNone(direct_login.load_token())

    def test_a2_01_login_does_not_store_a_token_when_the_account_changed_meanwhile(self):
        from core.crawler import direct_login

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        import settings.settings as settings

        def slow_login(username, password):
            settings.username = "user-b"  # 로그인하는 동안 설정이 바뀌었다
            return {"access_token": "tok-a", "expires_at": 4102444800.0}

        with mock.patch.object(settings, "datapath", tmp.name), mock.patch.object(settings, "username", "user-a"), \
                mock.patch.object(settings, "password", "pw"), mock.patch.object(direct_login, "_login_once", slow_login):
            with self.assertRaises(RuntimeError):
                direct_login.login_and_cache()
            self.assertIsNone(direct_login.load_token())


class CrawlChangesTests(unittest.TestCase):
    def test_a2_05_changes_file_is_consumed_once(self):
        from services import crawl_state_store

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with mock.patch.object(crawl_state_store, "_state_file", lambda name: os.path.join(tmp.name, name)):
            crawl_state_store._write_json(os.path.join(tmp.name, "crawl_changes.json"), [{"ID": "1"}])
            self.assertEqual(crawl_state_store.peek_crawl_changes(), [{"ID": "1"}])
            crawl_state_store.clear_crawl_changes()
            self.assertEqual(crawl_state_store.peek_crawl_changes(), [])


class GateOwnershipTests(unittest.TestCase):
    STATUS = {"gate": {"kakao": True}, "contributor": {"status": "active"},
              "consent": {"state": "active", "policy_version": "P", "consent_text_sha256": "H"},
              "policy": {"required_version": "P", "consent_text_sha256": "H"}}

    def _gate(self, session_user):
        from services import community_gate

        gate = community_gate._Gate(clock=lambda: 100.0)
        gate._owner = "ok"
        service = mock.Mock()
        service.config.return_value = mock.Mock()
        patches = [
            mock.patch.object(community_gate.cas, "get_service", return_value=service),
            mock.patch.object(community_gate, "config_state", return_value="ok"),
            mock.patch.object(community_gate._Gate, "_session_state",
                              staticmethod(lambda _service: ("valid", {"user_id": session_user[0]}))),
            mock.patch.object(community_gate._Gate, "_deactivate", staticmethod(lambda reason: None)),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return gate

    def test_d2_01_status_cached_for_another_user_is_not_used(self):
        user = ["user-a"]
        gate = self._gate(user)
        with gate._lock:
            gate._status, gate._verified_at, gate._invalidated, gate._status_user = self.STATUS, 100.0, False, "user-a"
        self.assertTrue(gate.evaluate()["can_enter"])
        user[0] = "user-b"  # 로그인 확정 직후(complete 재시도 중) — 캐시는 이전 계정 것
        result = gate.evaluate()
        self.assertFalse(result["can_enter"])
        self.assertEqual(result["state"], "verification_required")

    def test_d2_01_late_status_after_invalidation_is_discarded(self):
        user = ["user-a"]
        gate = self._gate(user)
        generation = gate._generation
        gate._mark_invalid()  # 조회를 기다리는 동안 로그인·로그아웃이 있었다
        with gate._lock:
            self.assertFalse(gate._still_current(None, generation, "user-a"))
            self.assertTrue(gate._still_current(None, gate._generation, "user-a"))
            user[0] = "user-b"
            self.assertFalse(gate._still_current(None, gate._generation, "user-a"))


class SessionKeyResetTests(unittest.TestCase):
    def test_d2_04_reset_archives_a_corrupt_key_with_its_files(self):
        from services.community_auth_store import CommunitySessionStore, StoreUnreadable

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = CommunitySessionStore(tmp.name)
        store.save({"current": {"user_id": "u", "access_token": "a"}})
        with open(store.key_path, "wb") as fh:
            fh.write(b"not-a-fernet-key")
        with self.assertRaises(StoreUnreadable):
            store.load()
        moved = store.reset_unreadable()
        self.assertIsNotNone(moved)
        self.assertFalse(os.path.exists(store.key_path))
        self.assertFalse(os.path.exists(store.session_path))
        names = os.listdir(store.auth_dir)
        self.assertTrue(any(n.startswith(os.path.basename(store.key_path) + ".unreadable-") for n in names))
        store.save({"current": {"user_id": "u2", "access_token": "b"}})  # 새 키로 다시 시작할 수 있다
        self.assertEqual(store.load()["current"]["user_id"], "u2")

    def test_d2_04_corrupt_key_without_session_is_reported(self):
        from services.community_auth_store import CommunitySessionStore, StoreUnreadable

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = CommunitySessionStore(tmp.name)
        os.makedirs(store.auth_dir, exist_ok=True)
        with open(store.key_path, "wb") as fh:
            fh.write(b"broken")
        with self.assertRaises(StoreUnreadable):
            store.load()


if __name__ == "__main__":
    unittest.main()
