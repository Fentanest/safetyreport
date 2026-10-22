"""진행 보고(4-1): 본문 검사(계약 벡터), 60초 억제, trigger 규칙, 기록, 조용한 실패, 503 maintenance."""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from services import community_progress_report as pr
from services.community_store import CommunityStore

ROOT = Path(__file__).resolve().parents[1]
CONN = "20000000-0000-4000-8000-000000000001"
CTX = {"contributor_fingerprint": "f" * 32, "connection_id": CONN, "writer_epoch": 1, "dataset_key": "d" * 64,
       "consent_grant_id": "22222222-3333-4444-8444-666666666666", "policy_version": "p", "consent_text_sha256": "h" * 64,
       "source_app": "safetyreport", "source_mode": "server"}
T0 = datetime(2026, 10, 22, 3, 0, tzinfo=timezone.utc).timestamp()  # KST 12:00


def backfill(**kw):
    base = {"state": "filling", "phase": "filling", "reason": None, "next_attempt_at": None, "total": 100, "stored": 10, "queued": 1,
            "pending": 89, "holds": 0, "stale_holds": 0, "raw_unavailable": 0, "not_eligible": 0,
            "run_started_at": T0 - 3600, "updated_at": T0}
    base.update(kw)
    return base


OPT = {"required": False, "running": False, "holds_only": False}


class VectorTest(unittest.TestCase):
    def test_contract_vectors(self):
        data = json.loads((ROOT / "contracts/community-ingest-v2/vectors/progress.json").read_text(encoding="utf-8"))
        for case in data["cases"]:
            if "body" not in case:
                continue
            with self.subTest(case["name"]):
                expected = case["expect"] == "ok"
                body = case["body"]
                # 모바일 전용 본문(pace 포함 모바일)도 형식은 같은 검사로 본다
                self.assertEqual(pr.validate_body(body), expected)


class SendTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = CommunityStore(self.tmp.name + "/community.db")
        self.store.set_context(**CTX)
        self.clock = [T0]
        self.calls = []
        self.reply = (200, b'{"protocol":1,"stored":true}', {})
        for p in (mock.patch.object(pr, "_now", lambda: self.clock[0]),
                  mock.patch.object(pr, "_credentials", return_value=("https://x.supabase.example", "pub", "tok")),
                  mock.patch.object(pr, "_http_post", side_effect=self._post),
                  mock.patch("services.community_gate._platform", return_value="docker"),
                  mock.patch("services.community_ingest_client._client_version", return_value="3.6.2")):
            p.start()
            self.addCleanup(p.stop)

    def _post(self, url, headers, body):
        self.calls.append((url, headers, body))
        return self.reply

    def send(self, trigger, bf=None, opt=None, login="ok"):
        pr.maybe_send(store=self.store, trigger=trigger, backfill=bf or backfill(), optimize=opt or OPT, official_login=login)

    def advance(self, seconds):
        self.clock[0] += seconds

    def test_body_headers_and_no_gzip(self):
        self.send("start")
        url, headers, raw = self.calls[0]
        self.assertTrue(url.endswith("/functions/v1/community-ingest/progress"))
        self.assertNotIn("Content-Encoding", headers)
        self.assertEqual(headers["Authorization"], "Bearer tok")
        body = json.loads(raw)
        self.assertTrue(pr.validate_body(body))
        self.assertIsNone(body["pace"])
        self.assertEqual(body["source_app"], "safetyreport")
        self.assertEqual(body["platform"], "docker")
        self.assertEqual(body["backfill"]["updated_at"], "2026-10-22T03:00:00Z")  # epoch -> ISO Z
        self.assertLessEqual(len(raw), 4096)

    def test_no_connection_sends_nothing(self):
        other = CommunityStore(self.tmp.name + "/other.db")
        pr.maybe_send(store=other, trigger="start", backfill=backfill(), optimize=OPT, official_login="ok")
        self.assertEqual(self.calls, [])

    def test_second_within_60_seconds_blocked_even_for_state_change(self):
        self.send("start")
        self.advance(59)
        self.send("state_change", backfill(state="paused", reason="network"))
        self.assertEqual(len(self.calls), 1)
        self.advance(2)
        self.send("state_change", backfill(state="paused", reason="network"))
        self.assertEqual(len(self.calls), 2)

    def test_state_change_ignores_counts_only(self):
        self.send("start")
        self.advance(120)
        self.send("state_change", backfill(stored=50, pending=49))
        self.assertEqual(len(self.calls), 1)
        for change in (dict(phase="uploading"), dict(reason="timeout")):
            self.advance(120)
            self.send("state_change", backfill(**change))
        self.assertEqual(len(self.calls), 3)
        self.advance(120)
        self.send("state_change", backfill(reason="timeout"), login="manual_required")
        self.send("state_change", backfill(reason="timeout"), opt={"required": True, "running": False, "holds_only": False}, login="manual_required")
        self.assertEqual(len(self.calls), 4)  # 같은 60초 안 두 번째는 막힘

    def test_periodic_15_minutes_only_while_in_progress(self):
        self.send("start")
        self.advance(14 * 60)
        self.send("periodic")
        self.assertEqual(len(self.calls), 1)
        self.advance(61)
        self.send("periodic")
        self.assertEqual(len(self.calls), 2)
        self.advance(16 * 60)
        self.send("periodic", backfill(state="done"))
        self.assertEqual(len(self.calls), 2)
        self.send("periodic", backfill(state="none"))
        self.assertEqual(len(self.calls), 2)

    def test_daily_once_per_kst_day_when_done(self):
        done = backfill(state="done", phase=None)
        self.send("state_change", done)
        self.advance(3600)
        self.send("daily", done)
        self.assertEqual(len(self.calls), 1)  # 오늘(KST) 이미 보냄
        self.advance(13 * 3600)  # KST 다음 날 01:00
        self.send("daily", done)
        self.assertEqual(len(self.calls), 2)
        self.advance(3600)
        self.send("daily", done)
        self.assertEqual(len(self.calls), 2)
        self.send("daily", backfill())  # 진행 중이면 daily 는 보내지 않는다
        self.assertEqual(len(self.calls), 2)

    def test_failures_are_quiet_and_not_retried_for_same_state(self):
        self.reply = (500, b"{}", {})
        self.send("start")
        self.advance(120)
        self.send("state_change")  # 같은 상태 — 재시도하지 않는다
        self.assertEqual(len(self.calls), 1)
        for status in (429, 401, 403, 422):
            self.reply = (status, b'{"error":{"code":"rate_limited"}}', {})
            self.advance(120)
            self.send("start")
        self.assertEqual(len(self.calls), 5)
        with mock.patch.object(pr, "_http_post", side_effect=OSError("down")):
            self.advance(120)
            self.send("start")

    def test_503_maintenance_recorded_then_cleared_on_success(self):
        err = {"error": {"code": "maintenance", "retry_after_seconds": 5400, "resume_at": "2026-10-22T06:30:00.000Z",
                         "pause_message": "서버 저장 공간을 늘리는 중입니다."}}
        self.reply = (503, json.dumps(err).encode(), {"Retry-After": "5400"})
        self.send("start")
        maint = pr.last_maintenance(self.store)
        self.assertEqual(maint["retry_after"], 5400)
        self.assertEqual(maint["message"], "서버 저장 공간을 늘리는 중입니다.")
        self.assertEqual(datetime.fromtimestamp(maint["resume_at"], timezone.utc).isoformat(), "2026-10-22T06:30:00+00:00")
        self.reply = (200, b"{}", {})
        self.advance(120)
        self.send("start")
        self.assertIsNone(pr.last_maintenance(self.store))

    def test_503_without_resume_time(self):
        self.reply = (503, b'{"error":{"code":"maintenance","retry_after_seconds":600,"resume_at":null,"pause_message":null}}', {})
        self.send("start")
        maint = pr.last_maintenance(self.store)
        self.assertIsNone(maint["resume_at"])

    def test_paused_or_locked_sends_nothing(self):
        self.store.set_meta("upload_pause", json.dumps({"paused": True, "resume_at": T0 + 600}))
        self.send("start")
        self.assertEqual(self.calls, [])
        self.store.set_meta("upload_pause", json.dumps({"paused": True, "resume_at": T0 - 5}))
        self.send("start")
        self.assertEqual(len(self.calls), 1)
        self.store.set_meta("client_lock", json.dumps({"locked": True}))
        self.advance(120)
        self.send("start")
        self.assertEqual(len(self.calls), 1)

    def test_unknown_values_become_null_and_stay_valid(self):
        self.send("start", {"state": "weird", "reason": "Has Space http://x", "total": -1, "stored": True})
        body = json.loads(self.calls[0][2])
        self.assertEqual(body["backfill"]["state"], "none")
        self.assertIsNone(body["backfill"]["total"])
        self.assertTrue(pr.validate_body(body))

    def test_credential_failure_is_silent(self):
        with mock.patch.object(pr, "_credentials", side_effect=RuntimeError("no token")):
            self.send("start")
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
