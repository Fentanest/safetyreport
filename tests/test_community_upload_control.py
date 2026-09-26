"""업로드 장애 대응 UC-1 시나리오 (요청서 §12-A) — 실제 ingest 클라이언트 + 가짜 HTTP 계층·시계·난수.

HTTP 는 `community_ingest_client._http_post` 만 바꿔 실제 상태 코드·헤더·본문(JSON 이 아닌 것 포함)을 돌려준다.
시계(`community_uploader._now`)와 난수(`_rand`=0 → 백오프 = 하한)를 고정해 대기 시각을 정확히 확인한다.
모바일 test/community/upload_control_test.dart 가 같은 시나리오를 같은 기대값으로 확인한다.
"""
import json
import os
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from unittest import mock

from services import community_capture as cap
from services import community_ingest_client as client
from services import community_schedule as sched
from services import community_uploader as up
from services.community_store import CommunityStore

CTX = {"contributor_fingerprint": "f" * 32, "connection_id": "11111111-2222-4333-8444-555555555555",
       "writer_epoch": 1, "dataset_key": "d" * 16, "consent_grant_id": "22222222-3333-4444-8444-666666666666",
       "policy_version": "2026-09-26.1", "consent_text_sha256": "h" * 64,
       "source_app": "safetyreport", "source_mode": "server"}
CTX_B = dict(CTX, contributor_fingerprint="b" * 32, connection_id="99999999-2222-4333-8444-555555555555",
             consent_grant_id="aaaaaaaa-3333-4444-8444-666666666666")
INPUT = {"processing_status": "수용", "penalty_amount": "과태료: 40,000원", "report_date": "2026-09-01",
         "response_date": "2026-09-10", "processing_agency": "서울특별시 중구청", "person_in_charge": "홍길동",
         "car_number": "12가3456", "violation_location": "서울특별시 중구 세종대로 110", "entry_value": "불법주정차신고",
         "penalty_points": "", "geocode": {"status": "ok", "lat": 37.5662952, "lng": 126.9779451}}
T0 = datetime(2026, 9, 27, 0, 0, tzinfo=timezone.utc)
RECEIPT = "11111111-1111-4111-8111-111111111111"


def ack_body(events, status="accepted", durable=True, request_id="req-1", only=None):
    results = []
    for e in events:
        if only is not None and e["event_id"] not in only:
            continue
        item = {"event_id": e["event_id"], "status": status, "durable": durable,
                "receipt_id": RECEIPT if durable else None, "projection_status": "published" if durable else "not_applicable"}
        if not durable:
            item["error"] = {"code": "deleted", "retryable": False}
        results.append(item)
    return 200, {}, json.dumps({"protocol": 1, "request_id": request_id, "results": results}).encode()


def err_body(status, code, retry_after=None, header=None):
    err = {"code": code, "message": "m", "request_id": "req-e", "retryable": status >= 429}
    if retry_after:
        err["retry_after_seconds"] = retry_after
    headers = {"Retry-After": header} if header else {}
    return status, headers, json.dumps({"error": err}).encode()


class UploadControlTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = CommunityStore.open(self.tmp)
        self.store.set_context(**CTX)
        self.now = T0
        self.requests = []  # (headers, envelope)
        self.responder = lambda env, headers: ack_body(env["events"])
        cfg = mock.Mock(supabase_url="http://127.0.0.1:56321", publishable_key="sb_publishable_x", configured=True)
        self.token_calls = []

        def token(rejected=None):
            self.token_calls.append(rejected)
            return "tok2" if rejected else "tok"

        patches = [
            mock.patch.object(up, "_gate_check", return_value={"state": "ok", "can_enter": True, "reasons": []}),
            mock.patch.object(up, "_now", lambda: self.now),
            mock.patch.object(up, "_rand", lambda: 0.0),
            mock.patch.object(up, "_sleep", lambda s: None),
            mock.patch.object(up, "_gate_invalidate", lambda reason: None),
            mock.patch.object(client, "_config", return_value=cfg),
            mock.patch.object(client, "_http_post", side_effect=self._http),
            mock.patch("services.community_auth_service.get_access_token", side_effect=token),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        up.stop_background()
        self.store.close()
        CommunityStore._forget(os.path.join(self.tmp, "community.db"))
        up._active_run = None
        up._next_due = None

    def _http(self, url, headers, body, timeout):
        envelope = json.loads(body)
        self.requests.append((dict(headers), envelope))
        result = self.responder(envelope, headers)
        if isinstance(result, Exception):
            raise result
        status, resp_headers, resp_body = result  # 응답 도우미는 (상태, 헤더, 본문) — 실제 _http_post 는 (상태, 본문, 헤더)
        return status, resp_body, resp_headers

    def capture(self, rid, **kw):
        data = dict(INPUT)
        data.update(kw)
        return cap.capture(data, source_report_id=rid, trigger="realtime", data_dir=self.tmp)

    def row(self, event_id):
        r = self.store.connect().execute("SELECT * FROM outbox WHERE event_id=?", (event_id,)).fetchone()
        return dict(r) if r else None

    def journal(self, event_id):
        return dict(self.store.connect().execute("SELECT * FROM source_journal WHERE event_id=?", (event_id,)).fetchone())

    def control(self, kind="service"):
        scope = up._scopes(CTX)[kind]
        r = self.store.connect().execute("SELECT * FROM upload_control WHERE scope=?", (scope,)).fetchone()
        return dict(r) if r else None

    def upload(self, trigger="realtime"):
        return up.request_upload(trigger, data_dir=self.tmp)

    def at(self, value):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    # ── 수집 사본 ─────────────────────────────────────────────────────────

    def test_retry_sends_the_same_captured_copy_and_event_id(self):
        """수집값 A 를 저장한 뒤 개인 DB 가 바뀌어도(uploader 는 개인 DB 를 읽지 않음) 실패·재시도 모두 같은 event_id·A 를 보낸다."""
        res = self.capture("R1")
        stored = self.journal(res.event_id)
        self.responder = lambda env, h: err_body(503, "busy")
        with mock.patch("core.database.engine.get_engine", side_effect=AssertionError("개인 DB 를 읽으면 안 된다")):
            self.assertEqual(self.upload()["result"], "cooldown")
            self.now = T0 + timedelta(minutes=10)
            self.responder = lambda env, h: ack_body(env["events"])
            self.assertEqual(self.upload("recovery")["result"], "sent")
        sent = [env["events"][0] for _, env in self.requests]
        self.assertEqual([e["event_id"] for e in sent], [res.event_id, res.event_id])
        for event in sent:
            self.assertEqual(json.dumps(event["payload"], ensure_ascii=False, separators=(",", ":"), sort_keys=True),
                             json.dumps(json.loads(stored["payload_json"]), ensure_ascii=False, separators=(",", ":"), sort_keys=True))
            self.assertEqual(event["payload_sha256"], stored["payload_sha256"])

    # ── 횟수·대기 ─────────────────────────────────────────────────────────

    def test_attempt_count_and_backoff_grow_with_each_real_request(self):
        res = self.capture("R1")
        self.responder = lambda env, h: err_body(503, "busy")
        expected_row = [2.5, 5.0, 10.0]  # UC-1: 5·2^(n-1)·0.5 (u=0)
        for n, delay in enumerate(expected_row, start=1):
            self.assertEqual(self.upload("recovery")["result"], "cooldown")
            row = self.row(res.event_id)
            self.assertEqual((row["state"], row["attempt_count"]), ("retry_wait", n))
            self.assertEqual(self.at(row["next_retry_at"]) - self.now, timedelta(seconds=delay))
            control = self.control()
            self.assertEqual(control["consecutive_failures"], n)
            self.now = self.at(max(row["next_retry_at"], control["next_attempt_at"])) + timedelta(seconds=1)
        self.assertEqual(len(self.requests), 3, "대기 중인 호출은 요청으로 세지 않는다")

    def test_gate_block_cooldown_and_missing_token_are_not_attempts(self):
        res = self.capture("R1")
        with mock.patch.object(up, "_gate_check", return_value={"state": "consent_required", "can_enter": False,
                                                                 "reasons": ["consent_revoked"]}):
            self.assertEqual(self.upload()["result"], "needs_consent")
        with mock.patch("services.community_auth_service.get_access_token",
                        side_effect=__import__("services.community_auth_service", fromlist=["x"]).CommunityAuthError("auth_unavailable")):
            self.assertEqual(self.upload()["result"], "cooldown")
        self.assertEqual(self.row(res.event_id)["attempt_count"], 0)
        self.assertEqual(self.requests, [])

    # ── 혼잡 시 전체 멈춤 ─────────────────────────────────────────────────

    def test_first_transient_failure_stops_the_remaining_batches(self):
        events = [self.capture(f"R{i}") for i in range(25)]  # 20 + 5 → 요청 2개가 필요한 양
        for status in (429, 503, 500, "offline"):
            with self.subTest(status=status):
                with self.store.transaction() as tx:
                    tx.execute("DELETE FROM upload_control")
                    tx.execute("UPDATE outbox SET state='pending', next_retry_at=NULL, attempt_count=0")
                self.requests.clear()
                self.responder = (lambda env, h: OSError("down")) if status == "offline" else \
                    (lambda env, h, s=status: err_body(s, "rate_limited" if s == 429 else "busy"))
                self.assertEqual(self.upload("manual")["result"], "cooldown")
                self.assertEqual(len(self.requests), 1, "첫 일시 장애 뒤 남은 배치를 보내지 않는다")
                untouched = [e for e in events if self.row(e.event_id)["attempt_count"] == 0]
                self.assertEqual(len(untouched), 5)

    def test_cooldown_holds_for_every_trigger_and_survives_a_restart(self):
        self.capture("R1")
        self.responder = lambda env, h: err_body(429, "rate_limited", retry_after=60, header="60")
        self.assertEqual(self.upload()["result"], "cooldown")
        account = self.control("account")
        self.assertEqual(account["state"], "cooling_down")
        self.assertEqual(self.at(account["next_attempt_at"]) - self.now, timedelta(seconds=60))
        self.capture("R2")  # 새 수집은 로컬에만 쌓인다
        for trigger in ("realtime", "manual", "midnight", "recovery", "reshare"):
            self.assertEqual(self.upload(trigger)["result"], "cooldown", trigger)
        self.store.close()  # 재시작
        CommunityStore._forget(os.path.join(self.tmp, "community.db"))
        self.store = CommunityStore.open(self.tmp)
        self.assertEqual(self.upload("manual")["result"], "cooldown")
        self.assertEqual(len(self.requests), 1)
        self.now = T0 + timedelta(seconds=61)
        self.responder = lambda env, h: ack_body(env["events"])
        self.assertEqual(self.upload("recovery")["result"], "sent")
        self.assertEqual(len(self.requests[1][1]["events"]), 1, "복구 확인은 1건짜리 요청 하나")
        self.assertEqual(self.control("account")["state"], "ready")
        self.assertEqual(len(self.requests), 3, "확인이 성공하면 나머지를 이어서 보낸다")

    def test_retry_after_header_date_body_and_plain_text(self):
        res = self.capture("R1")
        cases = [
            (lambda env, h: (503, {"Retry-After": format_datetime(self.now + timedelta(seconds=300), usegmt=True)},
                             b'{"error":{"code":"busy","message":"m","request_id":"r","retryable":true}}'), 300),
            (lambda env, h: (429, {"retry-after": "120"}, b"slow down"), 120),
            (lambda env, h: err_body(503, "busy", retry_after=45, header="90"), 90),
            (lambda env, h: (503, {}, b"<html>busy</html>"), 2.5),
        ]
        for responder, wait in cases:
            with self.subTest(wait=wait):
                with self.store.transaction() as tx:
                    tx.execute("DELETE FROM upload_control")
                    tx.execute("UPDATE outbox SET state='pending', next_retry_at=NULL, attempt_count=0")
                self.responder = responder
                self.assertEqual(self.upload()["result"], "cooldown")
                row = self.row(res.event_id)
                self.assertEqual(self.at(row["next_retry_at"]) - self.now, timedelta(seconds=wait))

    # ── ACK ───────────────────────────────────────────────────────────────

    def test_durable_false_or_malformed_ack_is_retried_never_completed_or_dead_lettered(self):
        res = self.capture("R1")
        bodies = [
            ack_body([{"event_id": res.event_id}], status="accepted", durable=False),
            (200, {}, json.dumps({"protocol": 1, "request_id": "r", "results": [
                {"event_id": res.event_id, "status": "accepted", "receipt_id": RECEIPT}]}).encode()),
            (200, {}, b"<html>oops</html>"),
            (200, {}, b'{"protocol":2,"request_id":"r","results":[]}'),
            (500, {}, json.dumps({"protocol": 1, "request_id": "r", "results": [
                {"event_id": res.event_id, "status": "accepted", "durable": True, "receipt_id": RECEIPT}]}).encode()),
        ]
        for body in bodies:
            with self.subTest(body=body[2][:40]):
                with self.store.transaction() as tx:
                    tx.execute("DELETE FROM upload_control")
                    tx.execute("UPDATE outbox SET state='pending', next_retry_at=NULL")
                self.responder = lambda env, h, b=body: b
                self.assertEqual(self.upload()["result"], "cooldown")
                self.assertEqual(self.row(res.event_id)["state"], "retry_wait")
                self.assertIsNone(self.journal(res.event_id)["ack_status"])

    def test_empty_results_back_off_instead_of_an_immediate_resend(self):
        res = self.capture("R1")
        self.responder = lambda env, h: (200, {}, b'{"protocol":1,"request_id":"r","results":[]}')
        self.assertEqual(self.upload()["result"], "partial")
        self.assertEqual(len(self.requests), 1)
        row = self.row(res.event_id)
        self.assertEqual((row["state"], row["last_error_code"]), ("retry_wait", "ack_missing"))
        self.assertEqual(self.upload()["result"], "not_due")
        self.assertEqual(len(self.requests), 1)

    def test_partial_ack_completes_only_the_confirmed_event(self):
        a, b = self.capture("R1"), self.capture("R2")
        self.responder = lambda env, h: ack_body(env["events"], only={b.event_id})
        self.assertEqual(self.upload()["result"], "partial")
        self.assertIsNone(self.row(b.event_id))
        self.assertEqual(self.journal(b.event_id)["ack_status"], "accepted")
        self.assertEqual(self.row(a.event_id)["state"], "retry_wait")

    def test_lost_response_after_central_commit_resends_and_counts_once(self):
        res = self.capture("R1")
        central: set[str] = set()

        def responder(env, h):
            fresh = [e for e in env["events"] if e["event_id"] not in central]
            central.update(e["event_id"] for e in env["events"])
            if fresh:
                return OSError("response lost")  # 중앙은 저장했지만 응답을 못 받음
            return ack_body(env["events"], status="duplicate")

        self.responder = responder
        self.assertEqual(self.upload()["result"], "cooldown")
        self.now = T0 + timedelta(minutes=5)
        self.assertEqual(self.upload("recovery")["result"], "sent")
        self.assertEqual(self.journal(res.event_id)["ack_status"], "duplicate")
        self.assertEqual(len(central), 1)
        self.assertEqual({env["events"][0]["event_id"] for _, env in self.requests}, {res.event_id})

    # ── 재시도 실행기·자정 ───────────────────────────────────────────────

    def test_recovery_runs_when_the_retry_time_comes_without_a_new_capture(self):
        res = self.capture("R1")
        self.responder = lambda env, h: err_body(503, "busy")
        self.upload()
        due = up.next_due_at(self.tmp)
        self.assertEqual(due, self.at(self.control()["next_attempt_at"]))
        self.responder = lambda env, h: ack_body(env["events"])
        up.start_background(self.tmp)
        time.sleep(1.5)
        self.assertEqual(len(self.requests), 1, "시각 전에는 깨지 않는다")
        self.now = due + timedelta(seconds=1)
        deadline = time.time() + 10
        while self.row(res.event_id) is not None and time.time() < deadline:
            time.sleep(0.1)
        self.assertIsNone(self.row(res.event_id), "재시도 시각이 되면 새 수집 없이 복구한다")

    def test_midnight_success_does_not_block_later_recovery(self):
        first = self.capture("R1")
        self.assertEqual(sched.run_midnight(now_utc=self.now, data_dir=self.tmp)["result"], "succeeded")
        self.assertIsNone(self.row(first.event_id))
        second = self.capture("R2")
        self.responder = lambda env, h: err_body(503, "busy")
        self.upload()
        self.now = T0 + timedelta(minutes=10)
        self.responder = lambda env, h: ack_body(env["events"])
        self.assertEqual(sched.run_midnight(now_utc=self.now, data_dir=self.tmp)["result"], "already_succeeded")
        self.assertEqual(self.upload("recovery")["result"], "sent")
        self.assertIsNone(self.row(second.event_id))

    def test_midnight_is_not_succeeded_while_rows_wait(self):
        self.capture("R1")
        self.responder = lambda env, h: err_body(503, "busy")
        self.assertEqual(sched.run_midnight(now_utc=self.now, data_dir=self.tmp)["result"], "deferred")
        self.assertEqual(sched.run_midnight(now_utc=self.now, data_dir=self.tmp)["result"], "deferred")
        row = self.store.connect().execute("SELECT state, deferred_reason FROM schedule_runs").fetchone()
        self.assertEqual((row["state"], row["deferred_reason"]), ("deferred", "busy"))

    # ── lease·재시작 ─────────────────────────────────────────────────────

    def test_expired_in_flight_is_recovered_but_a_live_run_is_not_touched(self):
        res = self.capture("R1")
        with self.store.transaction() as tx:
            tx.execute("UPDATE outbox SET state='in_flight', lease_owner='run:dead', lease_until=?",
                       (up._iso(self.now - timedelta(minutes=5)),))
        self.assertTrue(self.store.acquire_lease("upload", "run:other-live", 60))
        self.assertEqual(self.upload()["result"], "busy_other_run")
        self.assertEqual(self.row(res.event_id)["state"], "in_flight")
        self.assertEqual(self.requests, [])
        self.store.release_lease("upload", "run:other-live")
        self.assertEqual(self.upload("recovery")["result"], "sent")
        self.assertIsNone(self.row(res.event_id))

    def test_losing_the_lease_mid_run_stops_new_batches(self):
        for i in range(25):
            self.capture(f"R{i}")
        real = self.store.renew_lease
        calls = []

        def renew(name, owner, seconds):
            calls.append(owner)
            return real(name, owner, seconds) if len(calls) == 1 else False

        with mock.patch.object(self.store, "renew_lease", side_effect=renew):
            self.assertEqual(self.upload("manual")["result"], "busy_other_run")
        self.assertEqual(len(self.requests), 1)

    def test_two_runners_drain_once(self):
        self.capture("R1")
        gate = threading.Event()

        def slow(env, h):
            gate.wait(5)
            return ack_body(env["events"])

        self.responder = slow
        results = []
        threads = [threading.Thread(target=lambda t=t: results.append(self.upload(t))) for t in ("realtime", "manual")]
        for t in threads:
            t.start()
        time.sleep(0.3)
        gate.set()
        for t in threads:
            t.join(15)
        self.assertEqual(len(self.requests), 1)

    def test_a_joined_midnight_call_returns_its_own_run_not_the_running_one(self):
        """실시간 실행 중 합류한 자정 호출은 앞선 실행의 결과를 자기 것으로 쓰지 않는다(Sol 구현 검토) — 끝난 뒤 직접 실행한다."""
        self.capture("R1")
        with self.store.transaction() as tx:  # outbox 에 없는 미ACK journal(자정 enqueue 로만 보낼 수 있음)
            tx.execute("DELETE FROM outbox")
        self.capture("R2")
        gate = threading.Event()

        def slow(env, h):
            gate.wait(5)
            return ack_body(env["events"])

        self.responder = slow
        results = {}
        first = threading.Thread(target=lambda: results.__setitem__("realtime", self.upload("realtime")))
        first.start()
        time.sleep(0.3)
        second = threading.Thread(target=lambda: results.__setitem__("midnight", self.upload("midnight")))
        second.start()
        time.sleep(0.3)
        gate.set()
        first.join(15)
        second.join(15)
        self.assertNotEqual(results["midnight"]["run_id"], results["realtime"]["run_id"])
        self.assertEqual(results["midnight"]["result"], "sent")
        sent = [e["event_id"] for _, env in self.requests for e in env["events"]]
        self.assertEqual(len(sent), 2, "자정 실행이 outbox 밖 journal 을 enqueue 해 보냈다")

    def test_a_realtime_start_is_promoted_while_an_enqueue_call_waits(self):
        """realtime 이 먼저 시작해도 기다리는 manual/midnight/recovery 가 굶지 않는다(Sol 3차 확인)."""
        self.assertEqual(up._effective_trigger("realtime"), "realtime")
        with mock.patch.dict(up._waiting_enqueue, {"manual": 1}):
            self.assertEqual(up._effective_trigger("realtime"), "manual")
            self.assertEqual(up._effective_trigger("reshare"), "reshare")
        with mock.patch.dict(up._waiting_enqueue, {"manual": 1, "midnight": 1}):
            self.assertEqual(up._effective_trigger("realtime"), "midnight")

    def test_losing_the_lease_never_touches_a_held_suspect_now_owned_by_another_run(self):
        bad, other = self.capture("R1"), self.capture("R2")

        def responder(env, h):
            ids = [e["event_id"] for e in env["events"]]
            if bad.event_id in ids:
                return err_body(422, "schema_invalid")
            with self.store.transaction() as tx:  # 갱신 사이 lease 만료 → 다른 실행이 lease 와 보류 행을 가져갔다
                tx.execute("UPDATE leases SET owner='run:second' WHERE name='upload'")
                tx.execute("UPDATE outbox SET lease_owner='run:second' WHERE event_id=?", (bad.event_id,))
            return err_body(401, "auth_required")

        self.responder = responder
        self.assertEqual(self.upload("manual")["result"], "busy_other_run")
        held = self.row(bad.event_id)
        self.assertEqual((held["state"], held["lease_owner"]), ("in_flight", "run:second"))
        self.assertEqual(self.row(other.event_id)["state"], "retry_wait")

    def test_many_joined_manual_calls_share_one_run_started_after_they_arrived(self):
        self.capture("R1")
        gate = threading.Event()

        def slow(env, h):
            gate.wait(5)
            return ack_body(env["events"])

        self.responder = slow
        results = {}
        first = threading.Thread(target=lambda: results.__setitem__("rt", self.upload("realtime")))
        first.start()
        time.sleep(0.3)
        joiners = [threading.Thread(target=lambda i=i: results.__setitem__(f"m{i}", self.upload("manual"))) for i in range(4)]
        for t in joiners:
            t.start()
        time.sleep(0.3)
        gate.set()
        for t in [first, *joiners]:
            t.join(20)
        manual_runs = {results[f"m{i}"]["run_id"] for i in range(4)}
        self.assertEqual(len(manual_runs), 1, "도착 뒤 시작한 manual 실행 하나로 합쳐진다")
        self.assertNotIn(results["rt"]["run_id"], manual_runs)

    def test_a_midnight_run_that_raises_is_recorded_failed_not_left_running(self):
        self.capture("R1")
        with mock.patch.object(up, "request_upload", side_effect=RuntimeError("boom")):
            self.assertEqual(sched.run_midnight(now_utc=self.now, data_dir=self.tmp)["result"], "failed")
        row = self.store.connect().execute("SELECT state, deferred_reason, lease_owner FROM schedule_runs").fetchone()
        self.assertEqual((row["state"], row["deferred_reason"], row["lease_owner"]), ("failed", "RuntimeError", None))

    # ── 인증·동의 ────────────────────────────────────────────────────────

    def test_401_forces_a_real_refresh_and_resends_once(self):
        res = self.capture("R1")
        self.responder = lambda env, h: ack_body(env["events"]) if h["Authorization"] == "Bearer tok2" else \
            err_body(401, "auth_required")
        self.assertEqual(self.upload()["result"], "sent")
        self.assertEqual([h["Authorization"] for h, _ in self.requests], ["Bearer tok", "Bearer tok2"])
        self.assertIn("tok", self.token_calls, "거절된 토큰으로 강제 갱신")
        self.assertEqual(self.journal(res.event_id)["ack_status"], "accepted")

    def test_the_resend_after_401_keeps_the_interval_and_the_lease(self):
        self.capture("R1")
        self.responder = lambda env, h: ack_body(env["events"]) if h["Authorization"] == "Bearer tok2" else \
            err_body(401, "auth_required")
        sleeps, renews = [], []
        real_renew = self.store.renew_lease

        def renew(name, owner, seconds):
            renews.append(len(self.requests))
            return real_renew(name, owner, seconds)

        with mock.patch.object(up, "_sleep", lambda s: sleeps.append((len(self.requests), s))), \
                mock.patch.object(self.store, "renew_lease", side_effect=renew):
            self.assertEqual(self.upload()["result"], "sent")
        self.assertEqual(renews, [0, 1], "요청마다(재전송 포함) 소유권 heartbeat")
        self.assertTrue(any(n == 1 and s > 1.0 for n, s in sleeps), sleeps)

    def test_the_resend_after_401_stops_when_the_lease_was_taken(self):
        res = self.capture("R1")

        def responder(env, h):
            # 토큰 갱신 사이에 lease 가 만료돼 다른 실행이 lease 와 같은 행을 가져갔다
            with self.store.transaction() as tx:
                tx.execute("UPDATE leases SET owner='run:second' WHERE name='upload'")
                tx.execute("UPDATE outbox SET lease_owner='run:second', attempt_count=attempt_count+1")
            return err_body(401, "auth_required")

        self.responder = responder
        self.assertEqual(self.upload()["result"], "busy_other_run")
        self.assertEqual(len(self.requests), 1)
        row = self.row(res.event_id)
        self.assertEqual((row["state"], row["lease_owner"]), ("in_flight", "run:second"), "다른 실행 소유의 행을 덮지 않는다")

    def test_the_resend_after_401_returns_only_its_own_rows_when_the_lease_is_lost(self):
        res = self.capture("R1")

        def responder(env, h):
            with self.store.transaction() as tx:
                tx.execute("UPDATE leases SET owner='run:second' WHERE name='upload'")
            return err_body(401, "auth_required")

        self.responder = responder
        self.assertEqual(self.upload()["result"], "busy_other_run")
        self.assertEqual(self.row(res.event_id)["state"], "retry_wait")

    def test_refresh_network_failure_is_offline_not_auth(self):
        res = self.capture("R1")
        from services.community_auth_service import CommunityAuthError

        def token(rejected=None):
            if rejected:
                raise CommunityAuthError("auth_unavailable")
            return "tok"

        self.responder = lambda env, h: err_body(401, "auth_required")
        with mock.patch("services.community_auth_service.get_access_token", side_effect=token):
            self.assertEqual(self.upload()["result"], "cooldown")
        self.assertEqual(self.row(res.event_id)["state"], "retry_wait")

    def test_auth_required_rows_resume_after_login_with_the_same_event(self):
        res = self.capture("R1")
        self.responder = lambda env, h: err_body(403, "session_revoked")
        self.assertEqual(self.upload()["result"], "needs_auth")
        row = self.row(res.event_id)
        self.assertEqual(row["state"], "auth_required")
        self.assertIsNone(self.journal(res.event_id)["blocked_reason"], "재로그인하면 다시 보낼 수 있어야 한다")
        self.now = T0 + timedelta(minutes=10)
        self.responder = lambda env, h: ack_body(env["events"])
        self.assertEqual(self.upload("recovery")["result"], "sent")
        self.assertEqual(self.requests[-1][1]["events"][0]["event_id"], res.event_id)

    def test_consent_revoked_blocks_and_another_account_never_sends_old_rows(self):
        a = self.capture("R1")
        self.responder = lambda env, h: err_body(403, "consent_revoked")
        self.assertEqual(self.upload()["result"], "needs_consent")
        self.assertEqual(self.row(a.event_id)["state"], "blocked")
        self.store.set_context(**CTX_B)
        self.responder = lambda env, h: ack_body(env["events"])
        self.assertEqual(self.upload("manual")["result"], "no_pending")
        self.assertEqual(len(self.requests), 1)

    # ── 크기 ─────────────────────────────────────────────────────────────

    def _sizes(self, events):
        """(빈 envelope 바이트, 이벤트별 바이트) — 실제 보내는 직렬화와 같은 계산."""
        conn = self.store.connect()
        base = len(client.envelope_bytes(up._envelope(CTX, "manual", [])))
        sizes = []
        for e in events:
            row = dict(conn.execute("SELECT j.*, o.attempt_count FROM source_journal j JOIN outbox o ON o.event_id=j.event_id"
                                    " WHERE j.event_id=?", (e.event_id,)).fetchone())
            sizes.append(len(client.envelope_bytes(up._event(row, row["payload_json"], CTX))))
        return base, sizes

    def test_batches_are_split_by_the_exact_utf8_size_of_the_whole_envelope(self):
        events = [self.capture(f"R{i}", violation_location="서울특별시 중구 " + "가" * 190) for i in range(6)]  # 한글 3바이트
        base, sizes = self._sizes(events)
        limit = base + 2 * max(sizes) + 1  # 정확히 2건씩 들어가는 한도
        with mock.patch.object(up, "MAX_BODY_BYTES", limit), mock.patch.object(client, "MAX_BODY_BYTES", limit):
            self.assertEqual(self.upload("manual")["result"], "sent")
        self.assertEqual([len(env["events"]) for _, env in self.requests], [2, 2, 2])
        for _, env in self.requests:
            self.assertLessEqual(len(client.envelope_bytes(env)), limit)

    def test_one_oversize_event_is_isolated_and_the_next_one_still_goes(self):
        big = self.capture("R1", violation_location="서울특별시 " + "가" * 190)
        small = self.capture("R2", violation_location="서울")
        base, (big_size, small_size) = self._sizes([big, small])
        limit = base + small_size + 5
        self.assertGreater(base + big_size, limit)
        with mock.patch.object(up, "MAX_BODY_BYTES", limit), mock.patch.object(client, "MAX_BODY_BYTES", limit):
            self.assertEqual(self.upload("manual")["result"], "partial")
        self.assertEqual((self.row(big.event_id)["state"], self.row(big.event_id)["last_error_code"]),
                         ("dead_letter", "payload_too_large"))
        self.assertEqual(self.row(big.event_id)["attempt_count"], 0, "보내지 않은 이벤트는 시도로 세지 않는다")
        self.assertIsNone(self.row(small.event_id))

    def test_413_halves_the_batch_with_the_same_events(self):
        events = [self.capture(f"R{i}") for i in range(4)]
        self.responder = lambda env, h: err_body(413, "payload_too_large") if len(env["events"]) > 1 else ack_body(env["events"])
        self.assertEqual(self.upload("manual")["result"], "sent", "이분 뒤 모두 저장됐으면 문제가 아니다(자정 key 성공)")
        sent_ids = [e["event_id"] for _, env in self.requests if len(env["events"]) == 1 for e in env["events"]]
        self.assertEqual(sorted(sent_ids), sorted(e.event_id for e in events))
        for e in events:
            self.assertIsNone(self.row(e.event_id))

    # ── 요청 공통 오류와 이벤트 오류 구분(Sol 계획 검토 6) ────────────────

    def test_an_ambiguous_422_is_pinned_on_one_event_by_a_control_request(self):
        bad, good = self.capture("R1"), self.capture("R2")

        def responder(env, h):
            if any(e["event_id"] == bad.event_id for e in env["events"]):
                return err_body(422, "schema_invalid")
            return ack_body(env["events"])

        self.responder = responder
        self.assertEqual(self.upload("manual")["result"], "partial")
        self.assertEqual(self.row(bad.event_id)["state"], "dead_letter")
        self.assertIsNone(self.row(good.event_id))

    def test_a_422_for_every_event_is_held_not_dead_lettered(self):
        events = [self.capture(f"R{i}") for i in range(3)]
        self.responder = lambda env, h: err_body(422, "schema_invalid")
        self.assertEqual(self.upload("manual")["result"], "failed")
        for e in events:
            self.assertIn(self.row(e.event_id)["state"], ("retry_wait", "pending"), "공통 문제면 이벤트를 버리지 않는다")
            self.assertNotEqual(self.row(e.event_id)["state"], "dead_letter")

    def test_415_holds_the_batch_and_cools_down(self):
        events = [self.capture(f"R{i}") for i in range(2)]
        self.responder = lambda env, h: (415, {}, b"")
        self.assertEqual(self.upload("manual")["result"], "failed")
        self.assertEqual(len(self.requests), 1)
        for e in events:
            self.assertEqual(self.row(e.event_id)["state"], "retry_wait")
        self.assertEqual(self.control()["last_error_code"], "unsupported_media_type")

    # ── 이전 형식·불변 필드·예산 ─────────────────────────────────────────

    def test_an_old_in_flight_row_without_a_lease_is_recovered(self):
        res = self.capture("R1")
        with self.store.transaction() as tx:  # 예전 코드는 in_flight 에 lease 를 적지 않았다
            tx.execute("UPDATE outbox SET state='in_flight', lease_owner=NULL, lease_until=NULL")
        self.assertEqual(self.upload("recovery")["result"], "sent")
        self.assertIsNone(self.row(res.event_id))

    def test_an_old_ack_without_a_receipt_is_confirmed_again_with_the_same_event(self):
        res = self.capture("R1")
        with self.store.transaction() as tx:  # 예전 코드가 durable 확인 없이 완료로 적은 행
            tx.execute("DELETE FROM outbox")
            tx.execute("UPDATE source_journal SET ack_status='accepted', receipt_id='rcpt-1', acked_at='2026-09-26T00:00:00Z'")
        self.responder = lambda env, h: ack_body(env["events"], status="duplicate")
        self.assertEqual(self.upload("manual")["result"], "sent")
        self.assertEqual(self.requests[0][1]["events"][0]["event_id"], res.event_id)
        # 길이가 36 이어도 UUID 가 아니면 다시 확인받는다
        with self.store.transaction() as tx:
            tx.execute("UPDATE source_journal SET receipt_id=? WHERE event_id=?", ("x" * 36, res.event_id))
        self.requests.clear()
        self.assertEqual(self.upload("manual")["result"], "sent")
        self.assertEqual([env["events"][0]["event_id"] for _, env in self.requests], [res.event_id])
        journal = self.journal(res.event_id)
        self.assertEqual((journal["ack_status"], journal["receipt_id"]), ("duplicate", RECEIPT))

    def test_a_resend_keeps_the_stored_writer_epoch(self):
        res = self.capture("R1")
        self.responder = lambda env, h: err_body(503, "busy")
        self.upload()
        self.store.set_context(**dict(CTX, writer_epoch=2))  # 그 사이 writer 가 바뀌어도
        self.now = T0 + timedelta(minutes=5)
        self.responder = lambda env, h: ack_body(env["events"])
        self.upload("recovery")
        self.assertEqual([env["events"][0]["writer_epoch"] for _, env in self.requests], [1, 1])

    def test_a_run_stopped_by_its_budget_is_more_pending_and_midnight_is_not_succeeded(self):
        for i in range(25):
            self.capture(f"R{i}")
        with mock.patch.object(up, "RUN_MAX_REQUESTS", 1):
            self.assertEqual(sched.run_midnight(now_utc=self.now, data_dir=self.tmp)["result"], "deferred")
        self.assertEqual(len(self.requests), 1)
        row = self.store.connect().execute("SELECT state, deferred_reason FROM schedule_runs").fetchone()
        self.assertEqual((row["state"], row["deferred_reason"]), ("deferred", "more_pending"))

    # ── 기록·초기화 ──────────────────────────────────────────────────────

    def test_idle_runs_are_not_recorded_and_failures_do_not_touch_the_rebuild(self):
        for _ in range(5):
            self.assertEqual(self.upload()["result"], "no_pending")
        self.assertEqual(self.store.connect().execute("SELECT COUNT(*) FROM upload_runs").fetchone()[0], 0)
        with self.store.transaction() as tx:
            tx.execute("INSERT INTO rebuild_jobs(run_id, required_version, local_dataset_id, source_account_namespace,"
                       " state, updated_at) VALUES ('rb1','v','ds','ns','completed','t')")
        self.capture("R1")
        self.responder = lambda env, h: err_body(503, "busy")
        self.upload()
        self.assertEqual(self.store.connect().execute("SELECT state FROM rebuild_jobs").fetchone()[0], "completed")
        self.assertEqual(self.store.connect().execute("SELECT COUNT(*) FROM upload_runs").fetchone()[0], 1)

    def test_status_shows_wait_reason_times_and_central_store(self):
        res = self.capture("R1")
        self.responder = lambda env, h: err_body(429, "rate_limited", header="60")
        self.upload()
        status = up.upload_status(self.tmp)
        self.assertEqual(status["control"]["state"], "cooling_down")
        self.assertEqual(status["control"]["reason"], "rate_limited")
        self.assertEqual(status["next_retry_kst"], "2026-09-27 09:01")
        self.assertIsNotNone(status["oldest_unsent_kst"])
        self.assertIsNone(status["last_central_ack_kst"])
        self.assertEqual(status["last_result"], "deferred", "API 소비자 호환 값")
        self.assertEqual(status["last_outcome"], "cooldown")
        self.now = T0 + timedelta(minutes=2)
        self.responder = lambda env, h: (200, {}, json.dumps({"protocol": 1, "request_id": "r", "results": [
            {"event_id": res.event_id, "status": "quarantined", "durable": True, "receipt_id": RECEIPT,
             "projection_status": "not_applicable"}]}).encode())
        self.upload("recovery")
        status = up.upload_status(self.tmp)
        self.assertEqual((status["quarantined"], status["stored"]), (1, 1))
        self.assertEqual(status["last_central_ack_kst"], "2026-09-27 09:02")


if __name__ == "__main__":
    unittest.main()
