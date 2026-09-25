"""스케줄러 분리 + 복원 전 rotate (T3b).

- update_jobs(enabled true/false) 뒤 커뮤니티 job 존속(S-08), 크롤 job 만 교체.
- exchange.restore() 는 _swap_in 직전에 rotate_dataset(f"restore_{kind}") 을 호출하고,
  실패 경로에서도 되돌리지 않는다.
"""
import configparser
import os
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import settings.settings as app_settings
from core.database import models
from core.database.engine import get_engine
from core.storage import exchange
from core.utils import logger, scheduler
from scripts.dev import fixture_server

MIDNIGHT_JOB_ID = "community-midnight-upload"
GATE_POLL_JOB_ID = "community-gate-poll"


def _install_schedule_fake(testcase):
    module = types.ModuleType("services.community_schedule")

    def register_community_jobs(sched):
        def _noop():
            pass
        for job_id in (MIDNIGHT_JOB_ID, GATE_POLL_JOB_ID):
            try:
                sched.add_job(_noop, "interval", hours=24, id=job_id, replace_existing=True)
            except Exception:
                pass

    module.register_community_jobs = register_community_jobs
    saved = sys.modules.get("services.community_schedule")
    sys.modules["services.community_schedule"] = module
    testcase.addCleanup(lambda: _restore_schedule_module(saved))


def _restore_schedule_module(saved):
    if saved is None:
        sys.modules.pop("services.community_schedule", None)
    else:
        sys.modules["services.community_schedule"] = saved


def _write_scheduler_config(*, enabled, mode="interval"):
    config = configparser.ConfigParser()
    if os.path.exists(app_settings.config_path):
        config.read(app_settings.config_path)
    if not config.has_section("SCHEDULER"):
        config.add_section("SCHEDULER")
    config.set("SCHEDULER", "enabled", "true" if enabled else "false")
    config.set("SCHEDULER", "mode", mode)
    config.set("SCHEDULER", "interval_hours", "24")
    config.set("SCHEDULER", "interval_start", "00:00")
    os.makedirs(os.path.dirname(app_settings.config_path), exist_ok=True)
    with open(app_settings.config_path, "w") as fh:
        config.write(fh)


class SchedulerSplitTest(unittest.TestCase):
    def setUp(self):
        logger.LoggerFactory.create_logger(mode="crawl")
        _install_schedule_fake(self)
        scheduler.scheduler.remove_all_jobs()
        self._had_config = os.path.exists(app_settings.config_path)
        self.addCleanup(scheduler.scheduler.remove_all_jobs)
        if not self._had_config:
            self.addCleanup(lambda: os.path.exists(app_settings.config_path)
                            and os.remove(app_settings.config_path))

    def _job_ids(self):
        return {job.id for job in scheduler.scheduler.get_jobs()}

    def test_enabled_keeps_community_jobs_and_replaces_crawl_only(self):
        _write_scheduler_config(enabled=True)
        scheduler.update_jobs()
        first = self._job_ids()
        self.assertIn("crawl_job_interval", first)
        self.assertIn(MIDNIGHT_JOB_ID, first)
        self.assertIn(GATE_POLL_JOB_ID, first)

        # 외부 job + 크롤 job 을 가장한 추가분.
        scheduler.scheduler.add_job(lambda: None, "interval", hours=1, id="other-owner")
        scheduler.update_jobs()
        second = self._job_ids()
        self.assertIn("other-owner", second)  # 남의 job 은 손대지 않음
        self.assertIn(MIDNIGHT_JOB_ID, second)  # S-08: 커뮤니티 job 존속
        self.assertIn(GATE_POLL_JOB_ID, second)
        self.assertEqual([j for j in second if j == "crawl_job_interval"], ["crawl_job_interval"])

    def test_disabled_keeps_community_jobs(self):
        _write_scheduler_config(enabled=True)
        scheduler.update_jobs()
        self.assertIn(MIDNIGHT_JOB_ID, self._job_ids())
        _write_scheduler_config(enabled=False)
        scheduler.update_jobs()
        ids = self._job_ids()
        self.assertNotIn("crawl_job_interval", ids)
        self.assertIn(MIDNIGHT_JOB_ID, ids)  # disabled 여도 유지
        self.assertIn(GATE_POLL_JOB_ID, ids)

    def test_cron_crawl_jobs_are_replaced_not_community(self):
        _write_scheduler_config(enabled=True, mode="cron")
        config = configparser.ConfigParser()
        config.read(app_settings.config_path)
        config.set("SCHEDULER", "cron_times", "09:00, 18:30")
        with open(app_settings.config_path, "w") as fh:
            config.write(fh)
        scheduler.update_jobs()
        ids = self._job_ids()
        self.assertIn("cron_09_00", ids)
        self.assertIn("cron_18_30", ids)
        self.assertIn(MIDNIGHT_JOB_ID, ids)
        self.assertTrue(scheduler.is_crawl_job_id("cron_09_00"))
        self.assertFalse(scheduler.is_crawl_job_id(MIDNIGHT_JOB_ID))

    def test_run_crawler_skips_on_gate_or_rebuild(self):
        import services.crawl_control as cc
        gate = types.ModuleType("services.community_gate")
        gate.require_fresh = lambda max_age=60.0: {"state": "x", "can_enter": False, "reasons": []}
        saved = sys.modules.get("services.community_gate")
        sys.modules["services.community_gate"] = gate

        def _restore_gate():
            sys.modules.pop("services.community_gate", None)
            if saved is not None:
                sys.modules["services.community_gate"] = saved

        self.addCleanup(_restore_gate)
        with mock.patch.object(cc, "start_crawl") as started:
            scheduler.run_crawler()
        started.assert_not_called()


def _mobile_db(path: Path):
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE reports (ID TEXT PRIMARY KEY, 상태 TEXT, 신고번호 TEXT, 신고명 TEXT, 신고일 TEXT, 만족도조사여부 TEXT, 별점 INTEGER,
          별점사유 TEXT, 감시목록 TEXT, 처리상태 TEXT, 처리기관 TEXT, 담당자 TEXT, 위반장소 TEXT, 종결여부 TEXT, category TEXT, entry_value TEXT,
          raw_content TEXT, synced_at INTEGER);
        CREATE TABLE sync_meta (key TEXT PRIMARY KEY, value TEXT);
        """
    )
    con.execute("INSERT INTO reports VALUES ('m1','수용','SPP-2609-9000011','신호위반','2026-09-01','참여 완료',NULL,NULL,'N','수용','기관','','서울 강서구 1','Y','traffic','자동차·교통위반-신호위반','',NULL)")
    con.execute("INSERT INTO sync_meta VALUES ('last_sync','2026-09-24T10:00:00')")
    con.commit()
    con.close()


class ExchangeRotateTest(unittest.TestCase):
    def setUp(self):
        logger.LoggerFactory.create_logger(mode="crawl")
        get_engine().dispose()
        for ext in ("", "-wal", "-shm"):
            if os.path.exists(app_settings.db_path + ext):
                os.remove(app_settings.db_path + ext)
        fixture_server.seed_engine(get_engine())
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(get_engine().dispose)
        self.upload = Path(self._tmp.name) / "mobile.db"
        _mobile_db(self.upload)

    def _dataset_id(self):
        from services.community_store import CommunityStore
        store = CommunityStore.open()
        try:
            return store.local_dataset_id()
        finally:
            store.close()

    def test_restore_rotates_before_swap(self):
        from services.community_store import CommunityStore
        before = self._dataset_id()
        real_rotate = CommunityStore.rotate_dataset
        with mock.patch.object(CommunityStore, "rotate_dataset", autospec=True,
                               side_effect=lambda self, reason: real_rotate(self, reason)) as rotated:
            exchange.restore(str(self.upload), "mobile")
        rotated.assert_called_once_with(mock.ANY, "restore_mobile")
        self.assertNotEqual(self._dataset_id(), before)

    def test_swap_failure_does_not_roll_back_rotate(self):
        before = self._dataset_id()
        with mock.patch("core.storage.exchange._swap_in",
                        side_effect=RuntimeError("disk gone")):
            with self.assertRaises(RuntimeError):
                exchange.restore(str(self.upload), "mobile")
        # 선회전은 그대로(되돌리지 않음).
        self.assertNotEqual(self._dataset_id(), before)


if __name__ == "__main__":
    unittest.main()
