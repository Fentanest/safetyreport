"""DB 최적화 작업: 상세 원본 기반 재파싱 (2026-10-17 명세 3절).

합성 원본·가짜 세션/응답만 쓴다. 실데이터·운영 DB·외부 요청 없음.
"""
import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from sqlalchemy import select

import settings.settings as settings
from core.database import models
from core.database.engine import get_engine
from core.utils import logger, raw_detail
from services import db_optimize, maintenance_service
from services.community_capture import PARSER_VERSION
from services.community_store import CommunityStore


def _envelope(report_id, seed="", status="수용", agency="서울특별시 강서경찰서 교통과",
              amount_sentence="과태료 40,000원을 부과하였습니다.", c_now=10):
    return {
        "resultCode": "0000",
        "result": {
            "C_NO": int(report_id),
            "C_NAME": "홍길동" + seed,
            "C_EMAIL": "a@b.c",
            "C_PHONE": "01012345678",
            "C_PHONE2": "010-1234-5678",
            "C_ID": "fixtureid1",
            "C_NOW": c_now,
            "C_A_CONTENTS": ("본 신고는 안전신문고 앱의 자동차·교통위반 메뉴로 접수된 신고입니다\n"
                             "* 차량번호 : 12가3456\n* 발생일자 : 2026-09-01\n* 발생시각 : 08:00\n신고합니다" + seed),
            "RN_ADRES": "서울특별시 강서구 마곡동 1",
            "STTEMNT_NO": "SPP-2512-9000001",
            "C_A_TITLE": "(자동차·교통위반) 신호위반",
            "C_DATE": "2025-12-30 23:40:00",
            "answers": [
                {"C_MANAGER_TYPE_NM": status,
                 "C_MANAGE_CONTENTS": amount_sentence,
                 "C_MANAGE_ORG_NAME": agency,
                 "C_MANAGE_MAN": "김담당",
                 "C_DATE": "2026-01-02"},
            ],
        },
    }


def _insert_raw(engine, report_id, envelope, source="crawl"):
    canonical = raw_detail.canonical_bytes(envelope)
    with engine.begin() as conn:
        conn.execute(models.report_detail_raw_table.insert().values(
            ID=report_id, raw_zlib=raw_detail.compress(canonical),
            raw_sha256=raw_detail.sha256_hex(canonical), raw_size=len(canonical),
            fetched_at="2026-10-17T12:00:00.000Z", source=source))


def _report_ids(engine):
    with engine.connect() as conn:
        return [str(i) for i in conn.execute(
            select(models.report_table.c.ID).order_by(models.report_table.c.ID)).scalars().all()]


def _journal_report_ids():
    store = CommunityStore.open()
    return {r["source_report_id"] for r in store.connect().execute(
        "SELECT source_report_id FROM source_journal").fetchall()}


def _journal_count():
    store = CommunityStore.open()
    return store.connect().execute("SELECT COUNT(*) v FROM source_journal").fetchone()["v"]


def _reset_optimize_state():
    # 정리 중 후속 스레드 생성을 막고 살아 있는 스레드를 끝까지 기다린다 — 멈춤으로 끝난
    # 스레드의 자동 재시작이 다음 시험 DB 로 쓰는 오염을 막는다.
    with db_optimize._lock:
        db_optimize._auto_restart = False
    try:
        db_optimize._stop.set()
        with db_optimize._lock:
            thread = db_optimize._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=15)
    finally:
        with db_optimize._lock:
            db_optimize._thread = None
            db_optimize._state.update({"state": "idle", "total": 0, "done": 0, "stage": "",
                                       "message": "", "log": [], "finished_at": ""})
            db_optimize._suppress_restart = False
            db_optimize._auto_restart = True
        db_optimize._stop.clear()
        db_optimize.resume_after_restore()


class OptimizeBase(unittest.TestCase):
    def setUp(self):
        logger.LoggerFactory.create_logger(mode="crawl")
        get_engine().dispose()
        for ext in ("", "-wal", "-shm"):
            if os.path.exists(settings.db_path + ext):
                os.remove(settings.db_path + ext)
        # 커뮤니티 저장소·재시도 의도·원본 파일·크롤 로그·대기열을 깨끗이 한다(공유 DATA_DIR).
        cpath = os.path.join(settings.datapath, "community.db")
        try:
            CommunityStore.open().close()
        except Exception:
            pass
        CommunityStore._forget(cpath)
        for ext in ("", "-wal", "-shm", "-journal"):
            if os.path.exists(cpath + ext):
                os.remove(cpath + ext)
        retry = os.path.join(settings.datapath, "community_capture_retry.json")
        if os.path.exists(retry):
            os.remove(retry)
        shutil.rmtree(settings.raw_details_dir, ignore_errors=True)
        crawl_log = os.path.join(settings.logpath, "current_crawl.log")
        if os.path.exists(crawl_log):
            os.remove(crawl_log)
        from services.crawl_manager import crawl_manager

        # 대기열 싱글톤을 완전히 비운다(번호·알림 receipt·파일까지 — 다음 모듈의 대기열 시험과 공유되므로).
        with crawl_manager._state_lock:
            crawl_manager._pending_queue.clear()
            crawl_manager._notification_receipts = {}
            crawl_manager._notification_again = set()
            crawl_manager._reserved.clear()
            crawl_manager._pending_loaded = True
            try:
                crawl_manager._save_pending_locked()
            except OSError:
                pass
        pending_file = os.path.join(settings.datapath, crawl_manager.PENDING_FILE)
        if os.path.exists(pending_file):
            os.remove(pending_file)
        with crawl_manager._state_lock:
            timer, crawl_manager._retry_timer = crawl_manager._retry_timer, None
        if timer is not None:
            timer.cancel()
        # 최적화 전역 상태 초기화(다음 모듈의 크롤 시험이 게이트에 걸리지 않게 setUp·tearDown 모두).
        _reset_optimize_state()
        self.stop = threading.Event()
        # 공유 캡처 계정 문맥은 고정(저널은 쓰되 전송함에는 넣지 않음).
        ctx_patch = mock.patch("services.community_cloud.capture_context",
                               side_effect=lambda ctx: (ctx, "none"))
        ctx_patch.start()
        self.addCleanup(ctx_patch.stop)
        launch_patch = mock.patch.object(crawl_manager, "request_pending_launch")
        self.launch_mock = launch_patch.start()
        self.addCleanup(launch_patch.stop)
        from scripts.dev import fixture_server

        fixture_server.seed_engine(get_engine())
        self.engine = get_engine()

    def tearDown(self):
        self.stop.set()
        get_engine().dispose()
        _reset_optimize_state()
        from services.crawl_manager import crawl_manager

        with crawl_manager._state_lock:
            crawl_manager._pending_queue.clear()
            crawl_manager._notification_receipts = {}
            crawl_manager._notification_again = set()
            crawl_manager._reserved.clear()
            crawl_manager._pending_loaded = True
            try:
                crawl_manager._save_pending_locked()
            except OSError:
                pass
            timer, crawl_manager._retry_timer = crawl_manager._retry_timer, None
        if timer is not None:
            timer.cancel()
        pending_file = os.path.join(settings.datapath, crawl_manager.PENDING_FILE)
        if os.path.exists(pending_file):
            os.remove(pending_file)

    def _run(self, **kwargs):
        kwargs.setdefault("is_crawling", lambda: False)
        kwargs.setdefault("rebuild_pending", lambda: False)
        kwargs.setdefault("interval", 0)
        kwargs.setdefault("wait_seconds", 0)
        kwargs.setdefault("login_retry_seconds", 0.01)
        kwargs.setdefault("sleep", lambda s: None)
        kwargs.setdefault("stop", self.stop)
        return db_optimize.process_all(self.engine, **kwargs)

    def _no_network(self):
        def _boom(*args, **kwargs):
            raise AssertionError("네트워크를 쓰면 안 됨")
        return {"session_factory": _boom, "fetch_fn": _boom}


class MetaTests(OptimizeBase):
    def test_needs_run_without_meta(self):
        self.assertTrue(db_optimize.needs_run(self.engine))

    def test_needs_run_with_old_target(self):
        db_optimize.write_meta(self.engine, {"target": "pc-parser-1", "cursor": "", "done": True})
        self.assertTrue(db_optimize.needs_run(self.engine))

    def test_no_run_when_done_with_current_target(self):
        db_optimize.write_meta(self.engine, {"target": PARSER_VERSION, "cursor": "90000001",
                                              "done": True, "total": 24, "stats": {}})
        self.assertFalse(db_optimize.needs_run(self.engine))
        before = db_optimize.read_meta(self.engine)
        self.assertIsNone(db_optimize.maybe_start(self.engine))
        self.assertFalse(db_optimize.is_running())
        self.assertEqual(db_optimize.read_meta(self.engine), before)

    def test_maybe_start_runs_in_background_and_finishes(self):
        ids = _report_ids(self.engine)
        for rid in ids:
            _insert_raw(self.engine, rid, _envelope(rid))
        thread = db_optimize.maybe_start(
            self.engine, is_crawling=lambda: False, rebuild_pending=lambda: False,
            interval=0, wait_seconds=0, sleep=lambda s: None, stop=self.stop,
            **self._no_network())
        self.assertIsNotNone(thread)
        thread.join(timeout=30)
        self.assertFalse(thread.is_alive())
        meta = db_optimize.read_meta(self.engine)
        self.assertEqual((meta["target"], meta["done"]), (PARSER_VERSION, True))
        self.assertTrue(self.launch_mock.called)

    def test_wait_for_turn_waits_for_crawl_then_starts(self):
        calls = {"n": 0}

        def crawling():
            calls["n"] += 1
            return calls["n"] <= 2

        self.assertTrue(db_optimize.wait_for_turn(is_crawling=crawling,
                                                  rebuild_pending=lambda: False,
                                                  wait_seconds=0, stop=self.stop))
        self.assertGreaterEqual(calls["n"], 3)


class ReuseTests(OptimizeBase):
    def test_stored_raw_is_reparsed_without_network(self):
        ids = _report_ids(self.engine)
        for rid in ids:
            _insert_raw(self.engine, rid, _envelope(rid))
        result = self._run(**self._no_network())
        self.assertTrue(result["finished"])
        self.assertEqual(result["stats"].get("reused"), len(ids))
        self.assertEqual(result["stats"].get("refetched", 0), 0)
        self.assertEqual(result["done"], len(ids))
        meta = db_optimize.read_meta(self.engine)
        self.assertEqual((meta["target"], meta["done"], meta["cursor"]),
                         (PARSER_VERSION, True, ids[-1]))

    def test_cursor_resumes_after_restart(self):
        ids = _report_ids(self.engine)
        for rid in ids:
            _insert_raw(self.engine, rid, _envelope(rid))
        db_optimize.write_meta(self.engine, {"target": PARSER_VERSION, "cursor": ids[0],
                                              "done": False, "total": len(ids), "stats": {}})
        seen = []
        real = db_optimize._save_reparsed
        with mock.patch.object(db_optimize, "_save_reparsed",
                               side_effect=lambda *a, **k: (seen.append(a[2]), real(*a, **k))[1]):
            result = self._run(**self._no_network())
        self.assertNotIn(ids[0], seen)
        self.assertEqual(seen, ids[1:])
        self.assertTrue(result["finished"])

    def test_second_identical_run_creates_no_share_event(self):
        ids = _report_ids(self.engine)
        for rid in ids:
            _insert_raw(self.engine, rid, _envelope(rid))
        first = self._run(**self._no_network())
        journals_after_first = _journal_count()
        # 커서를 지우고 처음부터 다시 훑어도 같은 원본이면 바뀐 게 없어 이벤트가 없다.
        db_optimize.write_meta(self.engine, {"target": PARSER_VERSION, "cursor": "",
                                              "done": False, "total": len(ids), "stats": {}})
        second = self._run(**self._no_network())
        self.assertEqual(second["stats"].get("reused"), len(ids))
        self.assertEqual(_journal_count(), journals_after_first)
        self.assertEqual(second["stats"].get("events", 0), 0)
        self.assertEqual(second["stats"].get("changed", 0), 0)
        self.assertGreater(first["stats"].get("changed", 0), 0)

    def test_only_reparsed_change_emits_share_event(self):
        ids = _report_ids(self.engine)
        for rid in ids:
            _insert_raw(self.engine, rid, _envelope(rid))
        self._run(**self._no_network())
        journals_before = _journal_count()
        # 한 신고의 원본이 바뀌면(답변 변경) 다시 훑을 때 그 신고만 바뀌고 공유 이벤트도 그 신고만 난다.
        with self.engine.begin() as conn:
            conn.execute(models.report_detail_raw_table.delete().where(
                models.report_detail_raw_table.c.ID == ids[0]))
        _insert_raw(self.engine, ids[0], _envelope(ids[0], seed="바뀜", status="불수용",
                                                   agency="경기도 부천원미경찰서",
                                                   amount_sentence="처분이 불가합니다.", c_now=14))
        db_optimize.write_meta(self.engine, {"target": PARSER_VERSION, "cursor": "",
                                              "done": False, "total": len(ids), "stats": {}})
        result = self._run(**self._no_network())
        self.assertEqual(result["stats"].get("changed"), 1)
        self.assertEqual(result["stats"].get("events"), 1)
        self.assertEqual(_journal_count(), journals_before + 1)
        self.assertIn(ids[0], _journal_report_ids())


class RefetchTests(OptimizeBase):
    def test_missing_raw_is_fetched_saved_and_written(self):
        ids = _report_ids(self.engine)
        calls = []

        def fetch(report_id):
            calls.append(report_id)
            return _envelope(report_id)

        result = self._run(session_factory=lambda: object(), fetch_fn=fetch)
        self.assertTrue(result["finished"])
        self.assertEqual(result["stats"].get("refetched"), len(ids))
        self.assertEqual(sorted(calls), ids)
        with self.engine.connect() as conn:
            rows = {r["ID"]: dict(r) for r in conn.execute(
                select(models.report_detail_raw_table)).mappings()}
        self.assertEqual(set(rows), set(ids))
        for rid, row in rows.items():
            self.assertEqual(row["source"], "backfill")
            payload = raw_detail.verify(row["raw_zlib"], row["raw_sha256"])
            self.assertEqual(payload, raw_detail.canonical_bytes(_envelope(rid)))
            path = Path(settings.raw_details_dir) / f"{rid}.json"
            self.assertTrue(path.is_file())
            stored = json.loads(path.read_text(encoding="utf-8"))
            for key in raw_detail.REPORTER_KEYS:
                self.assertNotIn(key, stored["result"])

    def test_corrupt_raw_is_fetched_again(self):
        ids = _report_ids(self.engine)
        for rid in ids:
            _insert_raw(self.engine, rid, _envelope(rid))
        with self.engine.begin() as conn:
            row = conn.execute(select(models.report_detail_raw_table.c.raw_zlib).where(
                models.report_detail_raw_table.c.ID == ids[0])).scalar()
            broken = bytes([row[0] ^ 0xFF]) + row[1:]
            conn.execute(models.report_detail_raw_table.update().where(
                models.report_detail_raw_table.c.ID == ids[0]).values(raw_zlib=broken))
        fetched = []

        def fetch(report_id):
            fetched.append(report_id)
            return _envelope(report_id)

        result = self._run(session_factory=lambda: object(), fetch_fn=fetch)
        self.assertEqual(fetched, [ids[0]])
        self.assertEqual(result["stats"].get("refetched"), 1)
        with self.engine.connect() as conn:
            row = conn.execute(select(models.report_detail_raw_table).where(
                models.report_detail_raw_table.c.ID == ids[0])).mappings().first()
        self.assertEqual(raw_detail.verify(row["raw_zlib"], row["raw_sha256"]),
                         raw_detail.canonical_bytes(_envelope(ids[0])))

    def test_login_required_pauses_and_resumes(self):
        ids = _report_ids(self.engine)
        attempts = {"n": 0}

        def factory():
            attempts["n"] += 1
            if attempts["n"] <= 2:
                raise RuntimeError("로그인 필요")
            return object()

        result = self._run(session_factory=factory,
                            fetch_fn=lambda rid: _envelope(rid))
        self.assertTrue(result["finished"])
        self.assertEqual(attempts["n"], 3)
        self.assertEqual(result["stats"].get("refetched"), len(ids))
        log_text = "\n".join(db_optimize.job_for_status()["log"])
        self.assertIn("로그인 필요", log_text)
        self.assertIn("이어서", log_text)

    def test_unreceivable_report_is_skipped_with_reason(self):
        ids = _report_ids(self.engine)

        def fetch(report_id):
            if report_id == ids[0]:
                return {"error": "HTTP 404"}
            return _envelope(report_id)

        result = self._run(session_factory=lambda: object(), fetch_fn=fetch)
        self.assertTrue(result["finished"])
        self.assertEqual(result["stats"]["skipped"].get("not_found"), 1)
        self.assertEqual(result["stats"].get("refetched"), len(ids) - 1)
        log_text = "\n".join(db_optimize.job_for_status()["log"])
        self.assertIn(f"{ids[0]} 건너뜀(not_found", log_text)
        meta = db_optimize.read_meta(self.engine)
        self.assertTrue(meta["done"])

    def test_unparsable_result_is_skipped(self):
        ids = _report_ids(self.engine)
        for rid in ids:
            envelope = _envelope(rid)
            if rid == ids[0]:
                envelope["result"]["answers"] = [123]  # 파서가 예외를 내는 모양
            _insert_raw(self.engine, rid, envelope)
        result = self._run(**self._no_network())
        self.assertTrue(result["finished"])
        self.assertEqual(result["stats"]["skipped"].get("parse_failed"), 1)
        self.assertEqual(result["stats"].get("reused"), len(ids) - 1)

    def test_missing_file_is_created_from_stored_raw(self):
        ids = _report_ids(self.engine)
        for rid in ids:
            _insert_raw(self.engine, rid, _envelope(rid))
        self._run(**self._no_network())
        for rid in ids:
            self.assertTrue((Path(settings.raw_details_dir) / f"{rid}.json").is_file())


class SaveScopeTests(OptimizeBase):
    def test_parser_columns_change_but_list_and_override_stay(self):
        from core.storage import reports_repo

        rid = "90000001"
        with self.engine.connect() as conn:
            before = conn.execute(select(models.report_table).where(
                models.report_table.c.ID == rid)).mappings().first()
        list_before = {c: before[c] for c in ("상태", "신고번호", "신고명", "신고일")}
        with self.engine.begin() as conn:
            conn.execute(models.report_table.update().where(models.report_table.c.ID == rid)
                           .values(상태="진행"))  # 목록 값 Tamper — 재파싱이 덮으면 안 됨
            conn.execute(models.report_override_table.insert().values(
                ID=rid, column_name="처리기관", value="사용자 수정 기관", updated_at=1))
            reports_repo.refresh_effective_rows(conn, [rid])
        # 원본의 답변이 불수용이면 파서 열(처리상태·처리기관)은 바뀐다.
        fetched = _envelope(rid, status="불수용", agency="새 처리기관",
                            amount_sentence="처분이 불가합니다.", c_now=14)
        result = self._run(session_factory=lambda: object(),
                            fetch_fn=lambda report_id: fetched if report_id == rid else _envelope(report_id))
        self.assertTrue(result["finished"])
        with self.engine.connect() as conn:
            after = conn.execute(select(models.report_table).where(
                models.report_table.c.ID == rid)).mappings().first()
            effective = conn.execute(select(models.report_effective_table).where(
                models.report_effective_table.c.ID == rid)).mappings().first()
        # 파서 열은 바뀐다.
        self.assertEqual(after["처리상태"], "불수용")
        self.assertEqual(after["처리기관"], "새 처리기관")
        self.assertEqual(effective["처리상태"], "불수용")
        # 목록 열은 그대로.
        self.assertEqual(after["상태"], "진행")
        for column in ("신고번호", "신고명", "신고일"):
            self.assertEqual(after[column], list_before[column])
        # 사용자 수정값은 화면용 표에 그대로 얹힌다.
        self.assertEqual(effective["처리기관"], "사용자 수정 기관")


class CrawlOrderTests(OptimizeBase):
    def _start_optimize(self):
        with db_optimize._lock:
            db_optimize._state.update({"state": "running", "stage": "running",
                                       "total": 24, "done": 3})

    def _crawl_log(self):
        path = os.path.join(settings.logpath, "current_crawl.log")
        if not os.path.exists(path):
            return ""
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    def test_single_crawl_is_queued_behind_optimize(self):
        from services import crawl_control
        from services.crawl_manager import crawl_manager

        self._start_optimize()
        with mock.patch.object(crawl_control, "_check_crawl_allowed", return_value=None), \
                mock.patch.object(crawl_control, "_refuse_ambiguous", return_value=None):
            result = crawl_control.enqueue_report("SPP-2609-9999999")
        self.assertEqual(result["status"], "queued")
        self.assertEqual(result.get("queued_reason"), "db_optimize")
        self.assertIn("SPP-2609-9999999", crawl_manager.pending_items())
        self.assertFalse(crawl_manager.is_crawling())
        self.assertIn(db_optimize.DEFER_LOG_LINE, self._crawl_log())

    def test_notification_is_queued_behind_optimize(self):
        from services import crawl_control

        self._start_optimize()
        request_id = "inbox.enqueue.native.notification.11111111-2222-3333-8444-555555555555"
        with mock.patch.object(crawl_control, "_check_crawl_allowed", return_value=None), \
                mock.patch.object(crawl_control, "_refuse_ambiguous", return_value=None):
            result = crawl_control.enqueue_notification(
                "SPP-2609-9000001", request_id, "fixture-key")
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result.get("queued_reason"), "db_optimize")
        self.assertIn(db_optimize.DEFER_LOG_LINE, self._crawl_log())

    def test_manual_start_is_deferred_not_started(self):
        from services import crawl_control

        self._start_optimize()
        with mock.patch.object(crawl_control, "_check_crawl_allowed", return_value=None):
            with self.assertRaises(crawl_control.CrawlDeferredByOptimize) as ctx:
                crawl_control.start_crawl(crawl_mode="full", queue_list="SPP-2609-9000001",
                                          queue_filename="queue.txt", header="=== 테스트 ===",
                                          broadcast_source="web_start")
        self.assertEqual(ctx.exception.queued["status"], "queued")
        self.assertEqual(ctx.exception.queued.get("queued_reason"), "db_optimize")
        self.assertIn(db_optimize.DEFER_LOG_LINE, self._crawl_log())

    def test_manual_full_start_is_deferred_and_restarted_after_finish(self):
        from services import crawl_control

        self._start_optimize()
        with mock.patch.object(crawl_control, "_check_crawl_allowed", return_value=None):
            with self.assertRaises(crawl_control.CrawlDeferredByOptimize):
                crawl_control.start_crawl(crawl_mode="full", queue_list="",
                                          queue_filename="queue.txt", header="=== 테스트 전체 ===",
                                          broadcast_source="web_start")
        with db_optimize._lock:
            db_optimize._state.update({"state": "completed", "stage": "done"})
        started = []
        with mock.patch.object(crawl_control, "start_crawl",
                               side_effect=lambda **k: started.append(k) or "log"):
            db_optimize._after_finish(self.engine)
        self.assertTrue(self.launch_mock.called)
        self.assertEqual(len(started), 1)
        self.assertEqual(started[0]["broadcast_source"], "web_start")

    def test_pending_launch_refuses_while_optimize_runs(self):
        from services.crawl_manager import crawl_manager

        self._start_optimize()
        crawl_manager.append_to_pending("SPP-2609-9000001")
        self.assertFalse(crawl_manager.launch_pending_crawl())
        self.assertIn("SPP-2609-9000001", crawl_manager.pending_items())
        with db_optimize._lock:
            db_optimize._state.update({"state": "idle", "stage": ""})
        with mock.patch.object(crawl_manager, "start_crawl", return_value=False):
            with mock.patch("services.crawl_control._check_crawl_allowed", return_value=None):
                self.assertFalse(crawl_manager.launch_pending_crawl())


class StatusTests(OptimizeBase):
    def test_status_has_db_optimize_job(self):
        with db_optimize._lock:
            db_optimize._state.update({"state": "running", "stage": "running",
                                       "total": 24, "done": 7})
        db_optimize.append_log("원본 채우기 7/24")
        data = maintenance_service.status(self.engine)
        self.assertTrue(data["active"])
        jobs = {j["key"]: j for j in data["jobs"]}
        self.assertIn("db_optimize", jobs)
        job = jobs["db_optimize"]
        self.assertEqual(job["label"], "DB 최적화 중입니다…")
        self.assertEqual((job["done"], job["total"], job["stage"]), (7, 24, "running"))
        self.assertLessEqual(len(job["log"]), 50)
        self.assertTrue(job["log"][0].split(" ", 1)[0].count("-") == 2)  # 시각+문구

    def test_status_keeps_existing_fields(self):
        data = maintenance_service.status(self.engine)
        self.assertEqual(set(data), {"active", "jobs"})
        self.assertFalse(data["active"])
        self.assertEqual(data["jobs"], [])

    def test_migration_events_are_visible_after_startup(self):
        db_optimize.record_migration_event("업그레이드 전 백업 완료(before_schema_v7_x.db)")
        db_optimize.record_migration_event("새 표 생성(스키마 7)")
        data = maintenance_service.status(self.engine)
        jobs = {j["key"]: j for j in data["jobs"]}
        self.assertIn("db_optimize", jobs)
        self.assertFalse(data["active"])
        self.assertIn("새 표 생성(스키마 7)", "\n".join(jobs["db_optimize"]["log"]))


if __name__ == "__main__":
    unittest.main()
