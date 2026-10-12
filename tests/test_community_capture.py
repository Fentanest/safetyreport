"""capture 테스트 — S-03/S-04/S-06, A01~A04/A09, crash 3지점, inactive context."""
import hashlib
import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

from sqlalchemy import create_engine

from services import community_capture as cap
from services.community_store import CommunityStore

CTX = {"contributor_fingerprint": "f" * 32, "connection_id": "11111111-2222-4333-8444-555555555555",
       "writer_epoch": 1, "dataset_key": "d" * 16, "consent_grant_id": "22222222-3333-4444-8444-666666666666",
       "policy_version": "2026-09-26.1", "consent_text_sha256": "h" * 64,
       "source_app": "safetyreport", "source_mode": "server"}


def eligible_input(status="수용", amount="과태료: 40,000원"):
    return {"processing_status": status, "penalty_amount": amount, "report_date": "2026-09-01",
            "response_date": "2026-09-10", "processing_agency": "서울특별시 중구청",
            "person_in_charge": "홍길동", "car_number": "12가3456",
            "violation_location": "서울특별시 중구 세종대로 110", "entry_value": "불법주정차신고",
            "penalty_points": "", "geocode": {"status": "ok", "lat": 37.5662952, "lng": 126.9779451}}


def processing_input():
    data = eligible_input()
    data["processing_status"] = "처리중"
    data["penalty_amount"] = ""
    data["response_date"] = ""
    return data


def withdrawn_input():
    data = eligible_input()
    data["processing_status"] = "취하"
    data["penalty_amount"] = ""
    data["response_date"] = "2026-09-11"
    return data


class CaptureTest(unittest.TestCase):
    def test_report_number_backfill_keeps_observation_hash(self):
        first = cap.capture(dict(eligible_input()), source_report_id="R1", trigger="realtime", data_dir=self.tmp)
        numbered = dict(eligible_input(), report_number="SPP-2609-8000001")
        second = cap.capture(numbered, source_report_id="R1", trigger="realtime", data_dir=self.tmp)
        self.assertEqual(first.payload_sha256, second.payload_sha256)
        self.assertEqual(second.event_type, "completed_observation")
        self.assertEqual(self.store.connect().execute("SELECT report_number FROM source_journal WHERE event_id=?", (second.event_id,)).fetchone()[0], "SPP-2609-8000001")

    def setUp(self):
        # This suite tests the journal/uploader with synthetic writer contexts.
        # Owner/consent authorization is covered by test_community_offline.
        context_patch = mock.patch('services.community_cloud.capture_context',
                                   side_effect=lambda ctx: (ctx, 'active' if ctx and ctx.get('state') == 'active' else 'none'))
        context_patch.start(); self.addCleanup(context_patch.stop)
        cooldown_patch = mock.patch('services.community_cloud.remaining', return_value=0)
        cooldown_patch.start(); self.addCleanup(cooldown_patch.stop)
        self.tmp = tempfile.mkdtemp()
        self.store = CommunityStore.open(self.tmp)
        self.store.set_context(**CTX)

    def tearDown(self):
        self.store.close()
        CommunityStore._forget(os.path.join(self.tmp, "community.db"))

    def _counts(self):
        conn = self.store.connect()
        journal = conn.execute("SELECT COUNT(*) v FROM source_journal").fetchone()["v"]
        outbox = conn.execute("SELECT COUNT(*) v FROM outbox").fetchone()["v"]
        latest = conn.execute("SELECT COUNT(*) v FROM report_latest").fetchone()["v"]
        detail = conn.execute("SELECT COUNT(*) v FROM detail_status").fetchone()["v"]
        return journal, outbox, latest, detail

    def test_first_eligible_creates_event(self):
        result = cap.capture(dict(eligible_input()), source_report_id="R1", trigger="realtime",
                             data_dir=self.tmp)
        self.assertEqual(result.event_type, "completed_observation")
        self.assertTrue(result.event_id)
        self.assertEqual(self._counts(), (1, 1, 1, 0))
        # progress_status 가 없으면 detail_status 를 쓰지 않는다
        result2 = cap.capture(dict(eligible_input()), source_report_id="R1", trigger="realtime",
                              data_dir=self.tmp)
        self.assertIsNone(result2.event_id)
        self.assertEqual(self._counts(), (1, 1, 1, 0))

    def test_later_numeric_rating_changes_hash_and_emits_event_without_reason(self):
        first = cap.capture(dict(eligible_input()), source_report_id="R1", trigger="realtime", data_dir=self.tmp)
        rated = dict(eligible_input(), rating=4, rating_reason="비공개 사유")
        second = cap.capture(rated, source_report_id="R1", trigger="realtime", data_dir=self.tmp)
        self.assertEqual(second.event_type, "completed_observation")
        self.assertNotEqual(first.payload_sha256, second.payload_sha256)
        self.assertIsNone(cap.capture(rated, source_report_id="R1", trigger="realtime", data_dir=self.tmp).event_id)
        payload = cap.build_payload(rated)
        self.assertEqual(payload["rating"], 4)
        self.assertNotIn("reason", cap.canonical_json(payload))
        self.assertIsNone(cap.build_payload(dict(rated, rating=6))["rating"])

    def test_changed_official_coordinates_emit_new_completed_observation(self):
        first = eligible_input()
        first["geocode"] = {"status": "ok", "lat": 37.560123456789, "lng": 126.830123456789}
        cap.capture(first, source_report_id="R1", trigger="realtime", data_dir=self.tmp)
        updated = dict(first)
        updated["violation_location"] = "서울특별시 중구 세종대로 111"
        updated["geocode"] = {"status": "ok", "lat": 37.56123456789, "lng": 126.83123456789}
        result = cap.capture(updated, source_report_id="R1", trigger="realtime", data_dir=self.tmp)
        self.assertEqual(result.event_type, "completed_observation")
        rows = self.store.connect().execute(
            "SELECT event_type, payload_json FROM source_journal ORDER BY source_revision DESC"
        ).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual(json.loads(rows[0]["payload_json"])["location"],
                         {"lat": "37.56123456789", "lng": "126.83123456789", "source": "geocode"})

    def test_detail_status_recorded_with_progress(self):
        adapter = dict(eligible_input())
        adapter["progress_status"] = "답변완료"
        cap.capture(adapter, source_report_id="R1", trigger="realtime", data_dir=self.tmp)
        row = self.store.connect().execute(
            "SELECT c_now_label FROM detail_status").fetchone()
        self.assertEqual(row["c_now_label"], "답변완료")

    def test_s06_first_processing_writes_only_detail_status(self):
        adapter = dict(processing_input())
        adapter["progress_status"] = "진행"
        result = cap.capture(adapter, source_report_id="R9", trigger="realtime", data_dir=self.tmp)
        self.assertIsNone(result.event_id)
        self.assertFalse(result.eligible)
        self.assertEqual(self._counts(), (0, 0, 0, 1))
        # 같은 비적격 반복도 이벤트 없음
        result2 = cap.capture(dict(adapter), source_report_id="R9", trigger="realtime", data_dir=self.tmp)
        self.assertIsNone(result2.event_id)
        self.assertEqual(self._counts(), (0, 0, 0, 1))

    def test_s04_server_completed_does_not_create_correction(self):
        # 2026-09-28: server_completed 적중해도 첫 비적격 관측은 이벤트 없음(정정 발급 중단).
        prefix = hashlib.sha256(b"safetyreport|R7").hexdigest()[:24]
        with self.store.transaction() as tx:
            tx.execute("INSERT INTO server_completed(dataset_key, key_prefix, fetched_at) VALUES (?, ?, ?)",
                       (CTX["dataset_key"], prefix, "2026-09-26T00:00:00.000Z"))
        adapter = dict(withdrawn_input())
        adapter["progress_status"] = "취하"
        result = cap.capture(adapter, source_report_id="R7", trigger="realtime", data_dir=self.tmp)
        self.assertIsNone(result.event_id)
        self.assertFalse(result.eligible)
        self.assertEqual(self._counts(), (0, 0, 0, 1))

    def test_withdrawn_after_shared_creates_no_event(self):
        cap.capture(dict(eligible_input()), source_report_id="R1", trigger="realtime", data_dir=self.tmp)
        result = cap.capture(dict(withdrawn_input()), source_report_id="R1", trigger="realtime",
                             data_dir=self.tmp)
        self.assertIsNone(result.event_id)
        # 같은 비적격 반복도 이벤트 없음. 중앙은 마지막 답변 상태를 유지한다.
        again = cap.capture(dict(withdrawn_input()), source_report_id="R1", trigger="realtime",
                            data_dir=self.tmp)
        self.assertIsNone(again.event_id)
        self.assertEqual(self._counts(), (1, 1, 1, 0))

    def test_eligible_again_after_not_eligible(self):
        cap.capture(dict(eligible_input()), source_report_id="R1", trigger="realtime", data_dir=self.tmp)
        cap.capture(dict(withdrawn_input()), source_report_id="R1", trigger="realtime", data_dir=self.tmp)
        # 비적격 관측은 journal 에 남지 않으므로 같은 답변의 재관측은 무변경이다.
        result = cap.capture(dict(eligible_input()), source_report_id="R1", trigger="realtime",
                             data_dir=self.tmp)
        self.assertIsNone(result.event_id)
        edited = dict(eligible_input())
        edited["processing_agency"] = "부산광역시 해운대구청"
        changed = cap.capture(edited, source_report_id="R1", trigger="realtime", data_dir=self.tmp)
        self.assertEqual(changed.event_type, "completed_observation")

    def test_inactive_context_writes_journal_without_outbox(self):
        self.store.deactivate_context("consent_revoked")
        result = cap.capture(dict(eligible_input()), source_report_id="R1", trigger="realtime",
                             data_dir=self.tmp)
        self.assertIsNotNone(result.event_id)
        self.assertEqual(self._counts(), (1, 0, 1, 0))
        row = self.store.connect().execute("SELECT blocked_reason FROM source_journal").fetchone()
        self.assertEqual(row["blocked_reason"], "consent:none")

    def test_mark_personal_save(self):
        result = cap.capture(dict(eligible_input()), source_report_id="R1", trigger="realtime",
                             data_dir=self.tmp)
        states_at_wake = []
        def read_state():
            row = self.store.connect().execute(
                "SELECT personal_save_state FROM source_journal WHERE event_id=?", (result.event_id,)).fetchone()
            states_at_wake.append(row["personal_save_state"])
        with mock.patch("services.community_uploader.wake", side_effect=read_state) as wake:
            cap.mark_personal_save(result.event_id, True, data_dir=self.tmp)
            cap.mark_personal_save(result.event_id, False, data_dir=self.tmp)
            cap.mark_personal_save(None, True, data_dir=self.tmp)  # no-op
        self.assertEqual(states_at_wake, ["saved"])
        self.assertEqual(wake.call_count, 1)
        row = self.store.connect().execute(
            "SELECT personal_save_state FROM source_journal WHERE event_id=?", (result.event_id,)).fetchone()
        self.assertEqual(row["personal_save_state"], "failed")

    def test_a01_personal_edit_does_not_change_payload(self):
        """A01: enqueue 뒤 개인 DB 를 B 로 편집해도 전송 payload 는 A."""
        result = cap.capture(dict(eligible_input()), source_report_id="R1", trigger="realtime",
                             data_dir=self.tmp)
        row = self.store.connect().execute(
            "SELECT payload_json FROM source_journal WHERE event_id=?", (result.event_id,)).fetchone()
        payload_a = json.loads(row["payload_json"])
        self.assertEqual(payload_a["agency_name"], "서울특별시 중구청")
        # 개인 DB 편집을 흉내: 같은 신고를 다른 값으로 capture 하면 새 이벤트(내용 변화)
        edited = dict(eligible_input())
        edited["processing_agency"] = "부산광역시 해운대구청"
        result2 = cap.capture(edited, source_report_id="R1", trigger="realtime", data_dir=self.tmp)
        self.assertEqual(result2.event_type, "completed_observation")
        # outbox 의 첫 행 payload 는 여전히 A
        first = self.store.connect().execute(
            "SELECT payload_json FROM source_journal ORDER BY source_revision LIMIT 1").fetchone()
        self.assertEqual(json.loads(first["payload_json"])["agency_name"], "서울특별시 중구청")

    def test_a02_retry_after_failure_resends_a(self):
        """A02: 전송 실패 뒤 개인 편집이 있어도 재전송은 저장된 A."""
        result = cap.capture(dict(eligible_input()), source_report_id="R1", trigger="realtime",
                             data_dir=self.tmp)
        outbox = self.store.connect().execute(
            "SELECT j.payload_json FROM outbox o JOIN source_journal j ON j.event_id=o.event_id").fetchone()
        self.assertIn("서울특별시 중구청", outbox["payload_json"])
        self.assertEqual(result.payload_sha256, cap.payload_sha256(cap.build_payload(eligible_input())))

    def test_a03_dict_mutation_does_not_change_stored(self):
        adapter = dict(eligible_input())
        result = cap.capture(adapter, source_report_id="R1", trigger="realtime", data_dir=self.tmp)
        adapter["processing_status"] = "취하"
        adapter["geocode"] = {"status": "pending", "lat": None, "lng": None}
        row = self.store.connect().execute(
            "SELECT payload_json FROM source_journal WHERE event_id=?", (result.event_id,)).fetchone()
        self.assertEqual(json.loads(row["payload_json"])["status"], "accepted")

    def test_a04_override_and_backups_create_no_event(self):
        """A04: override 저장·백업 복원·exchange 변환은 capture 를 부르지 않으므로 journal 0."""
        self.assertEqual(self._counts(), (0, 0, 0, 0))

    def test_a09_override_geo_ignored(self):
        """A09: build_adapter_input 은 공식 geo 만 받는다 — override 좌표 필드가 있어도 무시."""
        adapter = cap.build_adapter_input(
            {"처리상태": "수용", "범칙금_과태료": "과태료: 40,000원", "답변일": "2026-09-10",
             "처리기관": "A", "담당자": "B", "차량번호": "1", "위반장소": "서울"},
            {"신고일": "2026-09-01"}, "불법주정차신고",
            {"위도": 37.5, "경도": 127.0, "지오코딩상태": "ok"}, "답변완료")
        self.assertEqual(adapter["geocode"], {"status": "ok", "lat": 37.5, "lng": 127.0})

    def test_crash_converges_to_single_event(self):
        """crash 3지점: journal commit 뒤 어디서 끊겨도 같은 공식 응답 재수집은 이벤트 1개로 수렴."""
        adapter = dict(eligible_input())
        adapter["progress_status"] = "답변완료"
        first = cap.capture(adapter, source_report_id="R1", trigger="realtime", data_dir=self.tmp)
        # 지점 1: journal commit 직후 personal_save 갱신 전 crash → 재수집
        second = cap.capture(dict(adapter), source_report_id="R1", trigger="realtime", data_dir=self.tmp)
        self.assertIsNone(second.event_id)
        # 지점 2: 개인 tx 중 crash 로 report_latest 만 남아도 마찬가지
        third = cap.capture(dict(adapter), source_report_id="R1", trigger="realtime", data_dir=self.tmp)
        self.assertIsNone(third.event_id)
        journal = self.store.connect().execute("SELECT COUNT(*) v FROM source_journal").fetchone()["v"]
        self.assertEqual(journal, 1)
        self.assertIsNotNone(first.event_id)

    def test_retry_file_and_consecutive_failures(self):
        """S-03: capture 실패 시 개인 저장 0·재시도 파일 기록·연속 3회 중단 신호."""
        from contextlib import contextmanager
        from core.storage import reports_repo as repo
        from core.storage.reports_repo import CrawledDetail
        cap.add_retry_id("RX", "capture_started", data_dir=self.tmp)
        self.assertIn("RX", cap.capture_retry_ids(data_dir=self.tmp))
        cap.remove_retry_id("RX", data_dir=self.tmp)
        self.assertNotIn("RX", cap.capture_retry_ids(data_dir=self.tmp))

        began = []

        class FakeEngine:
            from contextlib import contextmanager as _cm

            @_cm
            def connect(self):
                yield object()

            def begin(self):
                began.append(1)
                raise AssertionError("personal save must not run when capture fails")

        recs = [CrawledDetail(id=f"F{i}", category="other", detail={"ID": f"F{i}"},
                              title_fields={"신고일": "2026-09-01"}) for i in range(3)]
        with mock.patch.object(repo, "_find_row", return_value=None), \
                mock.patch.object(repo, "_prefetch_derived", return_value={}), \
                mock.patch("services.community_capture.capture", side_effect=RuntimeError("db locked")), \
                mock.patch.object(repo.logger.LoggerFactory, "logbot", create=True):
            with self.assertRaises(cap.CaptureStoreUnavailable):
                repo.save_crawled(FakeEngine(), recs, refresh_duplicates=False)
        self.assertEqual(began, [])  # 개인 저장 트랜잭션 0회
        import settings.settings as _app_settings
        # reports_repo 경로는 프로세스 데이터 dir(settings.datapath)을 쓴다
        retry_ids = cap.capture_retry_ids(data_dir=_app_settings.datapath)
        self.assertTrue({"F0", "F1", "F2"} <= retry_ids)  # 재시도 파일에 ID 기록
        for rid in ("F0", "F1", "F2"):
            cap.remove_retry_id(rid, data_dir=_app_settings.datapath)

    def test_manifest_partial_failure_keeps_old(self):
        """manifest 부분 실패 시 교체 0 — refresh_server_completed False 면 server_completed 불변."""
        from services import community_uploader as up
        with mock.patch("services.community_ingest_client.post_manifest", return_value=(False, {})):
            self.assertFalse(up.refresh_server_completed(data_dir=self.tmp))
        count = self.store.connect().execute("SELECT COUNT(*) v FROM server_completed").fetchone()["v"]
        self.assertEqual(count, 0)

    def test_on_contributions_deleted(self):
        cap.capture(dict(eligible_input()), source_report_id="R1", trigger="realtime", data_dir=self.tmp)
        cap.on_contributions_deleted(data_dir=self.tmp)
        conn = self.store.connect()
        outbox = conn.execute("SELECT state FROM outbox").fetchall()
        self.assertTrue(outbox)
        for row in outbox:
            self.assertEqual(row["state"], "blocked")
        journal = conn.execute("SELECT blocked_reason FROM source_journal").fetchone()
        self.assertEqual(journal["blocked_reason"], "deleted_by_user")
        self.assertEqual(conn.execute("SELECT COUNT(*) v FROM server_completed").fetchone()["v"], 0)



class ViolationLawPayloadTests(unittest.TestCase):
    """observation-v2(2026-09-28): 파서 결과 열 위반법규만 payload 로(처리내용 원문은 보내지 않는다)."""

    def test_adapter_and_payload_carry_violation_law(self):
        from services import community_capture as cc

        detail = {"처리상태": "수용", "범칙금_과태료": "범칙금: 60,000원", "위반법규": " 도로교통법  제5조 ",
                  "처리내용": "도로교통법 제5조 위반으로 범칙금을 부과하였습니다."}
        inp = cc.build_adapter_input(detail, {"신고일": "2026-09-01"}, "자동차·교통위반 > 신호위반", {})
        payload = cc.build_payload(inp)
        self.assertEqual(payload["violation_law"], "도로교통법 제5조")
        self.assertNotIn("처리내용", str(payload))
        self.assertEqual(cc.build_payload(cc.build_adapter_input({"처리상태": "수용"}, {}, "", {}))["violation_law"], None)


CTX_B = {"contributor_fingerprint": "b" * 32, "connection_id": "99999999-2222-4333-8444-555555555555",
         "writer_epoch": 1, "dataset_key": "e" * 16, "consent_grant_id": "aaaaaaaa-3333-4444-8444-666666666666",
         "policy_version": "2026-09-26.1", "consent_text_sha256": "h" * 64,
         "source_app": "safetyreport", "source_mode": "server"}


class AgencyCodePayloadTests(unittest.TestCase):
    """observation-v3(2026-09-28): 선택 답변의 C_MANAGE_ORG 원문을 TEXT 그대로 payload 로."""

    def setUp(self):
        p = mock.patch('services.community_cloud.capture_context', side_effect=lambda ctx: (ctx, 'active'))
        p.start(); self.addCleanup(p.stop)
        self.tmp = tempfile.mkdtemp()
        self.store = CommunityStore.open(self.tmp)
        self.store.set_context(**CTX)

    def tearDown(self):
        self.store.close()
        CommunityStore._forget(os.path.join(self.tmp, "community.db"))

    def test_adapter_and_payload_carry_agency_code_verbatim(self):
        from services import community_capture as cc

        detail = {"처리상태": "수용", "처리기관": "서울특별시 중구청", "처리기관코드": "B410002"}
        inp = cc.build_adapter_input(detail, {"신고일": "2026-09-01"}, "불법주정차신고", {})
        self.assertEqual(inp["agency_code"], "B410002")
        self.assertEqual(cc.build_payload(inp)["source_agency_code"], "B410002")
        # 선행 0·영문 보존, 신규 형식도 자르지 않음, 없으면 null(명시적 NULL)
        self.assertEqual(cc.build_payload(dict(inp, agency_code="0123456"))["source_agency_code"], "0123456")
        self.assertEqual(cc.build_payload(dict(inp, agency_code="X-12"))["source_agency_code"], "X-12")
        self.assertIsNone(cc.build_payload(dict(inp, agency_code=None))["source_agency_code"])
        self.assertIsNone(cc.build_payload(cc.build_adapter_input({"처리상태": "수용"}, {}, "", {}))["source_agency_code"])
        # REVIEW3 낮음-1: 32자를 넘는 신규 형식은 잘라서 보내지 않는다. 전송 payload
        # 에는 싣지 않지만(None), capture() 가 journal/outbox 에 명시적 사유로 기록한다.
        self.assertIsNone(cc.build_payload(dict(inp, agency_code="N" * 33))["source_agency_code"])
        self.assertEqual(cc.build_payload(dict(inp, agency_code="N" * 32))["source_agency_code"], "N" * 32)
        self.assertTrue(cc.agency_code_too_long("N" * 33))
        self.assertFalse(cc.agency_code_too_long("N" * 32))
        self.assertFalse(cc.agency_code_too_long(None))

    def test_overlong_agency_code_is_blocked_with_explicit_reason(self):
        # REVIEW3 낮음-1: 길이 초과 기관코드를 조용히 NULL 로 버리지 않는다.
        # journal/outbox 에 명시적 사유로 남고 전송 후보에서 제외된다.
        res = cap.capture(dict(eligible_input(), agency_code="N" * 33),
                          source_report_id="LONG1", trigger="realtime", data_dir=self.tmp)
        self.assertEqual(res.event_type, "completed_observation")
        conn = self.store.connect()
        journal = conn.execute(
            "SELECT blocked_reason, payload_json FROM source_journal WHERE event_id=?",
            (res.event_id,)).fetchone()
        self.assertEqual(journal["blocked_reason"], "blocked:source_agency_code_too_long")
        self.assertNotIn("N" * 33, journal["payload_json"])
        outbox = conn.execute(
            "SELECT state, last_error_code FROM outbox WHERE event_id=?", (res.event_id,)).fetchone()
        self.assertEqual(outbox["state"], "blocked")
        self.assertEqual(outbox["last_error_code"], "source_agency_code_too_long")
        # 같은 관측 반복 수집은 조용히 유지된다(저널 폭증 없음).
        again = cap.capture(dict(eligible_input(), agency_code="N" * 33),
                            source_report_id="LONG1", trigger="realtime", data_dir=self.tmp)
        self.assertIsNone(again.event_type)
        # 32자 이내는 정상 pending 전송 후보가 된다.
        ok = cap.capture(dict(eligible_input(), agency_code="N" * 32),
                         source_report_id="LONG2", trigger="realtime", data_dir=self.tmp)
        conn2 = self.store.connect()
        self.assertIsNone(conn2.execute(
            "SELECT blocked_reason FROM source_journal WHERE event_id=?", (ok.event_id,)).fetchone()["blocked_reason"])
        self.assertEqual(conn2.execute(
            "SELECT state FROM outbox WHERE event_id=?", (ok.event_id,)).fetchone()["state"], "pending")

    def test_same_payload_long_code_is_recorded_explicitly(self):
        # REVIEW4 낮음: 원문 코드만 33자가 된 관측은 전송 payload(코드 None)가
        # 직전과 같아도 조용히 버리지 않고 blocked 이벤트로 명시 기록한다.
        # 원문은 크롤 DB에 보존되고 journal/outbox에 사유가 남는다(서버·모바일 1:1).
        first = cap.capture(dict(eligible_input(), agency_code=None),
                            source_report_id="LONG-SAME", trigger="realtime", data_dir=self.tmp)
        self.assertEqual(first.event_type, "completed_observation")
        blocked = cap.capture(dict(eligible_input(), agency_code="N" * 33),
                              source_report_id="LONG-SAME", trigger="realtime", data_dir=self.tmp)
        self.assertEqual(blocked.event_type, "completed_observation")
        conn = self.store.connect()
        journal = conn.execute(
            "SELECT blocked_reason, payload_json FROM source_journal WHERE event_id=?",
            (blocked.event_id,)).fetchone()
        self.assertEqual(journal["blocked_reason"], "blocked:source_agency_code_too_long")
        self.assertNotIn("N" * 33, journal["payload_json"])
        outbox = conn.execute(
            "SELECT state, last_error_code FROM outbox WHERE event_id=?", (blocked.event_id,)).fetchone()
        self.assertEqual(outbox["state"], "blocked")
        self.assertEqual(outbox["last_error_code"], "source_agency_code_too_long")
        # 같은 장문 코드 반복은 조용히 유지(저널 폭증 없음 — 이미 명시 기록됨).
        again = cap.capture(dict(eligible_input(), agency_code="N" * 33),
                            source_report_id="LONG-SAME", trigger="realtime", data_dir=self.tmp)
        self.assertIsNone(again.event_type)
        fixed = cap.capture(dict(eligible_input(), agency_code=None),
                            source_report_id="LONG-SAME", trigger="realtime", data_dir=self.tmp)
        self.assertEqual(fixed.event_type, "completed_observation")
        self.assertEqual(fixed.payload_sha256, blocked.payload_sha256)
        self.assertIsNone(conn.execute("SELECT blocked_reason FROM source_journal WHERE event_id=?",
                                       (fixed.event_id,)).fetchone()[0])

    def test_same_report_two_accounts_create_separate_events(self):
        # A가 올린 동일 신고를 B도 제출: A 큐·연결을 건드리지 않고 B의 이벤트를 만든다(전역 중복 제거는 서버 몫).
        first = cap.capture(dict(eligible_input()), source_report_id="R1", trigger="realtime", data_dir=self.tmp)
        self.assertEqual(first.event_type, "completed_observation")
        self.store.set_context(**CTX_B)
        second = cap.capture(dict(eligible_input()), source_report_id="R1", trigger="realtime", data_dir=self.tmp)
        self.assertEqual(second.event_type, "completed_observation")
        self.assertNotEqual(first.event_id, second.event_id)
        rows = self.store.connect().execute(
            "SELECT contributor_fingerprint, connection_id FROM source_journal ORDER BY source_revision").fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["contributor_fingerprint"], CTX["contributor_fingerprint"])
        self.assertEqual(rows[1]["contributor_fingerprint"], CTX_B["contributor_fingerprint"])
        # A의 전송 완료가 B의 제출을 차단하지 않는다: A 행만 ACK 처리해도 B 행은 미ACK·대기로 남는다.
        conn = self.store.connect()
        conn.execute("UPDATE source_journal SET ack_status='accepted' WHERE event_id=?", (first.event_id,))
        conn.execute("DELETE FROM outbox WHERE event_id=?", (first.event_id,))
        conn.commit()
        b_row = conn.execute("SELECT ack_status FROM source_journal WHERE event_id=?", (second.event_id,)).fetchone()
        self.assertIsNone(b_row["ack_status"])
        b_out = conn.execute("SELECT state FROM outbox WHERE event_id=?", (second.event_id,)).fetchone()
        self.assertEqual(b_out["state"], "pending")


def _parking_adapter(**overrides):
    """payload 가 fine_estimate 대상(불법주정차·확정 금액 모름)이 되는 기본 adapter_input."""
    base = {
        "processing_status": "수용", "penalty_amount": "과태료",
        "report_date": "2026-09-01", "response_date": "2026-09-10",
        "processing_agency": "서울특별시 중구청", "person_in_charge": "홍길동",
        "car_number": "12가3456", "violation_location": "서울특별시 중구 세종대로 110",
        "entry_value": "불법주정차신고",
        "geocode": {"status": "ok", "lat": 37.5662952, "lng": 126.9779451},
    }
    base.update(overrides)
    return base


class FineEstimateSharedJudgmentTests(unittest.TestCase):
    """fine_estimate.estimate_if_unknown — build_payload 가 쓰는 판정 그대로를 공유 벡터로 검증한다
    (tests/fixtures/fine_estimate_vectors.json, services/stats/metrics.py `_metric_unknown_fine` 와 같은 조건)."""

    @classmethod
    def setUpClass(cls):
        from services import fine_estimate
        cls.fine_estimate = fine_estimate
        path = Path(__file__).resolve().parent / "fixtures" / "fine_estimate_vectors.json"
        cls.cases = json.loads(path.read_text(encoding="utf-8"))["cases"]

    def test_amount_unknown_cases_match_vector_amount_and_rule(self):
        """벡터의 음성 case(금액이 표에 없어 amount 가 null)는 payload 에도 fine_estimate 가 없다 — estimate_if_unknown 이 None."""
        for case in self.cases:
            with self.subTest(case=case["name"]):
                result = self.fine_estimate.estimate_if_unknown("과태료", case["record"])
                if case["amount"] is None:
                    self.assertIsNone(result, f"{case['name']}: 기대와 달리 금액이 나옴")
                else:
                    self.assertIsNotNone(result, f"{case['name']}: estimate_if_unknown 이 None")
                    self.assertEqual(result["amount"], case["amount"])
                    self.assertEqual(result["rule"], case["rule"])

    def test_confirmed_or_non_fine_amount_suppresses_estimate(self):
        record = dict(self.cases[0]["record"])
        self.assertIsNone(self.fine_estimate.estimate_if_unknown("과태료: 40,000원", record))
        self.assertIsNone(self.fine_estimate.estimate_if_unknown("범칙금: 40,000원", record))
        self.assertIsNone(self.fine_estimate.estimate_if_unknown("경고", record))
        self.assertIsNone(self.fine_estimate.estimate_if_unknown("", record))
        self.assertIsNone(self.fine_estimate.estimate_if_unknown(None, record))


class FineEstimatePayloadTests(unittest.TestCase):
    """build_payload 의 fine_estimate(v6, fine-estimate-contract.md): 있을 때만, 대상 아니면 payload·sha256 불변."""

    def test_fine_estimate_added_when_eligible(self):
        from services import fine_estimate
        payload = cap.build_payload(_parking_adapter())
        self.assertIn("fine_estimate", payload)
        self.assertEqual(payload["fine_estimate"],
                         {"won": 40000, "rule": "parking", "rule_version": fine_estimate.RULE_VERSION})

    def test_fine_estimate_absent_when_amount_confirmed(self):
        payload = cap.build_payload(_parking_adapter(penalty_amount="과태료: 40,000원"))
        self.assertNotIn("fine_estimate", payload)

    def test_fine_estimate_absent_when_not_a_fine_disposition(self):
        payload = cap.build_payload(_parking_adapter(penalty_amount="경고"))
        self.assertNotIn("fine_estimate", payload)

    def test_fine_estimate_absent_when_rule_does_not_match(self):
        # '생활불편신고' 는 과태료 추정 규칙에 없는 접수 메뉴 — classify() 가 None.
        payload = cap.build_payload(_parking_adapter(entry_value="생활불편신고"))
        self.assertNotIn("fine_estimate", payload)

    def test_payload_sha256_unchanged_for_non_target_report(self):
        """추정 대상이 아닌 신고는 신고명·사진 촬영 시각 입력이 새로 생겨도 payload·payload_sha256 이 바뀌지 않는다."""
        confirmed = _parking_adapter(penalty_amount="과태료: 40,000원")
        before = cap.build_payload(confirmed)
        after = cap.build_payload(dict(confirmed, report_name="불법주정차", occurrence_time="10:00",
                                       photo_first_at="2026-09-01 01:00:00", photo_last_at="2026-09-01 02:30:00"))
        self.assertNotIn("fine_estimate", after)
        self.assertEqual(before, after)
        self.assertEqual(cap.payload_sha256(before), cap.payload_sha256(after))

    def test_build_adapter_input_carries_fine_estimate_inputs(self):
        """fine_estimate 입력은 build_adapter_input 을 거쳐도 payload 로는 나가지 않는다(판정에만 쓰임)."""
        detail = {"처리상태": "수용", "범칙금_과태료": "과태료", "차량번호": "12가3456", "발생시각": "10:00"}
        adapter = cap.build_adapter_input(detail, {"신고일": "2026-09-01", "신고번호": "SPP-2609-8000001"},
                                          "불법주정차신고", {}, None,
                                          report_name="불법주정차 위반",
                                          photo_first_at="2026-09-01 01:00:00", photo_last_at="2026-09-01 02:30:00")
        self.assertEqual(adapter["report_name"], "불법주정차 위반")
        self.assertEqual(adapter["occurrence_time"], "10:00")
        self.assertEqual(adapter["photo_first_at"], "2026-09-01 01:00:00")
        self.assertEqual(adapter["photo_last_at"], "2026-09-01 02:30:00")
        payload = cap.build_payload(adapter)
        self.assertIn("fine_estimate", payload)
        for leaked in ("report_name", "occurrence_time", "photo_first_at", "photo_last_at", "신고명", "사진_첫촬영", "사진_끝촬영", "발생시각"):
            self.assertNotIn(leaked, payload)

    def test_report_name_falls_back_to_title_fields(self):
        """호출자가 report_name 을 안 넘기면 title_fields['신고명'] 으로 떨어진다(크롤 경로의 기본 동작)."""
        detail = {"처리상태": "수용", "범칙금_과태료": "과태료", "차량번호": "12가3456"}
        adapter = cap.build_adapter_input(detail, {"신고일": "2026-09-01", "신고명": "버스전용차로 위반"},
                                          "자동차·교통위반 > 신호위반", {})
        self.assertEqual(adapter["report_name"], "버스전용차로 위반")


def _validate_schema(instance, schema):
    """observation.schema.json 의 부분집합(type/enum/pattern/min·maxLength/minimum/maximum/
    properties/required/additionalProperties)만 지원하는 가벼운 검증기 — jsonschema 의존성 없이 계약 파일 그대로를 검사한다."""
    if "enum" in schema:
        assert instance in schema["enum"], f"{instance!r} not in {schema['enum']!r}"
        return
    types = schema.get("type")
    if types is not None:
        allowed = [types] if isinstance(types, str) else types
        pytypes = {"string": str, "integer": int, "number": (int, float), "object": dict, "null": type(None)}
        ok = any(isinstance(instance, pytypes[t]) and not (t == "integer" and isinstance(instance, bool)) for t in allowed)
        assert ok, f"{instance!r} not of type {allowed!r}"
    if instance is None:
        return
    if isinstance(instance, str):
        if "minLength" in schema:
            assert len(instance) >= schema["minLength"]
        if "maxLength" in schema:
            assert len(instance) <= schema["maxLength"]
        if "pattern" in schema:
            import re as _re
            assert _re.match(schema["pattern"], instance), f"{instance!r} !~ {schema['pattern']!r}"
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema:
            assert instance >= schema["minimum"]
        if "maximum" in schema:
            assert instance <= schema["maximum"]
    if isinstance(instance, dict) and schema.get("type") == "object":
        for key in schema.get("required", []):
            assert key in instance, f"missing required key {key!r}"
        if schema.get("additionalProperties") is False:
            extra = set(instance) - set(schema.get("properties", {}))
            assert not extra, f"additionalProperties: {extra!r}"
        for key, sub in schema.get("properties", {}).items():
            if key in instance:
                _validate_schema(instance[key], sub)


class FineEstimateSchemaValidationTests(unittest.TestCase):
    """build_payload 출력이 contracts/community-ingest/observation.schema.json 과 맞는지(fine_estimate 포함)."""

    @classmethod
    def setUpClass(cls):
        schema_path = Path(__file__).resolve().parents[1] / "contracts" / "community-ingest" / "observation.schema.json"
        cls.schema = json.loads(schema_path.read_text(encoding="utf-8"))

    def test_schema_declares_fine_estimate_as_optional(self):
        self.assertIn("fine_estimate", self.schema["properties"])
        self.assertNotIn("fine_estimate", self.schema["required"])

    def test_payload_with_fine_estimate_validates(self):
        payload = cap.build_payload(_parking_adapter())
        self.assertIn("fine_estimate", payload)
        _validate_schema(payload, self.schema)

    def test_payload_without_fine_estimate_still_validates(self):
        payload = cap.build_payload(_parking_adapter(penalty_amount="과태료: 40,000원"))
        self.assertNotIn("fine_estimate", payload)
        _validate_schema(payload, self.schema)

    def test_vector_cases_validate_as_fine_estimate_value(self):
        from services import fine_estimate
        path = Path(__file__).resolve().parent / "fixtures" / "fine_estimate_vectors.json"
        cases = json.loads(path.read_text(encoding="utf-8"))["cases"]
        sub_schema = self.schema["properties"]["fine_estimate"]
        for case in cases:
            with self.subTest(case=case["name"]):
                result = fine_estimate.estimate_if_unknown("과태료", case["record"])
                if result is None:  # 음성 case: payload 라면 이 키가 없다 — 검증 대상 아님
                    continue
                value = {"won": result["amount"], "rule": result["rule"], "rule_version": fine_estimate.RULE_VERSION}
                _validate_schema(value, sub_schema)


class FineEstimateResendTests(unittest.TestCase):
    """fine-estimate-contract.md "기존 신고 다시 보내기": 1회성·재개·동의 없음/삭제 대기 제외."""

    def setUp(self):
        from core.database import database, models
        from core.utils import logger
        logger.LoggerFactory.create_logger(mode="crawl")
        self.models = models
        self._dir = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self._dir.name) / 'data.db'}")
        database.upgrade_schema(self.engine)
        context_patch = mock.patch('services.community_cloud.capture_context',
                                   side_effect=lambda ctx: (ctx, 'active' if ctx and ctx.get('state') == 'active' else 'none'))
        context_patch.start(); self.addCleanup(context_patch.stop)
        self.tmp = tempfile.mkdtemp()
        self.store = CommunityStore.open(self.tmp)
        self.store.set_context(**CTX)

    def tearDown(self):
        self.engine.dispose()
        self._dir.cleanup()
        self.store.close()
        CommunityStore._forget(os.path.join(self.tmp, "community.db"))

    def _insert_report(self, rid, **overrides):
        base = dict(ID=rid, 상태="답변완료", 신고번호=f"SPP-2609-{rid}", 신고명="불법주정차 위반",
                    신고일="2026-09-01", 처리상태="수용", 차량번호="12가3456", 위반법규="",
                    범칙금_과태료="과태료", 벌점="", 처리기관="서울특별시 중구청", 담당자="홍길동",
                    답변일="2026-09-10", 발생일자="2026-09-01", 발생시각="10:00", 위반장소="서울특별시 중구",
                    종결여부="Y", 신고내용="", 처리내용="", 지도="", 첨부사진="", 첨부파일="",
                    synced_at=0, 보완횟수=0, 보완_미응답="N",
                    사진_첫촬영=None, 사진_끝촬영=None, 사진_촬영수=None,
                    category="parking", entry_value="불법주정차신고")
        base.update(overrides)
        with self.engine.begin() as conn:
            conn.execute(self.models.report_table.insert().values(**base))
        return base

    def _seed_pre_update_journal(self, source_report_id, row, *, eligible=True):
        """'이 기능이 생기기 전'을 흉내: 지금 다시 만들면 fine_estimate 가 붙을 payload 에서 그 키를 뺀 모습으로
        journal 을 먼저 만든다(parser_version pc-parser-5). resend 가 이걸 새 payload(있으면 fine_estimate 포함)로 갈아친다."""
        adapter = cap._resend_adapter_input(row)
        payload = cap.build_payload(adapter)
        payload.pop("fine_estimate", None)
        sha = cap.payload_sha256(payload)
        now = cap._now_iso()
        # resend 가 다시 만들 report_number 와 맞춰 둔다 — 안 맞으면 capture() 의 "번호만 뒤늦게 채워짐" 규칙이
        # sha 와 무관하게 새 이벤트를 발급해(REVIEW2 높음-1 대응) fine_estimate 변화와 섞인다.
        report_number = adapter.get("report_number")
        with self.store.transaction() as tx:
            revision = self.store.next_revision(tx)
            tx.execute(
                "INSERT INTO source_journal(event_id, project_namespace, local_dataset_id, dataset_key,"
                " source_report_id, report_number, source_revision, event_type, captured_at, capture_trigger,"
                " schema_version, parser_version, payload_json, payload_sha256, eligible,"
                " contributor_fingerprint, connection_id, writer_epoch, consent_grant_id, personal_save_state)"
                " VALUES (?, 'test', ?, ?, ?, ?, ?, 'completed_observation', ?, 'realtime',"
                " ?, 'pc-parser-5', ?, ?, ?, ?, ?, ?, ?, 'saved')",
                (str(uuid.uuid4()), self.store.local_dataset_id(), CTX["dataset_key"], source_report_id, report_number,
                 revision, now, cap.SCHEMA_VERSION, cap.canonical_json(payload), sha, 1 if eligible else 0,
                 CTX["contributor_fingerprint"], CTX["connection_id"], CTX["writer_epoch"], CTX["consent_grant_id"]))
        return sha

    def test_resend_sends_only_changed_payloads_once(self):
        matched = self._insert_report("R1")
        confirmed = self._insert_report("R2", 범칙금_과태료="과태료: 40,000원")
        self._seed_pre_update_journal("R1", matched)
        self._seed_pre_update_journal("R2", confirmed)

        result = cap.resend_fine_estimate_updates(self.engine, data_dir=self.tmp)
        self.assertEqual(result["state"], "done")
        self.assertEqual(result["candidates"], 2)
        self.assertEqual(result["resent"], 1)  # R1 만 바뀜(fine_estimate 신규)
        self.assertEqual(result["errors"], 0)

        conn = self.store.connect()
        r1_rows = conn.execute(
            "SELECT payload_sha256, payload_json FROM source_journal WHERE source_report_id='R1' ORDER BY source_revision").fetchall()
        self.assertEqual(len(r1_rows), 2)
        self.assertNotEqual(r1_rows[0]["payload_sha256"], r1_rows[1]["payload_sha256"])
        self.assertIn("fine_estimate", json.loads(r1_rows[1]["payload_json"]))
        outbox_r1 = conn.execute(
            "SELECT COUNT(*) v FROM outbox o JOIN source_journal j ON j.event_id=o.event_id"
            " WHERE j.source_report_id='R1'").fetchone()["v"]
        self.assertEqual(outbox_r1, 1)  # 새 이벤트만 outbox 에 들어간다

        r2_count = conn.execute("SELECT COUNT(*) v FROM source_journal WHERE source_report_id='R2'").fetchone()["v"]
        self.assertEqual(r2_count, 1)  # 안 바뀜 — 다시 보내지 않음

        # 1회성: 완료 표시 뒤에는 다시 돌아도 아무 것도 하지 않는다.
        total_before = conn.execute("SELECT COUNT(*) v FROM source_journal").fetchone()["v"]
        again = cap.resend_fine_estimate_updates(self.engine, data_dir=self.tmp)
        self.assertEqual(again["state"], "already_done")
        self.assertEqual(conn.execute("SELECT COUNT(*) v FROM source_journal").fetchone()["v"], total_before)

    def test_amount_typo_parser_bump_resends_once_without_crawl(self):
        from services import fine_estimate
        row = self._insert_report("T1", 범칙금_과태료="과태료: 40,0000원")
        self._seed_pre_update_journal("T1", row)
        conn = self.store.connect()
        old = cap.build_payload(cap._resend_adapter_input(row))
        old["amount"]["confirmed_won"] = None
        old["fine_estimate"] = {"won": 40000, "rule": "parking", "rule_version": fine_estimate.RULE_VERSION}
        conn.execute("UPDATE source_journal SET payload_json=?, payload_sha256=?, parser_version='pc-parser-6' WHERE source_report_id='T1'",
                     (cap.canonical_json(old), cap.payload_sha256(old)))
        conn.commit()
        cap._set_resend_marker(self.tmp, {"rule_version": fine_estimate.RULE_VERSION, "parser_version": "pc-parser-6"})
        result = cap.resend_fine_estimate_updates(self.engine, data_dir=self.tmp)
        self.assertEqual(result["resent"], 1)
        latest = conn.execute("SELECT payload_json, parser_version FROM source_journal WHERE source_report_id='T1' ORDER BY source_revision DESC").fetchone()
        self.assertEqual(json.loads(latest["payload_json"])["amount"]["confirmed_won"], 40000)
        self.assertEqual(latest["parser_version"], "pc-parser-7")
        self.assertEqual(cap.resend_fine_estimate_updates(self.engine, data_dir=self.tmp)["state"], "already_done")
        with self.engine.connect() as db:
            stored = db.execute(self.models.report_table.select().where(self.models.report_table.c.ID == "T1")).mappings().one()
        self.assertEqual(stored["범칙금_과태료"], "과태료: 40,0000원")

    def test_rule_version_bump_runs_again(self):
        from services import fine_estimate
        matched = self._insert_report("R1")
        self._seed_pre_update_journal("R1", matched)
        cap.resend_fine_estimate_updates(self.engine, data_dir=self.tmp)
        self.assertEqual(cap._resend_marker(self.tmp), {"rule_version": fine_estimate.RULE_VERSION, "parser_version": cap.PARSER_VERSION})
        # 규칙 버전이 바뀐 것처럼 표시를 되돌리면 완료 표시가 더는 맞지 않아 다시 돈다.
        with self.store.transaction() as tx:
            self.store.set_meta(cap.RESEND_MARKER_KEY, json.dumps({"rule_version": "old", "parser_version": "old"}), tx)
        again = cap.resend_fine_estimate_updates(self.engine, data_dir=self.tmp)
        self.assertEqual(again["state"], "done")  # 마커가 안 맞아 다시 전체를 훑는다(이미 반영된 건 no-op)
        self.assertEqual(again["resent"], 0)  # R1 은 이미 최신이라 다시 보낼 필요 없음

    def test_partial_failure_is_not_marked_done_and_resumes_next_run(self):
        matched = self._insert_report("R1")
        other = self._insert_report("R3")
        self._seed_pre_update_journal("R1", matched)
        self._seed_pre_update_journal("R3", other)

        original_capture = cap.capture

        def flaky_capture(adapter_input, *, source_report_id, trigger, rebuild_run_id=None, data_dir=None):
            if source_report_id == "R3":
                raise RuntimeError("boom")
            return original_capture(adapter_input, source_report_id=source_report_id, trigger=trigger,
                                    rebuild_run_id=rebuild_run_id, data_dir=data_dir)

        with mock.patch.object(cap, "capture", side_effect=flaky_capture):
            first = cap.resend_fine_estimate_updates(self.engine, data_dir=self.tmp)
        self.assertEqual(first["state"], "partial")
        self.assertEqual(first["errors"], 1)
        self.assertEqual(first["resent"], 1)
        self.assertIsNone(cap._resend_marker(self.tmp))  # 실패가 있으면 완료로 표시하지 않는다

        # 다음 실행: 전체를 다시 훑되 R1 은 이미 반영돼 no-op, R3 만 실제로 새로 처리된다("이어서").
        second = cap.resend_fine_estimate_updates(self.engine, data_dir=self.tmp)
        self.assertEqual(second["state"], "done")
        self.assertEqual(second["candidates"], 2)
        self.assertEqual(second["resent"], 1)
        self.assertIsNotNone(cap._resend_marker(self.tmp))

    def test_resend_without_active_context_writes_journal_but_not_outbox(self):
        """동의·계정 문맥이 없으면(기존 규칙) journal 만 기록하고 outbox 에 넣지 않는다 — 보내지 않는다."""
        matched = self._insert_report("R1")
        self._seed_pre_update_journal("R1", matched)
        self.store.deactivate_context("consent_revoked")
        result = cap.resend_fine_estimate_updates(self.engine, data_dir=self.tmp)
        self.assertEqual(result["state"], "done")
        self.assertEqual(result["resent"], 1)
        conn = self.store.connect()
        self.assertEqual(conn.execute("SELECT COUNT(*) v FROM outbox").fetchone()["v"], 0)
        row = conn.execute(
            "SELECT blocked_reason FROM source_journal WHERE source_report_id='R1' ORDER BY source_revision DESC LIMIT 1").fetchone()
        self.assertEqual(row["blocked_reason"], "consent:none")

    def test_resend_skips_while_deletion_cleanup_is_pending(self):
        """중앙 삭제 결과가 아직 불명(prepared)이면 이번 실행은 건너뛰고 완료로 표시하지 않는다."""
        matched = self._insert_report("R1")
        self._seed_pre_update_journal("R1", matched)
        cap.begin_deletion(self.tmp)
        result = cap.resend_fine_estimate_updates(self.engine, data_dir=self.tmp)
        self.assertEqual(result["state"], "deletion_pending")
        self.assertIsNone(cap._resend_marker(self.tmp))
        conn = self.store.connect()
        self.assertEqual(conn.execute("SELECT COUNT(*) v FROM source_journal WHERE source_report_id='R1'").fetchone()["v"], 1)


if __name__ == "__main__":
    unittest.main()
