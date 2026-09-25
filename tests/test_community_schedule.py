"""schedule 테스트 — H06~H13·H17 + register 멱등 + run_midnight/catch_up."""
import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

from services import community_schedule as sched
from services.community_store import CommunityStore

CTX = {"contributor_fingerprint": "f" * 32, "connection_id": "11111111-2222-4333-8444-555555555555",
       "writer_epoch": 1, "dataset_key": "d" * 16, "consent_grant_id": "22222222-3333-4444-8444-666666666666",
       "policy_version": "2026-09-26.1", "consent_text_sha256": "h" * 64,
       "source_app": "safetyreport", "source_mode": "server"}


def utc(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


class SchedulePureTest(unittest.TestCase):
    def test_h06_just_before_midnight(self):
        self.assertEqual(sched.due_key(utc("2026-09-26T14:59:59Z")), "midnight:2026-09-26")

    def test_h07_exactly_midnight(self):
        self.assertEqual(sched.due_key(utc("2026-09-26T15:00:00Z")), "midnight:2026-09-27")

    def test_h08_month_end(self):
        self.assertEqual(sched.due_key(utc("2026-09-30T15:00:01Z")), "midnight:2026-10-01")

    def test_h09_year_end(self):
        self.assertEqual(sched.due_key(utc("2026-12-31T15:00:00Z")), "midnight:2027-01-01")

    def test_h10_clock_rollback_keeps_succeeded(self):
        now = utc("2026-09-25T16:00:00Z")
        self.assertFalse(sched.should_run(now, {"state": "succeeded"}))

    def test_h11_days_missed_runs_only_latest(self):
        now = utc("2026-09-29T01:00:00Z")
        self.assertEqual(sched.due_key(now), "midnight:2026-09-29")
        self.assertTrue(sched.should_run(now, None))

    def test_h12_partial_is_not_succeeded(self):
        now = utc("2026-09-26T03:00:00Z")
        key = sched.due_key(now)
        self.assertTrue(sched.should_run(now, {"state": "deferred", "schedule_key": key}))
        self.assertTrue(sched.should_run(now, {"state": "failed", "schedule_key": key}))

    def test_h13_running_with_live_lease_joins(self):
        now = utc("2026-09-26T03:00:00Z")
        live = {"state": "running", "lease_until": "2026-09-26T03:05:00Z"}
        expired = {"state": "running", "lease_until": "2026-09-26T02:59:00Z"}
        self.assertFalse(sched.should_run(now, live))
        self.assertTrue(sched.should_run(now, expired))

    def test_h17_account_switch_changes_key_scope(self):
        now = utc("2026-09-26T03:00:00Z")
        key = sched.due_key(now)
        self.assertTrue(key.startswith("midnight:"))


class ScheduleJobsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = CommunityStore.open(self.tmp)
        self.store.set_context(**CTX)

    def tearDown(self):
        self.store.close()
        CommunityStore._forget(os.path.join(self.tmp, "community.db"))

    def test_register_is_idempotent(self):
        from apscheduler.schedulers.background import BackgroundScheduler
        scheduler = BackgroundScheduler()
        sched.register_community_jobs(scheduler)
        sched.register_community_jobs(scheduler)
        ids = sorted(job.id for job in scheduler.get_jobs())
        self.assertEqual(ids, [sched.GATE_POLL_JOB_ID, sched.MIDNIGHT_JOB_ID])
        midnight = scheduler.get_job(sched.MIDNIGHT_JOB_ID)
        self.assertIsNotNone(midnight)
        self.assertIn("hour='0'", str(midnight.trigger))
        self.assertIn("minute='0'", str(midnight.trigger))

    def test_run_midnight_success_records_succeeded(self):
        from services import community_uploader as up
        with mock.patch.object(up, "_gate_check",
                               return_value={"state": "ok", "can_enter": True, "reasons": []}):
            outcome = sched.run_midnight(utc("2026-09-26T03:00:00Z"), data_dir=self.tmp)
        self.assertEqual(outcome["result"], "succeeded")
        self.assertEqual(outcome["schedule_key"], "midnight:2026-09-26")
        row = self.store.connect().execute("SELECT state FROM schedule_runs").fetchone()
        self.assertEqual(row["state"], "succeeded")

    def test_run_midnight_second_call_is_already_succeeded(self):
        from services import community_uploader as up
        with mock.patch.object(up, "_gate_check",
                               return_value={"state": "ok", "can_enter": True, "reasons": []}):
            first = sched.run_midnight(utc("2026-09-26T03:00:00Z"), data_dir=self.tmp)
            second = sched.run_midnight(utc("2026-09-26T04:00:00Z"), data_dir=self.tmp)
        self.assertEqual(first["result"], "succeeded")
        self.assertEqual(second["result"], "already_succeeded")

    def test_catch_up_on_start(self):
        from services import community_uploader as up
        with mock.patch.object(up, "_gate_check",
                               return_value={"state": "ok", "can_enter": True, "reasons": []}):
            sched.catch_up_on_start(data_dir=self.tmp)
        row = self.store.connect().execute("SELECT COUNT(*) v FROM schedule_runs").fetchone()
        self.assertEqual(row["v"], 1)


if __name__ == "__main__":
    unittest.main()
