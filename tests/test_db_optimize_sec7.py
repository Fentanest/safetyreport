"""명세 7절(배포 전 검토 반영) 서버 시험. 실경로만 쓰고, 가짜는 네트워크 응답·세션만 쓴다.

Astra 재현 시나리오: 503 반복 → done 아님·partial·재기동 뒤 재시도 / 403 100건 →
auth 쉼·요청 간격 유지 / 진행 신고 새 수용 답변 재조회 → 저장 안 함·일반 크롤
대기열 등록·이후 일반 저장이 change / 직접 로그인 실패 → 크롤 즉시 실행 가능·
크롤 성공 뒤 최적화 재개 / 보류 등록과 완료 경합 → 배출 / 보류 저장 실패 → API 오류.
계약 벡터(`contracts/reparse-new-activity-vectors.json`, 수정 금지)와 저장 직전 재확인도 고정한다.
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
from services import db_optimize
from services.community_capture import PARSER_VERSION
from services.community_store import CommunityStore
from tests.test_db_optimize import _align_no_new_activity


def _envelope(report_id, status="수용", agency="서울특별시 강서경찰서 교통과",
             amount_sentence="과태료 40,000원을 부과하였습니다.", c_now=10,
             answer_date="2026-01-02"):
    return {
        "resultCode": "0000",
        "result": {
            "C_NO": int(report_id),
            "C_NAME": "홍길동",
            "C_EMAIL": "a@b.c",
            "C_PHONE": "01012345678",
            "C_PHONE2": "010-1234-5678",
            "C_ID": "fixtureid1",
            "C_NOW": c_now,
            "C_A_CONTENTS": ("본 신고는 안전신문고 앱의 자동차·교통위반 메뉴로 접수된 신고입니다\n"
                             "* 차량번호 : 12가3456\n* 발생일자 : 2026-09-01\n* 발생시각 : 08:00\n신고합니다"),
            "RN_ADRES": "서울특별시 강서구 마곡동 1",
            "STTEMNT_NO": "SPP-2512-9000001",
            "C_A_TITLE": "(자동차·교통위반) 신호위반",
            "C_DATE": "2025-12-30 23:40:00",
            "answers": [
                {"C_MANAGER_TYPE_NM": status,
                 "C_MANAGE_CONTENTS": amount_sentence,
                 "C_MANAGE_ORG_NAME": agency,
                 "C_MANAGE_MAN": "김담당",
                 "C_DATE": answer_date},
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


def _reset_optimize_state():
    with db_optimize._life_lock:
        db_optimize._shutdown = True
        db_optimize._stop.set()
        thread = db_optimize._thread
        retry_timer, db_optimize._retry_timer = db_optimize._retry_timer, None
    if retry_timer is not None:
        retry_timer.cancel()
    if thread is not None and thread is not threading.current_thread():
        thread.join(timeout=15)
    with db_optimize._life_lock:
        db_optimize._thread = None
        db_optimize._restore_tokens.clear()
        db_optimize._restore_pause = False
        db_optimize._pause_requested = False
        db_optimize._shutdown = False
        db_optimize._current_engine = None
        db_optimize._current_run_kwargs = {}
        db_optimize._state.update({"state": "idle", "total": 0, "done": 0, "stage": "",
                                   "message": "", "log": [], "finished_at": ""})
    try:
        db_optimize._crawl_success.clear()
    except AttributeError:
        pass
    db_optimize._stop.clear()


class Sec7Base(unittest.TestCase):
    def setUp(self):
        logger.LoggerFactory.create_logger(mode="crawl")
        get_engine().dispose()
        for ext in ("", "-wal", "-shm"):
            if os.path.exists(settings.db_path + ext):
                os.remove(settings.db_path + ext)
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
        _reset_optimize_state()
        self.stop = threading.Event()
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


class ClassifyTests(Sec7Base):
    def test_403_401_login_screen_are_auth_404_410_permanent(self):
        from core.crawler import crawldetail_api

        for note in ("HTTP 401", "HTTP 419", "HTTP 403", "HTTP 403 Forbidden",
                     "login page", "LOGIN REQUIRED", "로그인 페이지입니다",
                     "unauthorized: 세션 만료"):
            self.assertEqual(crawldetail_api.classify_fetch_error(note), "auth", note)
        for note in ("HTTP 404", "HTTP 410", "HTTP 404 Not Found"):
            self.assertEqual(crawldetail_api.classify_fetch_error(note), "permanent", note)
        for note in ("HTTP 500", "HTTP 502", "HTTP 503", "network: timeout",
                     "JSON parse: bad", "HTTP 429"):
            self.assertEqual(crawldetail_api.classify_fetch_error(note), "retryable", note)


class PartialAndRetryTests(Sec7Base):
    def test_all_503_is_partial_not_done_and_retried_after_restart(self):
        ids = _report_ids(self.engine)
        _align_no_new_activity(self.engine)

        def fetch(report_id):
            return {"error": "HTTP 503"}

        result = self._run(session_factory=lambda: object(), fetch_fn=fetch,
                           outage_retry_seconds=0)
        # 일시 실패가 done으로 기록되면 안 된다(Astra 1건).
        self.assertFalse(result["finished"])
        meta = db_optimize.read_meta(self.engine)
        self.assertFalse(meta.get("done"))
        self.assertEqual(sorted(meta.get("holds", [])), sorted(ids))
        self.assertFalse(db_optimize.is_running())
        # 재기동하면 보류 목록만 다시 시도한다.
        seen = []

        def fetch2(report_id):
            seen.append(report_id)
            return _envelope(report_id)

        result2 = self._run(session_factory=lambda: object(), fetch_fn=fetch2)
        self.assertTrue(result2["finished"])
        self.assertEqual(sorted(seen), sorted(ids))
        self.assertTrue(db_optimize.read_meta(self.engine).get("done"))

    def test_single_503_hold_retries_only_holds(self):
        ids = _report_ids(self.engine)
        target = ids[0]
        for rid in ids:
            if rid != target:
                _insert_raw(self.engine, rid, _envelope(rid))
        _align_no_new_activity(self.engine, target)

        def fetch(report_id):
            if report_id == target:
                return {"error": "HTTP 503"}
            return _envelope(report_id)

        result = self._run(session_factory=lambda: object(), fetch_fn=fetch,
                           outage_retry_seconds=0)
        self.assertFalse(result["finished"])
        meta = db_optimize.read_meta(self.engine)
        self.assertFalse(meta.get("done"))
        self.assertEqual(meta.get("holds", []), [target])
        with db_optimize._lock:
            stage = db_optimize._state.get("stage")
        self.assertEqual(stage, "partial")
        self.assertFalse(db_optimize.is_running())
        # 다음 회차는 보류만 다시 받는다.
        seen = []

        def fetch2(report_id):
            seen.append(report_id)
            return _envelope(report_id)

        result2 = self._run(session_factory=lambda: object(), fetch_fn=fetch2)
        self.assertTrue(result2["finished"])
        self.assertEqual(seen, [target])
        self.assertTrue(db_optimize.read_meta(self.engine).get("done"))

    def test_403_retry_then_done_keeps_interval(self):
        # 403이 와도 not_found로 끝내지 않고 로그인 쉼으로 멈춘다. 쉬는 동안 크롤 게이트는
        # 열려 있고(실행 중 아님), 실패한 요청에도 요청 간격 sleep이 불린다.
        ids = _report_ids(self.engine)
        _align_no_new_activity(self.engine)
        sleeps = []
        calls = {}

        def fetch(report_id):
            # 신고마다 3회 연속 403 뒤 정상 응답 — 시도 안에서는 해소되지 않아 로그인 쉼을 탄다.
            n = calls.get(report_id, 0) + 1
            calls[report_id] = n
            if n <= 3:
                return {"error": "HTTP 403"}
            return _envelope(report_id)

        stages = []
        real_update = db_optimize._update

        def hooked_update(**changes):
            if "stage" in changes:
                stages.append(changes["stage"])
            return real_update(**changes)

        with mock.patch.object(db_optimize, "_update", side_effect=hooked_update):
            result = self._run(session_factory=lambda: object(), fetch_fn=fetch,
                               interval=0.01, sleep=sleeps.append,
                               login_retry_seconds=0.01,
                               outage_retry_seconds=0.01,
                               max_rest_episodes=100)
        self.assertTrue(result["finished"])
        # 403은 로그인 쉼(login_required)을 거친다.
        self.assertIn("login_required", stages)
        meta = db_optimize.read_meta(self.engine)
        self.assertTrue(meta.get("done"))
        # 전역 장애 쉼은 신고별 시도 횟수를 늘리지 않는다.
        self.assertEqual(meta.get("attempts", {}), {})
        # 실패해도 요청 간격은 지킨다: 네트워크를 쓴 신고마다 sleep이 불렸다.
        self.assertGreaterEqual(len(sleeps), len(ids))

    def test_403_persistent_parks_without_attempts(self):
        # 403이 계속 와도 not_found로 끝내지 않고, 신고별 횟수 없이 보류로 주차한다.
        ids = _report_ids(self.engine)
        sleeps = []
        fetches = []

        def fetch(report_id):
            fetches.append(report_id)
            return {"error": "HTTP 403"}

        result = self._run(session_factory=lambda: object(), fetch_fn=fetch,
                           interval=0.01, sleep=sleeps.append,
                           login_retry_seconds=0.01,
                           outage_retry_seconds=0.01,
                           max_rest_episodes=2)
        self.assertFalse(result["finished"])
        meta = db_optimize.read_meta(self.engine)
        self.assertFalse(meta.get("done"))
        self.assertEqual(meta.get("attempts", {}), {})
        self.assertEqual(sorted(meta.get("holds", [])), sorted(ids))
        # 실패한 요청에도 요청 간격을 지킨다.
        self.assertEqual(len(sleeps), len(fetches))
        self.assertGreater(len(sleeps), 0)
        with db_optimize._lock:
            stage = db_optimize._state.get("stage")
        self.assertEqual(stage, "partial")
        self.assertFalse(db_optimize.is_running())
        log_text = "\n".join(db_optimize._state.get("log", []))
        self.assertIn("로그인", log_text)


class NewActivityTests(Sec7Base):
    def test_contract_vectors(self):
        from services import parser as doc_parser

        path = Path("contracts/reparse-new-activity-vectors.json")
        vectors = json.loads(path.read_text(encoding="utf-8"))
        table = {str(k): v for k, v in vectors["c_now_status"].items()}
        for key, label in table.items():
            self.assertEqual(db_optimize.c_now_status_label(int(key)), label)
            self.assertEqual(db_optimize.c_now_status_label(key), label)
        for case in vectors["cases"]:
            self.assertEqual(
                db_optimize.is_new_activity(
                    case["stored_status"], case["stored_answer_date"],
                    case["detail_c_now"], case["parsed_answer_date"]),
                case["new_activity"], case["name"])
        # 판정 함수는 parser._C_NOW_STATUS 를 그대로 쓴다.
        self.assertIs(db_optimize.c_now_status_table(), doc_parser._C_NOW_STATUS)

    def test_new_answer_is_forwarded_not_saved(self):
        from services import crawl_control
        from services.crawl_manager import crawl_manager

        ids = _report_ids(self.engine)
        target = ids[0]
        with self.engine.connect() as conn:
            row = conn.execute(select(models.report_table).where(
                models.report_table.c.ID == target)).mappings().first()
        report_no = row["신고번호"]
        self.assertTrue(report_no)
        with self.engine.begin() as conn:
            conn.execute(models.report_table.update().where(
                models.report_table.c.ID == target).values(상태="진행", 답변일=""))
        fresh = _envelope(target, status="수용", c_now=10, answer_date="2026-10-16")

        def fetch(report_id):
            if report_id == target:
                return fresh
            return _envelope(report_id)

        with mock.patch.object(crawl_control, "_check_crawl_allowed", return_value=None), \
                mock.patch.object(crawl_control, "_refuse_ambiguous", return_value=None):
            result = self._run(session_factory=lambda: object(), fetch_fn=fetch)
        self.assertTrue(result["finished"])
        # 저장하지 않는다: 원본 표·파일 모두 손대지 않음.
        with self.engine.connect() as conn:
            raw = conn.execute(select(models.report_detail_raw_table).where(
                models.report_detail_raw_table.c.ID == target)).first()
        self.assertIsNone(raw)
        self.assertFalse((Path(settings.raw_details_dir) / f"{target}.json").exists())
        with self.engine.connect() as conn:
            after = conn.execute(select(models.report_table).where(
                models.report_table.c.ID == target)).mappings().first()
        self.assertEqual(after["상태"], "진행")
        # 일반 크롤 대기열에 등록하고 로그를 남긴다. 보류가 아니라 처리 완료다.
        self.assertIn(report_no, crawl_manager.pending_items())
        log_text = "\n".join(db_optimize._state.get("log", []))
        self.assertIn("새 답변 — 일반 크롤로 넘김", log_text)
        meta = db_optimize.read_meta(self.engine)
        self.assertTrue(meta.get("done"))
        self.assertEqual(meta.get("holds", []), [])
        # 이후 일반 저장은 같은 답변을 변경으로 본다(알림 대상 보존).
        from core.crawler import detail_pipeline
        from core.storage import reports_repo
        from services import parser as doc_parser

        details = doc_parser.parse_json_details(fresh["result"])
        tup = detail_pipeline.build_detail_result(
            target, details, progress_status=details.get("progress_status", ""),
            satisfaction_client=None,
            satisfaction_fetcher=db_optimize._no_satisfaction,
            raw_envelope=None, raw_source="crawl")
        rec = reports_repo.CrawledDetail.from_legacy_tuple(tup)
        change = reports_repo._save_one(self.engine, rec)
        self.assertIsNotNone(change)
        self.assertEqual(change["id"], target)


class GateTests(Sec7Base):
    def _set_state(self, state, stage):
        with db_optimize._lock:
            db_optimize._state.update({"state": state, "stage": stage})

    def test_resting_optimize_does_not_block_crawl_gate(self):
        from services import crawl_control

        self._set_state("running", "running")
        self.assertTrue(db_optimize.is_running())
        self.assertTrue(crawl_control._optimize_running())
        for state, stage in [("paused", "login_required"), ("paused", "outage"),
                             ("paused", "partial"), ("paused", "waiting_crawl"),
                             ("paused", "waiting_rebuild")]:
            self._set_state(state, stage)
            self.assertFalse(db_optimize.is_running())
            self.assertFalse(crawl_control._optimize_running())

    def test_direct_login_failure_reuses_last_crawl_session(self):
        ids = _report_ids(self.engine)
        for rid in ids:
            if rid != "90000105":
                _insert_raw(self.engine, rid, _envelope(rid))
        sentinel = object()
        # 90000105(저장 상태 답변완료·답변일 2026-07-15)와 같은 새 활동 없는 응답 → 저장 경로.
        matching = _envelope("90000105", c_now=10, answer_date="2026-07-15")

        def boom():
            raise RuntimeError("직접 로그인 실패")

        def fetch(report_id):
            self.assertEqual(report_id, "90000105")
            return matching

        with mock.patch.object(db_optimize, "_reuse_last_crawl_session",
                               return_value=sentinel):
            result = self._run(session_factory=boom, fetch_fn=fetch)
        self.assertTrue(result["finished"])
        self.assertGreater(result["stats"].get("refetched", 0), 0)

    def test_crawl_success_resumes_holds(self):
        target = "90000105"
        db_optimize.write_meta(self.engine, {"target": PARSER_VERSION, "cursor": "",
                                             "done": False, "total": 1,
                                             "stats": {}, "attempts": {},
                                             "holds": [target]})
        matching = _envelope(target, c_now=10, answer_date="2026-07-15")
        with mock.patch.object(db_optimize, "_reuse_last_crawl_session",
                               return_value=None):
            db_optimize.on_crawl_success(
                self.engine, background=False, is_crawling=lambda: False,
                rebuild_pending=lambda: False, interval=0, wait_seconds=0,
                login_retry_seconds=0.01, sleep=lambda s: None, stop=self.stop,
                session_factory=lambda: object(),
                fetch_fn=lambda report_id: matching)
        meta = db_optimize.read_meta(self.engine)
        self.assertTrue(meta.get("done"))
        self.assertEqual(meta.get("holds", []), [])


class RaceTests(Sec7Base):
    def _full_entry(self):
        return {"crawl_mode": "full", "queue_filename": "queue.txt",
                "header": "=== 테스트 전체 ===", "broadcast_source": "web_start"}

    def test_defer_after_finish_drains_immediately(self):
        from services import crawl_control

        ids = _report_ids(self.engine)
        db_optimize.write_meta(self.engine, {"target": PARSER_VERSION,
                                             "cursor": ids[-1], "done": True,
                                             "total": len(ids), "stats": {},
                                             "attempts": {}})
        started = []
        with mock.patch.object(crawl_control, "start_crawl",
                               side_effect=lambda **k: started.append(k) or "log"):
            n = db_optimize.defer_full_crawl(self.engine, self._full_entry())
        self.assertEqual(n, 1)
        self.assertEqual(len(started), 1)
        self.assertEqual(db_optimize.peek_deferred_fulls(self.engine), [])

    def test_defer_save_failure_raises_for_api_error(self):
        import sqlalchemy.exc

        with mock.patch.object(self.engine, "begin",
                               side_effect=sqlalchemy.exc.OperationalError(
                                   "BEGIN", {}, Exception("database is locked"))):
            with self.assertRaises(RuntimeError):
                db_optimize.defer_full_crawl(self.engine, self._full_entry())

    def test_defer_failure_propagates_from_start_crawl(self):
        from services import crawl_control

        with db_optimize._lock:
            db_optimize._state.update({"state": "running", "stage": "running"})
        with mock.patch.object(crawl_control, "_check_crawl_allowed", return_value=None), \
                mock.patch.object(db_optimize, "defer_full_crawl",
                                  side_effect=db_optimize.DeferSaveFailed("boom")):
            with self.assertRaises(db_optimize.DeferSaveFailed):
                crawl_control.start_crawl(crawl_mode="full", queue_list="",
                                          queue_filename="queue.txt", header="h",
                                          broadcast_source="web_start")
        # 기존 오류 처리(RuntimeError → 오류 JSON)가 그대로 받는다(하위호환).
        self.assertIsInstance(db_optimize.DeferSaveFailed("boom"), RuntimeError)


class RecheckTests(Sec7Base):
    def _rec_for(self, report_id, envelope):
        from core.crawler import detail_pipeline
        from core.storage import reports_repo
        from services import parser as doc_parser

        details = doc_parser.parse_json_details(envelope["result"])
        tup = detail_pipeline.build_detail_result(
            report_id, details, progress_status=details.get("progress_status", ""),
            satisfaction_client=None,
            satisfaction_fetcher=db_optimize._no_satisfaction,
            raw_envelope=None, raw_source="backfill")
        return reports_repo.CrawledDetail.from_legacy_tuple(tup)

    def test_reparse_one_rejects_stale_snapshot(self):
        from core.storage import reports_repo

        rid = _report_ids(self.engine)[0]
        envelope = _envelope(rid)
        _insert_raw(self.engine, rid, envelope)
        rec = self._rec_for(rid, envelope)
        with self.engine.connect() as conn:
            current = conn.execute(select(models.report_table).where(
                models.report_table.c.ID == rid)).mappings().first()
        snapshot = {"답변일": current["답변일"], "처리내용": current["처리내용"],
                    "상태": current["상태"]}
        # 같은 스냅샷이면 저장한다(픽스처 행과 합성 원본이 달라 변경으로 잡힌다).
        change = reports_repo.reparse_one(self.engine, rec, trigger="reparse",
                                          expected=snapshot)
        self.assertIsNotNone(change)
        # 그 사이 답변이 바뀌었으면 저장하지 않는다.
        with self.engine.begin() as conn:
            conn.execute(models.report_table.update().where(
                models.report_table.c.ID == rid).values(답변일="2026-10-16"))
        with self.assertRaises(reports_repo._ReparseSkipped):
            reports_repo.reparse_one(self.engine, rec, trigger="reparse",
                                     expected=snapshot)
        with self.engine.connect() as conn:
            after = conn.execute(select(models.report_table).where(
                models.report_table.c.ID == rid)).mappings().first()
        self.assertEqual(after["답변일"], "2026-10-16")

    def test_process_all_passes_snapshot_to_reparse(self):
        ids = _report_ids(self.engine)
        rid = ids[0]
        for other in ids:
            _insert_raw(self.engine, other, _envelope(other))
        seen = {}
        real = db_optimize._save_reparsed

        def hooked(engine, rec, report_id, **kwargs):
            if report_id == rid:
                seen.update(kwargs.get("expected") or {})
            return real(engine, rec, report_id, **kwargs)

        def boom():
            raise AssertionError("no network")

        with mock.patch.object(db_optimize, "_save_reparsed", side_effect=hooked):
            result = self._run(session_factory=boom,
                               fetch_fn=lambda i: _envelope(i))
        self.assertTrue(result["finished"])
        with self.engine.connect() as conn:
            current = conn.execute(select(models.report_table).where(
                models.report_table.c.ID == rid)).mappings().first()
        self.assertEqual(seen.get("상태"), current["상태"])
        self.assertEqual(seen.get("답변일"), current["답변일"])
        self.assertIn("처리내용", seen)
