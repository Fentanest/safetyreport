"""uploader 테스트 — B01~B05/B08/B11~B13, C04, H02~H05, reshare, status 범위."""
import json
import os
import tempfile
import threading
import unittest
from unittest import mock

from services import community_capture as cap
from services import community_ingest_client as client
from services.community_ingest_client import EventResult, IngestResponse
from services import community_uploader as up
from services.community_store import CommunityStore

CTX = {"contributor_fingerprint": "f" * 32, "connection_id": "11111111-2222-4333-8444-555555555555",
       "writer_epoch": 1, "dataset_key": "d" * 16, "consent_grant_id": "22222222-3333-4444-8444-666666666666",
       "policy_version": "2026-09-26.1", "consent_text_sha256": "h" * 64,
       "source_app": "safetyreport", "source_mode": "server"}
CTX_B = dict(CTX, contributor_fingerprint="b" * 32,
             connection_id="99999999-2222-4333-8444-555555555555",
             consent_grant_id="aaaaaaaa-3333-4444-8444-666666666666")

INPUT = {"processing_status": "수용", "penalty_amount": "과태료: 40,000원", "report_date": "2026-09-01",
         "response_date": "2026-09-10", "processing_agency": "서울특별시 중구청",
         "person_in_charge": "홍길동", "car_number": "12가3456",
         "violation_location": "서울특별시 중구 세종대로 110", "entry_value": "불법주정차신고",
         "penalty_points": "", "geocode": {"status": "ok", "lat": 37.5662952, "lng": 126.9779451}}


def ack(event_id, status="accepted", projection="published", error_code=None):
    return client.EventResult(event_id=event_id, status=status,
                              durable=status in up.DURABLE_ACK,
                              receipt_id="rcpt-1" if status in up.DURABLE_ACK else None,
                              projection_status=projection if status in up.DURABLE_ACK else None,
                              error_code=error_code, error_retryable=False)


def ok_resp(results, request_id="req-1"):
    return client.IngestResponse(ok=True, http_status=200, code=None, request_id=request_id, results=results)


def err_resp(code, http_status, retry_after=None):
    return client.IngestResponse(ok=False, http_status=http_status, code=code,
                                 retryable=True, retry_after=retry_after)


ok_ack = ack
ack_response = ok_resp


class UploaderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = CommunityStore.open(self.tmp)
        self.store.set_context(**CTX)
        self._gate = mock.patch.object(up, "_gate_check",
                                       return_value={"state": "ok", "can_enter": True, "reasons": []})
        self._gate.start()
        self.posts = []

    def tearDown(self):
        self._gate.stop()
        up.stop_background()
        self.store.close()
        CommunityStore._forget(os.path.join(self.tmp, "community.db"))
        up._active_run = None

    def _capture(self, rid="R1", **kw):
        data = dict(INPUT)
        data.update(kw)
        return cap.capture(data, source_report_id=rid, trigger="realtime", data_dir=self.tmp)

    def _outbox(self):
        return [dict(r) for r in self.store.connect().execute(
            "SELECT o.event_id, o.state, j.source_report_id FROM outbox o"
            " JOIN source_journal j ON j.event_id=o.event_id ORDER BY j.source_revision")]

    def _run_with(self, response, trigger="realtime"):
        with mock.patch.object(client, "post_envelope",
                               side_effect=lambda env: (self.posts.append(env), response)[1]):
            return up.request_upload(trigger, data_dir=self.tmp)

    def test_b01_upload_right_after_capture(self):
        """B01: capture 직후 1초 안에 전송 시도 — 동기 request_upload 1회로 전송."""
        res = self._capture("R1")
        result = self._run_with(ok_resp([ack(res.event_id)]))
        self.assertEqual(result["result"], "success")
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(self._outbox(), [])
        row = self.store.connect().execute(
            "SELECT ack_status, projection_status FROM source_journal WHERE event_id=?",
            (res.event_id,)).fetchone()
        self.assertEqual(row["ack_status"], "accepted")
        self.assertEqual(row["projection_status"], "published")

    def test_b02_upload_5xx_does_not_stop_capture(self):
        """B02: 업로드 5xx 여도 capture 계속."""
        self._capture("R1")
        result = self._run_with(err_resp("server_error", 500))
        self.assertEqual(result["result"], "deferred")
        res2 = self._capture("R2")
        self.assertEqual(res2.event_type, "completed_observation")

    def test_b03_pending_resumes_after_restart(self):
        """B03: 재시작 후 pending 재개."""
        self._capture("R1")
        self.store.close()
        CommunityStore._forget(os.path.join(self.tmp, "community.db"))
        self.store = CommunityStore.open(self.tmp)
        res_id = self.store.connect().execute("SELECT event_id FROM outbox").fetchone()["event_id"]
        result = self._run_with(ok_resp([ack(res_id)]), trigger="recovery")
        self.assertEqual(result["result"], "success")

    def test_b04_lost_response_retransmit_duplicate(self):
        """B04: 응답 유실 → 재전송 → duplicate 도 durable."""
        res = self._capture("R1")
        first = self._run_with(err_resp("offline", None))
        self.assertEqual(first["result"], "deferred")
        with self.store.transaction() as tx:
            tx.execute("UPDATE outbox SET next_retry_at='2000-01-01T00:00:00.000Z'")
        second = self._run_with(ok_resp([ack(res.event_id, status="duplicate")]), trigger="recovery")
        self.assertEqual(second["result"], "success")
        count = self.store.connect().execute("SELECT COUNT(*) v FROM source_journal").fetchone()["v"]
        self.assertEqual(count, 1)

    def test_b05_conflict_dead_letter_journal_kept(self):
        """B05: conflict → dead_letter, journal 불변."""
        res = self._capture("R1")
        before = self.store.connect().execute(
            "SELECT payload_json FROM source_journal WHERE event_id=?", (res.event_id,)).fetchone()
        result = self._run_with(ok_resp([ack(res.event_id, status="conflict")]))
        self.assertEqual(result["result"], "partial")
        self.assertEqual(self._outbox()[0]["state"], "dead_letter")
        after = self.store.connect().execute(
            "SELECT payload_json FROM source_journal WHERE event_id=?", (res.event_id,)).fetchone()
        self.assertEqual(dict(before), dict(after))

    def test_b08_concurrent_requests_send_once(self):
        """B08: 두 스레드 동시 request_upload → 중복 전송 0."""
        res = self._capture("R1")
        import time as _t
        calls = []

        def fake_post(env):
            calls.append(1)
            _t.sleep(0.5)
            return ok_resp([ack(res.event_id)])

        results = []
        with mock.patch.object(client, "post_envelope", side_effect=fake_post):
            threads = [threading.Thread(
                target=lambda: results.append(up.request_upload("manual", data_dir=self.tmp)))
                for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)
        self.assertEqual(len(results), 2)
        self.assertEqual(calls, [1])
        self.assertEqual(results[0]["run_id"], results[1]["run_id"])

    def test_b11_status_branches(self):
        """B11: 401/403/409(conflict)/413/422/429/5xx/timeout 분기."""
        cases = [
            ("auth_required", 401, "auth_required", "auth_required"),
            ("consent_revoked", 403, "consent_required", "blocked"),
            ("conflict-event", 200, "partial", "dead_letter"),
            ("payload_too_large", 413, "partial", "dead_letter"),
            ("schema_invalid", 422, "partial", "dead_letter"),
            ("rate_limited", 429, "deferred", "retry_wait"),
            ("server_error", 500, "deferred", "retry_wait"),
            ("offline", None, "deferred", "retry_wait"),
        ]
        for idx, (code, http_status, run_result, outbox_state) in enumerate(cases):
            with self.subTest(code=code):
                rid = f"B11-{idx}"
                res = self._capture(rid)
                if code == "conflict-event":
                    resp = ok_resp([ack(res.event_id, status="conflict")])
                else:
                    resp = err_resp(code, http_status, retry_after=1 if code == "rate_limited" else None)
                with mock.patch.object(client, "post_envelope", return_value=resp):
                    with mock.patch("services.community_auth_service.get_access_token",
                                    return_value="tok"):
                        result = up.request_upload("realtime", data_dir=self.tmp)
                self.assertEqual(result["result"], run_result, code)
                row = self.store.connect().execute(
                    "SELECT state FROM outbox WHERE event_id=?", (res.event_id,)).fetchone()
                self.assertIsNotNone(row, code)
                self.assertEqual(row["state"], outbox_state, code)

    def test_b12_partial_ack_deletes_only_success(self):
        """B12: partial ACK — 성공분만 삭제."""
        res1 = self._capture("R1")
        res2 = self._capture("R2")

        def fake_post(env):
            results = []
            for item in env["events"]:
                if item["source_report_id"] == "R1":
                    results.append(ack(item["event_id"]))
                else:
                    results.append(ack(item["event_id"], status="rejected", error_code="deleted"))
            return ok_resp(results)

        with mock.patch.object(client, "post_envelope", side_effect=fake_post):
            result = up.request_upload("realtime", data_dir=self.tmp)
        self.assertEqual(result["result"], "partial")
        remaining = self._outbox()
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["source_report_id"], "R2")
        self.assertEqual(remaining[0]["state"], "blocked")

    def test_b13_outbox_capacity_warning(self):
        """B13: outbox 용량 경고."""
        self.assertFalse(up.outbox_size_warning(data_dir=self.tmp))
        with mock.patch("os.path.getsize", return_value=201 * 1024 * 1024):
            self.assertTrue(up.outbox_size_warning(data_dir=self.tmp))

    def test_c04_other_account_rows_not_sent(self):
        """C04: context 가 다른 계정이면 이전 계정 행 전송 0."""
        self._capture("R1")
        self.store.set_context(**CTX_B)
        with mock.patch.object(client, "post_envelope") as poster:
            result = up.request_upload("manual", data_dir=self.tmp)
        poster.assert_not_called()
        self.assertEqual(result["result"], "no_change")

    def test_h02_manual_does_not_read_personal_db(self):
        """H02: 수동 업로드가 개인 DB 를 읽지 않음."""
        res = self._capture("R1")
        with mock.patch("core.database.engine.get_engine",
                        side_effect=AssertionError("must not touch personal db")):
            result = self._run_with(ok_resp([ack(res.event_id)]), trigger="manual")
        self.assertEqual(result["result"], "success")

    def test_h03_journal_kept_after_ack(self):
        """H03: ACK 후 journal 유지."""
        res = self._capture("R1")
        self._run_with(ok_resp([ack(res.event_id)]), trigger="manual")
        row = self.store.connect().execute(
            "SELECT ack_status FROM source_journal WHERE event_id=?", (res.event_id,)).fetchone()
        self.assertEqual(row["ack_status"], "accepted")

    def test_h04_reclick_is_no_change(self):
        """H04: 재클릭 no_change."""
        res = self._capture("R1")
        first = self._run_with(ok_resp([ack(res.event_id)]), trigger="manual")
        self.assertEqual(first["result"], "success")
        with mock.patch.object(client, "post_envelope") as poster:
            second = up.request_upload("manual", data_dir=self.tmp)
        poster.assert_not_called()
        self.assertEqual(second["result"], "no_change")

    def test_sol01_stale_pending_row_of_acked_event_is_removed_not_sent(self):
        """SOL-01(2026-09-26 감사): ACK 된 journal 의 남은 대기 행은 보내지 않고 정리한다(모바일과 같은 단계)."""
        res = self._capture("R1")
        self._run_with(ok_resp([ack(res.event_id)]), trigger="manual")
        with self.store.transaction() as tx:
            tx.execute("INSERT INTO outbox(event_id, state, attempt_count, enqueued_trigger, enqueued_at)"
                       " VALUES (?, 'pending', 0, 'manual', '2026-09-26T00:00:00Z')", (res.event_id,))
        for trigger in ("manual", "midnight", "recovery"):
            with mock.patch.object(client, "post_envelope") as poster:
                up.request_upload(trigger, data_dir=self.tmp)
            poster.assert_not_called()
        self.assertEqual(self._outbox(), [])

    def test_h05_simultaneous_triggers_single_lease(self):
        """H05: realtime·manual·midnight 동시 → lease 1개."""
        res = self._capture("R1")
        import time as _t
        calls = []

        def fake_post(env):
            calls.append(1)
            _t.sleep(0.5)
            return ok_resp([ack(res.event_id)])

        results = []
        with mock.patch.object(client, "post_envelope", side_effect=fake_post):
            threads = [threading.Thread(
                target=lambda t=t: results.append(up.request_upload(t, data_dir=self.tmp)))
                for t in ("realtime", "manual", "midnight")]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)
        self.assertEqual(len(results), 3)
        self.assertEqual(calls, [1])

    def test_reshare(self):
        """reshare: 새 grant/connection 으로 재발급 후 업로드."""
        res = self._capture("R1")
        self._run_with(ok_resp([ack(res.event_id)]), trigger="manual")
        self.assertEqual(up.reshare_candidates(data_dir=self.tmp), 0)
        self.store.set_context(**CTX_B)
        self.assertEqual(up.reshare_candidates(data_dir=self.tmp), 1)
        with mock.patch.object(client, "post_envelope",
                               side_effect=lambda env: ok_resp([ack(e["event_id"]) for e in env["events"]])):
            result = up.request_reshare(data_dir=self.tmp)
        self.assertEqual(result["result"], "success")
        self.assertEqual(result.get("reshared"), 1)
        row = self.store.connect().execute(
            "SELECT event_type, consent_grant_id, captured_at FROM source_journal"
            " ORDER BY source_revision DESC LIMIT 1").fetchone()
        self.assertEqual(row["event_type"], "reshare")
        self.assertEqual(row["consent_grant_id"], CTX_B["consent_grant_id"])

    def test_upload_status_scoped_to_current_account(self):
        """upload_status 는 현재 context 계정 것만."""
        self._capture("R1")
        status = up.upload_status(data_dir=self.tmp)
        self.assertTrue(status["has_journal"])
        self.assertEqual(status["pending"], 1)
        self.store.set_context(**CTX_B)
        status2 = up.upload_status(data_dir=self.tmp)
        self.assertEqual(status2["pending"], 0)
        self.assertFalse(status2["has_journal"])

    def test_gate_consent_required(self):
        with mock.patch.object(up, "_gate_check",
                               return_value={"state": "blocked", "can_enter": False,
                                             "reasons": ["consent_revoked"], "verified_age": 99.0}):
            result = up.request_upload("manual", data_dir=self.tmp)
        self.assertEqual(result["result"], "consent_required")

    def test_refresh_server_completed_replaces(self):
        _check_manifest_contract(self)

    def test_projection_status_saved_and_shown(self):
        """ACK projection_status 5종 저장·패널 반영."""
        for idx, projection in enumerate(["published", "removed", "held", "not_public", "not_applicable"]):
            rid = f"P{idx}"
            res = self._capture(rid)
            with mock.patch.object(client, "post_envelope",
                                   return_value=ok_resp([ack(res.event_id, projection=projection)])):
                up.request_upload("realtime", data_dir=self.tmp)
        status = up.upload_status(data_dir=self.tmp)
        for projection in ("published", "removed", "held", "not_public", "not_applicable"):
            self.assertEqual(status["projections"].get(projection), 1, projection)


class UploaderBranchesTest(UploaderTest):
    def setUp(self):
        super().setUp()
        self.sent = []
        self.post_count = 0
        self.next_response = None
        self._post = mock.patch.object(client, "post_envelope", side_effect=self._fake_post)
        self._post.start()
        self.addCleanup(self._post.stop)

    def tearDown(self):
        super().tearDown()

    def _fake_post(self, envelope):
        self.post_count += 1
        self.sent.append(envelope)
        if self.next_response is not None:
            resp = self.next_response
            return resp(envelope) if callable(resp) else resp
        return ack_response([ok_ack(e["event_id"]) for e in envelope["events"]],
                            request_id=f"req-{self.post_count}")

    def test_b03_pending_resumes_after_restart(self):
        self._capture()
        self.next_response = IngestResponse(ok=False, http_status=None, code="offline", retryable=True)
        up.request_upload("realtime", data_dir=self.tmp)
        pending = self.store.connect().execute(
            "SELECT COUNT(*) v FROM outbox WHERE state='retry_wait'").fetchone()["v"]
        self.assertEqual(pending, 1)
        self.next_response = None
        self.store.close()
        CommunityStore._forget(os.path.join(self.tmp, "community.db"))
        self.store = CommunityStore.open(self.tmp)
        with self.store.transaction() as tx:
            tx.execute("UPDATE outbox SET next_retry_at='2000-01-01T00:00:00.000Z'")
        result = up.request_upload("recovery", data_dir=self.tmp)
        self.assertEqual(result["result"], "success")

    def test_b04_offline_then_duplicate_ack(self):
        self._capture()
        self.next_response = IngestResponse(ok=False, http_status=None, code="offline", retryable=True)
        first = up.request_upload("realtime", data_dir=self.tmp)
        self.assertEqual(first["result"], "deferred")
        with self.store.transaction() as tx:
            tx.execute("UPDATE outbox SET next_retry_at='2000-01-01T00:00:00.000Z'")
        self.next_response = None
        second = up.request_upload("recovery", data_dir=self.tmp)
        self.assertEqual(second["result"], "success")
        journal = self.store.connect().execute("SELECT COUNT(*) v FROM source_journal").fetchone()["v"]
        self.assertEqual(journal, 1)

    def test_b11_status_branches(self):
        branches = [
            (IngestResponse(ok=False, http_status=401, code="auth_required", retryable=True), "auth_required"),
            (IngestResponse(ok=False, http_status=403, code="consent_revoked"), "blocked"),
            (IngestResponse(ok=False, http_status=413, code="payload_too_large"), "dead_letter"),
            (IngestResponse(ok=False, http_status=422, code="schema_invalid"), "dead_letter"),
            (IngestResponse(ok=False, http_status=429, code="rate_limited", retryable=True, retry_after=1),
             "retry_wait"),
            (IngestResponse(ok=False, http_status=500, code="server_error", retryable=True), "retry_wait"),
            (IngestResponse(ok=False, http_status=None, code="offline", retryable=True), "retry_wait"),
        ]
        for index, (resp, expected_state) in enumerate(branches):
            with self.subTest(code=resp.code):
                report_id = f"B11-{index}"
                self._capture(report_id)
                self.next_response = resp
                with mock.patch("services.community_auth_service.get_access_token", return_value="tok"):
                    up.request_upload("realtime", data_dir=self.tmp)
                row = self.store.connect().execute(
                    "SELECT o.state FROM outbox o JOIN source_journal j ON j.event_id=o.event_id"
                    " WHERE j.source_report_id=?", (report_id,)).fetchone()
                if resp.code == "auth_required":
                    self.assertEqual(row["state"] if row else "auth_required", "auth_required")
                else:
                    self.assertIsNotNone(row)
                    self.assertEqual(row["state"], expected_state)
                self.next_response = None

    def test_b13_outbox_capacity_warning(self):
        self.assertFalse(up.outbox_size_warning(data_dir=self.tmp))
        journal = self.store.connect().execute("SELECT COUNT(*) v FROM source_journal").fetchone()["v"]
        self.assertEqual(journal, 0)

    def test_c04_other_account_rows_never_sent(self):
        self._capture("R1")
        sent_before = self.post_count
        self.store.set_context(**CTX_B)
        result = up.request_upload("manual", data_dir=self.tmp)
        self.assertEqual(result["result"], "no_change")
        self.assertEqual(self.post_count, sent_before)

    def test_h02_manual_does_not_read_personal_db(self):
        self._capture("R1")
        with mock.patch("core.database.engine.get_engine",
                        side_effect=AssertionError("personal DB must not be read")):
            result = up.request_upload("manual", data_dir=self.tmp)
        self.assertEqual(result["result"], "success")

    def test_h03_journal_kept_after_ack(self):
        event = self._capture("R1")
        up.request_upload("manual", data_dir=self.tmp)
        row = self.store.connect().execute(
            "SELECT ack_status, projection_status FROM source_journal WHERE event_id=?",
            (event.event_id,)).fetchone()
        self.assertEqual(row["ack_status"], "accepted")
        self.assertEqual(row["projection_status"], "published")

    def test_h04_reclick_is_no_change(self):
        self._capture("R1")
        first = up.request_upload("manual", data_dir=self.tmp)
        self.assertEqual(first["result"], "success")
        second = up.request_upload("manual", data_dir=self.tmp)
        self.assertEqual(second["result"], "no_change")
        self.assertEqual(self.post_count, 1)

    def test_h05_simultaneous_triggers_single_lease(self):
        self._capture("R1")
        results = []
        barrier = threading.Barrier(3)

        def run(trigger):
            barrier.wait()
            results.append(up.request_upload(trigger, data_dir=self.tmp))

        threads = [threading.Thread(target=run, args=(t,)) for t in ("realtime", "manual", "midnight")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        self.assertEqual(len(results), 3)
        self.assertEqual(self.post_count, 1)

    def test_quarantined_is_durable_with_projection(self):
        event = self._capture("R1")
        self.next_response = ack_response(
            [EventResult(event_id=event.event_id, status="quarantined", durable=True,
                         projection_status="held", error_code="status_mapping_mismatch")])
        result = up.request_upload("realtime", data_dir=self.tmp)
        self.assertEqual(result["counts"]["quarantined"], 1)
        row = self.store.connect().execute(
            "SELECT ack_status, projection_status FROM source_journal WHERE event_id=?",
            (event.event_id,)).fetchone()
        self.assertEqual(row["ack_status"], "quarantined")
        self.assertEqual(row["projection_status"], "held")
        outbox = self.store.connect().execute("SELECT COUNT(*) v FROM outbox").fetchone()["v"]
        self.assertEqual(outbox, 0)

    def test_reshare_reissues_with_new_grant(self):
        event = self._capture("R1")
        up.request_upload("manual", data_dir=self.tmp)
        row = self.store.connect().execute(
            "SELECT ack_status FROM source_journal WHERE event_id=?", (event.event_id,)).fetchone()
        self.assertEqual(row["ack_status"], "accepted")
        self.store.set_context(**CTX_B)
        self.assertEqual(up.reshare_candidates(data_dir=self.tmp), 1)
        result = up.request_reshare(data_dir=self.tmp)
        self.assertEqual(result["result"], "success")
        rows = self.store.connect().execute(
            "SELECT event_type, consent_grant_id FROM source_journal ORDER BY source_revision").fetchall()
        self.assertEqual(rows[-1]["event_type"], "reshare")
        self.assertEqual(rows[-1]["consent_grant_id"], CTX_B["consent_grant_id"])

    def test_upload_status_scoped_to_current_account(self):
        self._capture("R1")
        up.request_upload("manual", data_dir=self.tmp)
        status = up.upload_status(data_dir=self.tmp)
        self.assertTrue(status["has_journal"])
        self.store.set_context(**CTX_B)
        status_b = up.upload_status(data_dir=self.tmp)
        self.assertEqual(status_b["pending"], 0)
        self.assertEqual(status_b["needs_attention"], 0)
        self.assertFalse(status_b["has_journal"])

    def test_gate_blocked_result(self):
        with mock.patch.object(up, "_gate_check",
                               return_value={"state": "blocked", "can_enter": False,
                                             "reasons": ["consent_revoked"], "verified_age": 0.0}):
            result = up.request_upload("manual", data_dir=self.tmp)
        self.assertEqual(result["result"], "consent_required")

    def test_refresh_server_completed_replaces_atomically(self):
        _check_manifest_contract(self)

def _page(keys, token="7", total=None, next_after=None, **over):
    """account-api.md manifest 응답 모양(protocol·dataset_key·writer_epoch·total·manifest_token·key_prefixes·next_after)."""
    body = {"protocol": 1, "dataset_key": CTX["dataset_key"], "writer_epoch": CTX["writer_epoch"],
            "total": len(keys) if total is None else total, "manifest_token": token,
            "key_prefixes": keys, "next_after": next_after}
    body.update(over)
    return body


K = [c * 24 for c in "abcdef"]
CURSOR = "e" * 64


def _check_manifest_contract(t):
    """전 페이지·같은 토큰·개수=total·중복 없음·연결 일치일 때만 교체, 토큰 변화 최대 3회, 실패·형식 오류는 교체 없음."""
    rows = lambda: sorted(r["key_prefix"] for r in t.store.connect().execute("SELECT key_prefix FROM server_completed"))
    pages = [_page(K[:2], total=3, next_after=CURSOR), _page(K[2:3], total=3)]
    with mock.patch.object(client, "post_manifest", side_effect=[(True, p) for p in pages]) as post:
        t.assertTrue(up.refresh_server_completed(data_dir=t.tmp))
    t.assertEqual(rows(), K[:3])
    t.assertEqual(post.call_args_list[0].kwargs, {"connection_id": CTX["connection_id"], "after": None, "limit": 5000})
    t.assertEqual(post.call_args_list[1].kwargs["after"], CURSOR)
    t.assertEqual(t.store.meta("manifest_scope"), f"{CTX['dataset_key']}:{CTX['writer_epoch']}")
    for bad in ([_page(K[4:5], total=2)],                                    # 개수 != total
                [_page([K[4], K[4]])],                                       # 중복
                [_page(K[4:5], dataset_key="0" * 64)],                       # 다른 dataset
                [_page(K[4:5], writer_epoch=CTX["writer_epoch"] + 1)],       # 다른 epoch
                [_page(K[4:5], token="7", next_after=CURSOR), _page(K[5:6], token="8"),
                 _page(K[4:5], token="9", next_after=CURSOR), _page(K[5:6], token="10"),
                 _page(K[4:5], token="11", next_after=CURSOR), _page(K[5:6], token="12")]):  # 3회 모두 토큰 변화
        with mock.patch.object(client, "post_manifest", side_effect=[(True, p) for p in bad]):
            t.assertFalse(up.refresh_server_completed(data_dir=t.tmp))
        t.assertEqual(rows(), K[:3], "실패면 이전 목록을 그대로 둔다")
    with mock.patch.object(client, "post_manifest", side_effect=[(False, {})]):
        t.assertFalse(up.refresh_server_completed(data_dir=t.tmp))
    # 토큰이 한 번 바뀌면 처음부터 다시 받아 성공
    retry = [_page(K[:1], token="7", total=2, next_after=CURSOR), _page(K[1:2], token="8", total=2),
             _page([K[5]], token="9")]
    with mock.patch.object(client, "post_manifest", side_effect=[(True, p) for p in retry]):
        t.assertTrue(up.refresh_server_completed(data_dir=t.tmp))
    t.assertEqual(rows(), [K[5]])
    # 자기 업로드가 lease 를 잡고 있으면 교체하지 않는다
    t.assertTrue(t.store.acquire_lease("upload", "other-run", 60))
    try:
        with mock.patch.object(client, "post_manifest") as post:
            t.assertFalse(up.refresh_server_completed(data_dir=t.tmp))
        post.assert_not_called()
    finally:
        t.store.release_lease("upload", "other-run")
    # 빈 dataset: 토큰 "0", total 0 → 빈 목록으로 교체
    with mock.patch.object(client, "post_manifest", side_effect=[(True, _page([], token="0"))]):
        t.assertTrue(up.refresh_server_completed(data_dir=t.tmp))
    t.assertEqual(rows(), [])


class ManifestClientContractTest(unittest.TestCase):
    def test_request_body_and_response_validation(self):
        sent = {}

        def fake_post(url, headers, body, timeout):
            sent["url"], sent["body"] = url, json.loads(body)
            return 200, json.dumps(fake_post.reply).encode(), {}

        cfg = mock.Mock(supabase_url="http://127.0.0.1:56321", publishable_key="sb_publishable_x")
        with mock.patch.object(client, "_config", return_value=cfg), \
             mock.patch("services.community_auth_service.get_access_token", return_value="tok"), \
             mock.patch.object(client, "_http_post", side_effect=fake_post):
            fake_post.reply = _page(K[:1])
            ok, body = client.post_manifest(connection_id=CTX["connection_id"], after=None, limit=9000)
            self.assertTrue(ok)
            self.assertEqual(sent["url"], "http://127.0.0.1:56321/functions/v1/community-ingest/manifest")
            self.assertEqual(sent["body"], {"protocol": 1, "connection_id": CTX["connection_id"], "after": None, "limit": 5000})
            for broken in (dict(_page(K[:1]), key_prefixes=["zz"]), dict(_page(K[:1]), manifest_token="x"),
                           dict(_page(K[:1]), next_after="short"), dict(_page(K[:1]), total=-1),
                           {k: v for k, v in _page(K[:1]).items() if k != "protocol"}, {"keys": K[:1]}):
                fake_post.reply = broken
                self.assertEqual(client.post_manifest(connection_id=CTX["connection_id"]), (False, {}), broken)


if __name__ == "__main__":
    unittest.main()
