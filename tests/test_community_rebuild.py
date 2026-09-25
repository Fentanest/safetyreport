"""PC 1회 초기화 크롤링 job (T3b, services/community_rebuild.py + start.py --rebuild).

실제 안전신문고·Supabase 호출 없음. 크롤러·게이트·업로더·런처는 가짜로 주입한다.
settings.datapath·username 은 테스트별 임시값으로 패치한다(운영 data/ 금지).
"""
import os
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest import mock

import pandas as pd
from sqlalchemy import create_engine

import settings.settings as app_settings
from core.database import database
from services import community_rebuild as rebuild
from services.community_store import CommunityStore


def _title_df(rows):
    return pd.DataFrame(rows, columns=["ID", "상태", "신고번호", "신고명", "신고일",
                                       "만족도조사여부", "감시목록"])


class RebuildEnv:
    """게이트 통과·manifest 통과·런처 기록. 개별 테스트에서 can_enter 등을 바꾼다."""

    def __init__(self, testcase, *, login="Tester@Example.com"):
        self.testcase = testcase
        self.tmp = tempfile.mkdtemp(prefix="rebuild-")
        self.launches = []
        self.gate_state = {"state": "linked", "can_enter": True, "reasons": []}
        self.manifest_ok = True
        self.login = login
        self._patches = []
        self._saved_modules = {}

    def install(self):
        t = self.testcase
        from core.utils import logger
        logger.LoggerFactory.create_logger(mode="crawl")
        p1 = mock.patch.object(app_settings, "datapath", self.tmp)
        p2 = mock.patch.object(app_settings, "username", self.login)
        p1.start(); p2.start()
        self._patches += [p1, p2]

        gate = types.ModuleType("services.community_gate")
        env = self

        def require_fresh(max_age=60.0):
            return dict(env.gate_state)

        gate.require_fresh = require_fresh
        self._inject("services.community_gate", gate)

        uploader = types.ModuleType("services.community_uploader")
        uploader.refresh_server_completed = lambda: env.manifest_ok
        self._inject("services.community_uploader", uploader)

        import services.crawl_control as cc
        p3 = mock.patch.object(cc, "start_rebuild",
                               side_effect=lambda run_id: env.launches.append(run_id))
        p3.start()
        self._patches.append(p3)
        self.start_rebuild_patcher = p3

        # 개인 DB 엔진(목록 반영용).
        fd, self.personal_db = tempfile.mkstemp(suffix=".db", prefix="personal-")
        os.close(fd)
        self.engine = create_engine(f"sqlite:///{self.personal_db}")
        database.upgrade_schema(self.engine)
        t.addCleanup(self.uninstall)
        return self

    def _inject(self, name, module):
        if name in sys.modules:
            self._saved_modules[name] = sys.modules[name]
        sys.modules[name] = module

    def uninstall(self):
        for p in reversed(self._patches):
            p.stop()
        for name in ("services.community_gate", "services.community_uploader"):
            sys.modules.pop(name, None)
            if name in self._saved_modules:
                sys.modules[name] = self._saved_modules[name]
        CommunityStore._forget(os.path.join(self.tmp, "community.db"))
        try:
            self.engine.dispose()
        except Exception:
            pass
        for path in (self.personal_db,):
            try:
                os.remove(path)
            except OSError:
                pass

    # -- 가짜 크롤러 ---------------------------------------------------------
    def fake_titles(self, dfs, *, list_ok=True, note=None):
        def _crawl(driver=None, browser_fallback=False, progress=None, **kwargs):
            if progress is not None:
                progress.update({"total": sum(len(d) for d in dfs),
                                 "pages_expected": [1], "pages_ok": [1] if list_ok else [],
                                 "pages_failed": [] if list_ok else [1],
                                 "first_error": None if list_ok else (note or "page 1 failed"),
                                 "list_ok": list_ok})
            return dfs, 1
        return mock.patch("core.crawler.crawltitle_api.crawl_titles", _crawl)

    def fake_details(self, outcomes):
        """outcomes: {report_id: (outcome, note)}. ok 는 yield 도 한다."""
        from core.storage import reports_repo

        def _crawl(driver=None, report_ids=None, browser_fallback=False,
                   status_sink=None, **kwargs):
            for rid in report_ids or []:
                outcome, note = outcomes.get(rid, ("ok", ""))
                if status_sink is not None:
                    status_sink[rid] = (outcome, note)
                if outcome == "ok":
                    df = pd.DataFrame([{"ID": rid}])
                    yield (df, "traffic", None, None, None, None, "")

        def _from_tuple(item):
            df = item[0]
            rid = str(df.iloc[0]["ID"])
            rec = mock.Mock()
            rec.id = rid
            return rec

        def _save(engine, records, refresh_duplicates=False):
            result = reports_repo.SaveResult()
            for rec in records:
                result.saved += 1
                result.changed.append({"id": rec.id, "change_type": "신규"})
            return result

        return (mock.patch("core.crawler.crawldetail_api.crawl_details", _crawl),
                mock.patch.object(reports_repo.CrawledDetail, "from_legacy_tuple",
                                  staticmethod(_from_tuple)),
                mock.patch.object(reports_repo, "save_crawled", _save))


def _setUp_env(t, **kwargs):
    env = RebuildEnv(t, **kwargs).install()
    return env


class NamespaceTest(unittest.TestCase):
    def test_namespace_normalizes_login_id(self):
        self.assertEqual(rebuild.source_account_namespace("  USER@X.com "),
                         rebuild.source_account_namespace("user@x.com"))
        self.assertIsNotNone(rebuild.source_account_namespace("a"))
        self.assertNotEqual(rebuild.source_account_namespace("a"),
                            rebuild.source_account_namespace("b"))

    def test_namespace_none_without_login(self):
        self.assertIsNone(rebuild.source_account_namespace(None))
        self.assertIsNone(rebuild.source_account_namespace("   "))


class GateAndCommandTest(unittest.TestCase):
    def setUp(self):
        self.env = _setUp_env(self)

    def test_g01_no_crawl_before_confirm(self):
        self.assertTrue(rebuild.required())
        payload = rebuild.status()
        self.assertEqual(payload["state"], "required")
        self.assertEqual(self.env.launches, [])
        # 확인 전 일반 크롤도 막힌다.
        import services.crawl_control as cc
        with self.assertRaisesRegex(RuntimeError, "COMMUNITY_REBUILD_REQUIRED"):
            cc.start_crawl(crawl_mode="full", header="x", broadcast_source="t")
        with self.assertRaisesRegex(RuntimeError, "COMMUNITY_REBUILD_REQUIRED"):
            cc.enqueue_report("SPP-1")

    def test_g02_rebuild_command_has_force_and_rebuild_without_reset(self):
        import services.crawl_control as cc
        cmd = cc._build_rebuild_command("run123")
        self.assertIn("--force", cmd)
        self.assertIn("--rebuild", cmd)
        self.assertIn("run123", cmd)
        self.assertNotIn("--reset", cmd)

    def test_g02_frozen_command_shape(self):
        import services.crawl_control as cc
        with mock.patch.object(sys, "frozen", True, create=True):
            cmd = cc._build_rebuild_command("run123")
        self.assertEqual(cmd[1:3], ["--mode", "crawl"])
        self.assertEqual(cmd[-2:], ["--rebuild", "run123"])
        self.assertNotIn("--reset", cmd)

    def test_start_rebuild_launches_single_command(self):
        import services.crawl_control as cc
        from services.crawl_manager import crawl_manager
        # RebuildEnv 가 start_rebuild 를 기록용 가짜로 바꿔 두므로 원본으로 되돌린다.
        self.env.start_rebuild_patcher.stop()
        self.addCleanup(self.env.start_rebuild_patcher.start)
        self.assertFalse(crawl_manager.is_crawling())
        with mock.patch.object(crawl_manager, "is_crawling", return_value=False), \
                mock.patch.object(crawl_manager, "start_crawl", return_value=True) as started, \
                mock.patch("services.crawl_control._write_log_header", return_value="/tmp/x.log"), \
                mock.patch("services.crawl_control._start_after_crawl_hook"):
            with mock.patch.object(cc, "_check_crawl_allowed", return_value=None):
                cc.start_rebuild("run123")
        args = started.call_args[0][0]
        self.assertIn("--force", args)
        self.assertIn("run123", args)
        self.assertNotIn("--reset", args)


class FullFlowTest(unittest.TestCase):
    def setUp(self):
        self.env = _setUp_env(self)
        self.dfs = [_title_df([
            ("101", "진행", "SPP-101", "t1", "2026-09-01", "", "N"),
            ("102", "답변완료", "SPP-102", "t2", "2026-09-01", "", "N"),  # G03: 종결 Y 도 포함
            ("103", "취하", "SPP-103", "t3", "2026-09-01", "", "N"),
        ])]

    def _run_crawl(self, run_id, outcomes=None):
        import start
        outcomes = outcomes if outcomes is not None else {}
        p1, p2, p3 = self.env.fake_details(outcomes)
        with self.env.fake_titles(self.dfs, list_ok=True), p1, p2, p3:
            return start._run_rebuild_process(None, self.env.engine,
                                              {"rebuild": run_id}, False)

    def test_start_lists_and_completes(self):
        payload = rebuild.start("tester")
        self.assertEqual(payload["state"], "running")
        run_id = payload["run_id"]
        self.assertEqual(self.env.launches, [run_id])

        changed = self._run_crawl(run_id)
        self.assertEqual(len(changed), 3)
        store = CommunityStore.open()
        items = {r["source_report_id"]: r["state"] for r in store.connect().execute(
            "SELECT source_report_id, state FROM rebuild_items WHERE run_id=?", (run_id,))}
        self.assertEqual(items, {"101": "fetched", "102": "fetched", "103": "fetched"})

        done = rebuild.on_crawl_finished(run_id)
        self.assertEqual(done["state"], "completed")
        self.assertFalse(rebuild.required())

    def test_g05_no_rerun_after_completed(self):
        first = rebuild.start("tester")
        self._run_crawl(first["run_id"])
        rebuild.on_crawl_finished(first["run_id"])
        before = list(self.env.launches)
        again = rebuild.start("tester")
        self.assertEqual(again["state"], "completed")
        self.assertEqual(again["run_id"], first["run_id"])
        self.assertEqual(self.env.launches, before)

    def test_g06_concurrent_start_returns_single_run(self):
        first = rebuild.start("a")
        second = rebuild.start("b")
        self.assertEqual(first["run_id"], second["run_id"])
        self.assertEqual(self.env.launches, [first["run_id"]])

    def test_g12_partial_list_is_failure_not_success(self):
        payload = rebuild.start("tester")
        run_id = payload["run_id"]
        import start
        with self.env.fake_titles(self.dfs, list_ok=False, note="page 2 timeout"):
            changed = start._run_rebuild_process(None, self.env.engine,
                                                 {"rebuild": run_id}, False)
        self.assertEqual(changed, [])
        job = rebuild._get_job(run_id)
        self.assertEqual(job["state"], "failed")
        self.assertIn("page 2 timeout", job["last_error"])
        self.assertEqual(job["list_complete"], 0)

    def test_g12_login_failure_marks_failed(self):
        payload = rebuild.start("tester")
        rebuild.mark_login_failed(payload["run_id"], "login_failed: 401")
        job = rebuild._get_job(payload["run_id"])
        self.assertEqual(job["state"], "failed")
        self.assertTrue(rebuild.required())

    def test_g13_empty_list_completes_with_zero_items(self):
        payload = rebuild.start("tester")
        run_id = payload["run_id"]
        import start
        with self.env.fake_titles([], list_ok=True):
            changed = start._run_rebuild_process(None, self.env.engine,
                                                 {"rebuild": run_id}, False)
        self.assertEqual(changed, [])
        job = rebuild._get_job(run_id)
        self.assertEqual(job["list_complete"], 1)
        done = rebuild.on_crawl_finished(run_id)
        self.assertEqual(done["state"], "completed")

    def test_g14_rotate_requires_rebuild_again(self):
        payload = rebuild.start("tester")
        self._run_crawl(payload["run_id"])
        rebuild.on_crawl_finished(payload["run_id"])
        self.assertFalse(rebuild.required())
        CommunityStore.open().rotate_dataset("restore_server")
        self.assertTrue(rebuild.required())

    def test_detail_failure_retry_then_permanent_and_gaps(self):
        payload = rebuild.start("tester")
        run_id = payload["run_id"]
        # 103 영구 실패(404), 나머지는 성공.
        self._run_crawl(run_id, {"103": ("permanent", "HTTP 404")})
        store = CommunityStore.open()
        state = store.connect().execute(
            "SELECT state, last_list_label FROM rebuild_items WHERE run_id=? AND source_report_id='103'",
            (run_id,)).fetchone()
        self.assertEqual(state["state"], "failed_permanent")
        self.assertEqual(state["last_list_label"], "취하")  # 당시 목록 라벨 보존
        done = rebuild.on_crawl_finished(run_id)
        self.assertEqual(done["state"], "validating")
        accepted = rebuild.accept_gaps()
        self.assertEqual(accepted["state"], "completed_with_gaps")

    def test_cutover_merges_without_deleting(self):
        payload = rebuild.start("tester")
        run_id = payload["run_id"]
        self._run_crawl(run_id)
        store = CommunityStore.open()
        dataset_id = store.local_dataset_id()
        with store.transaction() as tx:
            # 기존 completed 포인터: 하나는 이번 run 에 갱신될 것, 하나는 손대지 않을 것.
            tx.execute("INSERT INTO report_latest(local_dataset_id, source_report_id, event_id,"
                       " payload_sha256, eligible, source_generation) VALUES (?, '101', 'old-e', 'old-h', 1, 7)",
                       (dataset_id,))
            tx.execute("INSERT INTO report_latest(local_dataset_id, source_report_id, event_id,"
                       " payload_sha256, eligible, source_generation) VALUES (?, '999', 'keep-e', 'keep-h', 1, 7)",
                       (dataset_id,))
            for rid in ("101", "102", "103"):
                tx.execute("INSERT INTO report_latest_staging(run_id, source_report_id, event_id,"
                           " payload_sha256, eligible) VALUES (?, ?, ?, ?, 1)",
                           (run_id, rid, f"new-{rid}", f"h-{rid}"))
        done = rebuild.on_crawl_finished(run_id)
        self.assertEqual(done["state"], "completed")
        rows = {r["source_report_id"]: dict(r) for r in store.connect().execute(
            "SELECT * FROM report_latest WHERE local_dataset_id=?", (dataset_id,))}
        self.assertEqual(rows["101"]["event_id"], "new-101")
        self.assertEqual(rows["101"]["source_generation"], 8)
        self.assertEqual(rows["999"]["event_id"], "keep-e")  # 무변경 carry-forward
        self.assertEqual(rows["999"]["source_generation"], 7)
        # 영구 실패 pointer 성격: items 행 보존.
        self.assertEqual(len(store.connect().execute(
            "SELECT 1 FROM rebuild_items WHERE run_id=?", (run_id,)).fetchall()), 3)

    def test_record_item_retry_is_bounded(self):
        payload = rebuild.start("tester")
        run_id = payload["run_id"]
        rebuild.register_list(run_id, ["201"])
        for _ in range(4):
            rebuild.record_item(run_id, "201", "retryable", note="HTTP 500")
        state = CommunityStore.open().connect().execute(
            "SELECT state FROM rebuild_items WHERE run_id=? AND source_report_id='201'",
            (run_id,)).fetchone()["state"]
        self.assertEqual(state, "failed_retryable")
        rebuild.record_item(run_id, "201", "retryable", note="HTTP 500")
        state = CommunityStore.open().connect().execute(
            "SELECT state FROM rebuild_items WHERE run_id=? AND source_report_id='201'",
            (run_id,)).fetchone()["state"]
        self.assertEqual(state, "failed_permanent")


class CrashResumeTest(unittest.TestCase):
    def setUp(self):
        self.env = _setUp_env(self)

    def _expire_lease(self):
        store = CommunityStore.open()
        with store.transaction() as tx:
            tx.execute("UPDATE leases SET until='2000-01-01T00:00:00.000Z' WHERE name='rebuild'")

    def test_g11_running_with_expired_lease_resumes_same_run(self):
        payload = rebuild.start("tester")
        run_id = payload["run_id"]
        self._expire_lease()
        # 재시작 흉내: 핸들 캐시 비우기.
        CommunityStore._forget(os.path.join(self.env.tmp, "community.db"))
        resumed = rebuild.resume_on_startup()
        self.assertEqual(resumed["resumed"], run_id)
        self.assertEqual(self.env.launches, [run_id, run_id])

    def test_g11_valid_run_with_live_lease_does_not_resume(self):
        rebuild.start("tester")
        resumed = rebuild.resume_on_startup()
        self.assertIsNone(resumed["resumed"])
        self.assertEqual(len(self.env.launches), 1)

    def test_g11_failed_resumes_same_run(self):
        payload = rebuild.start("tester")
        run_id = payload["run_id"]
        rebuild.mark_list_failed(run_id, "list_incomplete")
        out = rebuild.resume()
        self.assertEqual(out["run_id"], run_id)
        self.assertEqual(out["state"], "running")
        self.assertEqual(self.env.launches, [run_id, run_id])

    def test_g11_crash_in_preparing_backup_continues_same_run(self):
        payload = rebuild.start("tester")
        first_id = payload["run_id"]
        # preparing_backup 상태에서 멈춘 것처럼 되돌린 뒤 start 재호출.
        rebuild._set_state(first_id, "preparing_backup")
        again = rebuild.start("tester")
        self.assertEqual(again["run_id"], first_id)

    def test_pause_and_auth_paths(self):
        payload = rebuild.start("tester")
        run_id = payload["run_id"]
        paused = rebuild.pause("user")
        self.assertEqual(paused["state"], "paused")
        # on_crawl_finished 는 사용자 paused 를 덮지 않는다.
        kept = rebuild.on_crawl_finished(run_id)
        self.assertEqual(kept["state"], "paused")
        out = rebuild.resume()
        self.assertEqual(out["state"], "running")


class BackupFailureTest(unittest.TestCase):
    def setUp(self):
        self.env = _setUp_env(self)

    def test_g07_backup_failure_leaves_personal_db_untouched(self):
        # 개인 DB 파일 실재 + 내용 고정. 백업 단계 실패를 주입한다.
        with open(app_settings.db_path, "wb") as fh:
            fh.write(b"personal-db-bytes")
        with open(app_settings.db_path, "rb") as fh:
            before = fh.read()
        try:
            with mock.patch.object(rebuild, "_backup_personal_db",
                                   side_effect=OSError("no space left on device")):
                payload = rebuild.start("tester")
        finally:
            pass
        self.assertEqual(payload["state"], "failed")
        self.assertIn("backup", payload["last_error"])
        with open(app_settings.db_path, "rb") as fh:
            self.assertEqual(fh.read(), before)
        self.assertEqual(self.env.launches, [])
        os.remove(app_settings.db_path)

    def test_manifest_unavailable_holds_without_crawl(self):
        self.env.manifest_ok = False
        payload = rebuild.start("tester")
        self.assertEqual(payload["state"], "prerequisites_required")
        self.assertEqual(self.env.launches, [])

    def test_gate_denied_holds_without_crawl(self):
        self.env.gate_state = {"state": "need_consent", "can_enter": False,
                               "reasons": ["consent"]}
        payload = rebuild.start("tester")
        self.assertEqual(payload["state"], "prerequisites_required")
        self.assertEqual(self.env.launches, [])

    def test_missing_login_id_is_prerequisites(self):
        with mock.patch.object(app_settings, "username", None):
            payload = rebuild.start("tester")
        self.assertEqual(payload["state"], "prerequisites_required")
        self.assertEqual(self.env.launches, [])


class RouterTest(unittest.TestCase):
    def setUp(self):
        self.env = _setUp_env(self)
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from starlette.middleware.sessions import SessionMiddleware
        from web.routers import community_rebuild_route as route
        from web.routers.api_route import _require_api_key

        app = FastAPI()
        app.add_middleware(SessionMiddleware, secret_key="test-secret")
        app.include_router(route.router)
        app.include_router(route.api_router)
        app.dependency_overrides[_require_api_key] = lambda: "test-key"
        self.client = TestClient(app, raise_server_exceptions=False)
        self._csrf = mock.patch.object(route.csrf, "verify_json_post", return_value=None)
        self._csrf.start()
        self._manage = mock.patch.object(route, "_can_manage", return_value=True)
        self._manage.start()
        self.addCleanup(self._csrf.stop)
        self.addCleanup(self._manage.stop)

    def test_web_status_and_actions_no_store(self):
        resp = self.client.get("/settings/community/rebuild")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get("Cache-Control"), "no-store")
        self.assertEqual(resp.json()["data"]["state"], "required")

        started = self.client.post("/settings/community/rebuild/start", json={})
        self.assertEqual(started.status_code, 200)
        run_id = started.json()["data"]["run_id"]
        self.assertTrue(run_id)

        paused = self.client.post("/settings/community/rebuild/pause", json={"reason": "t"})
        self.assertEqual(paused.json()["data"]["state"], "paused")

        resumed = self.client.post("/settings/community/rebuild/resume", json={})
        self.assertEqual(resumed.json()["data"]["state"], "running")

        bad = self.client.post("/settings/community/rebuild/start", json={"unknown": 1})
        self.assertEqual(bad.status_code, 400)

    def test_web_start_gate_failure_is_409(self):
        self.env.gate_state = {"state": "need_consent", "can_enter": False, "reasons": []}
        resp = self.client.post("/settings/community/rebuild/start", json={})
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["code"], "prerequisites_required")

    def test_api_status_and_start(self):
        resp = self.client.get("/api/v1/community/rebuild")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get("Cache-Control"), "no-store")
        started = self.client.post("/api/v1/community/rebuild/start", json={})
        self.assertEqual(started.status_code, 200)
        self.assertTrue(started.json()["data"]["run_id"])

    def test_no_token_in_responses(self):
        resp = self.client.get("/settings/community/rebuild")
        self.assertNotIn("token", resp.text.lower())


if __name__ == "__main__":
    unittest.main()
