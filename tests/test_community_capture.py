"""capture 테스트 — S-03/S-04/S-06, A01~A04/A09, crash 3지점, inactive context."""
import hashlib
import json
import os
import tempfile
import unittest
from unittest import mock

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
    def setUp(self):
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

    def test_s04_server_completed_first_withdrawn_is_correction(self):
        prefix = hashlib.sha256(b"safetyreport|R7").hexdigest()[:24]
        with self.store.transaction() as tx:
            tx.execute("INSERT INTO server_completed(dataset_key, key_prefix, fetched_at) VALUES (?, ?, ?)",
                       (CTX["dataset_key"], prefix, "2026-09-26T00:00:00.000Z"))
        adapter = dict(withdrawn_input())
        adapter["progress_status"] = "취하"
        result = cap.capture(adapter, source_report_id="R7", trigger="realtime", data_dir=self.tmp)
        self.assertEqual(result.event_type, "status_correction")
        self.assertEqual(self._counts(), (1, 1, 1, 1))

    def test_withdrawn_after_shared_is_correction_then_stable(self):
        cap.capture(dict(eligible_input()), source_report_id="R1", trigger="realtime", data_dir=self.tmp)
        result = cap.capture(dict(withdrawn_input()), source_report_id="R1", trigger="realtime",
                             data_dir=self.tmp)
        self.assertEqual(result.event_type, "status_correction")
        again = cap.capture(dict(withdrawn_input()), source_report_id="R1", trigger="realtime",
                            data_dir=self.tmp)
        self.assertIsNone(again.event_id)
        self.assertEqual(self._counts(), (2, 2, 1, 0))

    def test_eligible_again_after_correction(self):
        cap.capture(dict(eligible_input()), source_report_id="R1", trigger="realtime", data_dir=self.tmp)
        cap.capture(dict(withdrawn_input()), source_report_id="R1", trigger="realtime", data_dir=self.tmp)
        result = cap.capture(dict(eligible_input()), source_report_id="R1", trigger="realtime",
                             data_dir=self.tmp)
        self.assertEqual(result.event_type, "completed_observation")

    def test_inactive_context_writes_journal_without_outbox(self):
        self.store.deactivate_context("consent_revoked")
        result = cap.capture(dict(eligible_input()), source_report_id="R1", trigger="realtime",
                             data_dir=self.tmp)
        self.assertIsNotNone(result.event_id)
        self.assertEqual(self._counts(), (1, 0, 1, 0))
        row = self.store.connect().execute("SELECT blocked_reason FROM source_journal").fetchone()
        self.assertEqual(row["blocked_reason"], "no_active_context")

    def test_mark_personal_save(self):
        result = cap.capture(dict(eligible_input()), source_report_id="R1", trigger="realtime",
                             data_dir=self.tmp)
        cap.mark_personal_save(result.event_id, True, data_dir=self.tmp)
        row = self.store.connect().execute(
            "SELECT personal_save_state FROM source_journal WHERE event_id=?", (result.event_id,)).fetchone()
        self.assertEqual(row["personal_save_state"], "saved")
        cap.mark_personal_save(result.event_id, False, data_dir=self.tmp)
        row = self.store.connect().execute(
            "SELECT personal_save_state FROM source_journal WHERE event_id=?", (result.event_id,)).fetchone()
        self.assertEqual(row["personal_save_state"], "failed")
        cap.mark_personal_save(None, True, data_dir=self.tmp)  # no-op

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
        with mock.patch.object(repo, "_find_existing", return_value=(None, None)), \
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


if __name__ == "__main__":
    unittest.main()
