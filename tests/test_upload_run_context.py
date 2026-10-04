"""EO R-10: 업로드 실행 상태(UploadRunContext)와 outbox 전이 저장소(_Outbox), 상태 조회 분리."""

import unittest

import test_community_uploader as _uploader_tests

from services import community_upload_status
from services import community_uploader as up


class _Base(unittest.TestCase):
    """UploaderTest 의 저장소·게이트·토큰 준비만 빌린다(그 시험들을 다시 돌리지 않게 상속하지 않는다)."""
    setUp = _uploader_tests.UploaderTest.setUp
    tearDown = _uploader_tests.UploaderTest.tearDown
    _capture = _uploader_tests.UploaderTest._capture


class OutboxTransitionsTest(_Base):
    def _rows(self):
        return [dict(r) for r in self.store.connect().execute(
            "SELECT o.event_id, o.state, o.attempt_count, o.lease_owner, o.last_error_code, j.blocked_reason"
            " FROM outbox o JOIN source_journal j ON j.event_id=o.event_id ORDER BY j.source_revision")]

    def _two_in_flight(self, owner="run:A"):
        self._capture("R1")
        self._capture("R2")
        rows = [{"event_id": r["event_id"], "attempt_count": r["attempt_count"]} for r in self._rows()]
        up._Outbox(self.store).mark_in_flight(rows, owner)
        return rows

    def test_owner_scoped_transitions_leave_other_runs_rows_alone(self):
        rows = self._two_in_flight()
        outbox = up._Outbox(self.store)
        outbox.retry(rows, "lease_lost", owner="run:B")
        outbox.hold([(rows[0], "schema_invalid")], "run:B")
        self.assertEqual([r["state"] for r in self._rows()], ["in_flight", "in_flight"])
        outbox.retry(rows[:1], "lease_lost", owner="run:A")
        outbox.hold([(rows[1], "schema_invalid")], "run:A")
        states = self._rows()
        self.assertEqual([r["state"] for r in states], ["retry_wait", "retry_wait"])
        self.assertEqual([r["last_error_code"] for r in states], ["lease_lost", "schema_invalid"])
        self.assertEqual([r["lease_owner"] for r in states], [None, None])

    def test_attempts_split_dead_auth_and_block(self):
        rows = self._two_in_flight()
        outbox = up._Outbox(self.store)
        self.assertEqual([r["attempt_count"] for r in self._rows()], [1, 1])
        outbox.undo_attempt(rows[:1])
        self.assertEqual([r["attempt_count"] for r in self._rows()], [0, 1])
        outbox.requeue_for_split(rows)
        self.assertEqual([r["state"] for r in self._rows()], ["pending", "pending"])
        outbox.require_auth(rows[:1], "auth_required")
        outbox.block(rows[1:], "consent_revoked")
        states = self._rows()
        self.assertEqual([r["state"] for r in states], ["auth_required", "blocked"])
        self.assertEqual(states[1]["blocked_reason"], "blocked:consent_revoked")
        outbox.dead_letter(rows[:1], "payload_too_large")
        self.assertEqual(self._rows()[0]["state"], "dead_letter")

    def test_expired_in_flight_rows_are_recovered_without_losing_attempts(self):
        self._two_in_flight()
        self.store.connect().execute("UPDATE outbox SET lease_until='2000-01-01T00:00:00Z'")
        self.store.connect().commit()
        up._Outbox(self.store).recover_expired()
        rows = self._rows()
        self.assertEqual([r["state"] for r in rows], ["retry_wait", "retry_wait"])
        self.assertEqual([r["attempt_count"] for r in rows], [1, 1])


class RunContextTest(_Base):
    def test_finish_records_the_run_and_returns_the_same_shape(self):
        messages = []
        run = up.UploadRunContext(store=self.store, run_id="r-1", trigger="manual", started="2026-10-05T00:00:00Z",
                                  progress=messages.append)
        run.counts["sent"] = 3
        run.error_code = "busy"
        out = run.finish("cooldown")
        self.assertEqual(set(out), {"run_id", "result", "counts", "request_ids", "error_code", "next_attempt_at"})
        self.assertIs(out["counts"], run.counts)
        self.assertEqual((out["result"], out["error_code"]), ("cooldown", "busy"))
        self.assertIn("(busy)", messages[-1])
        row = self.store.connect().execute("SELECT result FROM upload_runs WHERE run_id='r-1'").fetchone()
        self.assertEqual(row["result"], "cooldown")

    def test_a_failing_progress_callback_does_not_stop_the_run(self):
        def broken(message):
            raise RuntimeError("ui gone")
        run = up.UploadRunContext(store=self.store, run_id="r-2", trigger="manual", started="2026-10-05T00:00:00Z",
                                  progress=broken)
        self.assertEqual(run.finish("no_pending")["result"], "no_pending")

    def test_status_functions_live_in_their_own_module_with_the_old_names(self):
        self._capture("R1")
        self.assertEqual(up.upload_status(self.tmp), community_upload_status.upload_status(self.tmp))
        self.assertEqual(up.reshare_candidates(self.tmp), community_upload_status.reshare_candidates(self.tmp))


if __name__ == "__main__":
    unittest.main()
