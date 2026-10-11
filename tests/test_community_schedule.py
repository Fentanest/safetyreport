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
        sched.register_community_jobs(scheduler, data_dir=self.tmp)
        sched.register_community_jobs(scheduler, data_dir=self.tmp)
        ids = sorted(job.id for job in scheduler.get_jobs())
        self.assertEqual(ids, [sched.GATE_POLL_JOB_ID, sched.MIDNIGHT_JOB_ID])
        midnight = scheduler.get_job(sched.MIDNIGHT_JOB_ID)
        self.assertIsNotNone(midnight)
        self.assertIn("hour='0'", str(midnight.trigger))
        # 2026-10-11 cloud-retry: 00:00 정각이 아니라 설치별 영속 오프셋(분·초)으로 기동한다(동시 기동 분산).
        offset = sched.midnight_offset_seconds(self.tmp)
        minute, second = divmod(offset, 60)
        self.assertIn(f"minute='{minute}'", str(midnight.trigger))
        self.assertIn(f"second='{second}'", str(midnight.trigger))

    def test_midnight_offset_is_persisted_per_install_and_bounded(self):
        # 2026-10-11 cloud-retry: 모든 PC 가 정확히 00:00:00 에 기동해 Supabase 를 몰아치지 않도록, 설치(기기)별
        # 영속 오프셋 0~1800초를 첫 호출 때 정해 저장하고 이후 고정한다(기존 코드는 이 함수가 없어 항상 실패한다).
        first = sched.midnight_offset_seconds(self.tmp)
        self.assertGreaterEqual(first, 0)
        self.assertLessEqual(first, sched.MIDNIGHT_OFFSET_MAX_SECONDS)
        for _ in range(3):
            self.assertEqual(sched.midnight_offset_seconds(self.tmp), first, "같은 설치는 재호출해도 같은 오프셋")
        other_dir = tempfile.mkdtemp()
        try:
            other_store = CommunityStore.open(other_dir)
            other = sched.midnight_offset_seconds(other_dir)
            self.assertGreaterEqual(other, 0)
            self.assertLessEqual(other, sched.MIDNIGHT_OFFSET_MAX_SECONDS)
        finally:
            other_store.close()
            CommunityStore._forget(os.path.join(other_dir, "community.db"))

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
            sched.catch_up_on_start(data_dir=self.tmp, wait=True)  # 서버 시작은 막지 않고 백그라운드에서 돈다
        row = self.store.connect().execute("SELECT COUNT(*) v FROM schedule_runs").fetchone()
        self.assertEqual(row["v"], 1)


if __name__ == "__main__":
    unittest.main()


class NextMidnightLabelTest(unittest.TestCase):
    """화면의 '다음 자정 업로드' 는 실제 실행 시각(자정 + 설치별 오프셋)과 같아야 한다(Astra 2차 #7)."""

    def test_before_todays_offset_shows_today(self):
        from datetime import datetime, timezone
        from services.community_upload_status import _next_midnight_label
        now = datetime(2026, 10, 11, 15, 5, tzinfo=timezone.utc)  # KST 2026-10-12 00:05
        self.assertEqual(_next_midnight_label(now=now, offset_seconds=900), "2026-10-12 00:15")

    def test_after_todays_offset_shows_tomorrow(self):
        from datetime import datetime, timezone
        from services.community_upload_status import _next_midnight_label
        now = datetime(2026, 10, 11, 15, 20, tzinfo=timezone.utc)  # KST 2026-10-12 00:20
        self.assertEqual(_next_midnight_label(now=now, offset_seconds=900), "2026-10-13 00:15")

    def test_daytime_shows_next_midnight_plus_offset(self):
        from datetime import datetime, timezone
        from services.community_upload_status import _next_midnight_label
        now = datetime(2026, 10, 12, 3, 0, tzinfo=timezone.utc)  # KST 2026-10-12 12:00
        self.assertEqual(_next_midnight_label(now=now, offset_seconds=0), "2026-10-13 00:00")
