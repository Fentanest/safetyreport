"""공유 자료 삭제 뒤 로컬 차단 (Sol 통합 검토 H-03): 시계와 무관한 행 순번 경계, 영속 표시, 실패 시 업로드·reshare 중단."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from services import community_capture as cap
from services import community_ingest_client as client
from services import community_uploader as up
from services.community_store import CommunityStore
from test_community_uploader import CTX, INPUT


class DeletionBlockTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.store = CommunityStore.open(self.tmp)
        self.addCleanup(CommunityStore._forget, os.path.join(self.tmp, "community.db"))
        self.store.set_context(**CTX)
        for p in (mock.patch.object(up, "_gate_check", return_value={"state": "ok", "can_enter": True, "reasons": []}),
                  mock.patch.object(client, "post_envelope", side_effect=AssertionError("must not send"))):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(setattr, up, "_active_run", None)

    def journal(self):
        return {r["source_report_id"]: r["blocked_reason"] for r in
                self.store.connect().execute("SELECT source_report_id, blocked_reason FROM source_journal")}

    def test_rows_existing_at_deletion_are_blocked_even_with_a_future_clock(self):
        cap.capture(dict(INPUT), source_report_id="R1", trigger="realtime", data_dir=self.tmp)
        cap.capture(dict(INPUT), source_report_id="R2", trigger="realtime", data_dir=self.tmp)
        with self.store.transaction() as tx:  # 앞선 시계로 기록된 행(이전 방식은 captured_at 비교라 빠졌다)
            tx.execute("UPDATE source_journal SET captured_at='2099-01-01T00:00:00.000Z' WHERE source_report_id='R2'")
        up.on_contributions_deleted(data_dir=self.tmp, deletion_id="del-1")
        self.assertEqual(self.journal(), {"R1": "deleted_by_user", "R2": "deleted_by_user"})
        states = {r["state"] for r in self.store.connect().execute("SELECT state FROM outbox")}
        self.assertEqual(states, {"blocked"})
        self.assertFalse(os.path.exists(os.path.join(self.tmp, cap.DELETION_MARKER)))
        cap.capture(dict(INPUT, processing_status="일부수용"), source_report_id="R3", trigger="realtime", data_dir=self.tmp)
        self.assertIsNone(self.journal()["R3"], "삭제 뒤 새 관측은 막지 않는다")

    def test_failed_local_block_keeps_marker_and_stops_upload_and_reshare(self):
        cap.capture(dict(INPUT), source_report_id="R1", trigger="realtime", data_dir=self.tmp)
        with mock.patch.object(cap, "apply_pending_deletion", side_effect=RuntimeError("disk")):
            with self.assertRaises(RuntimeError):
                up.on_contributions_deleted(data_dir=self.tmp, deletion_id="del-2")
            self.assertTrue(os.path.exists(os.path.join(self.tmp, cap.DELETION_MARKER)))
            run = up.request_upload("manual", data_dir=self.tmp)
            self.assertEqual((run["result"], run.get("error_code")), ("deferred", "deletion_cleanup_pending"))
            self.assertEqual(up.request_reshare(data_dir=self.tmp)["error_code"], "deletion_cleanup_pending")
        self.assertIsNone(self.journal()["R1"], "아직 적용 전")
        # 다음 실행: 표시를 먼저 적용하고 나서야 진행한다(보낼 행이 없으니 전송 없음)
        up.request_upload("manual", data_dir=self.tmp)
        self.assertEqual(self.journal()["R1"], "deleted_by_user")
        self.assertFalse(os.path.exists(os.path.join(self.tmp, cap.DELETION_MARKER)))

    def test_marker_records_the_boundary_before_applying(self):
        cap.capture(dict(INPUT), source_report_id="R1", trigger="realtime", data_dir=self.tmp)
        with mock.patch.object(cap, "apply_pending_deletion"):
            up.on_contributions_deleted(data_dir=self.tmp, deletion_id="del-3")
        with open(os.path.join(self.tmp, cap.DELETION_MARKER), encoding="utf-8") as fh:
            marker = json.load(fh)
        self.assertEqual((marker["deletion_id"], marker["journal_rowid_max"]), ("del-3", 1))
        cap.capture(dict(INPUT), source_report_id="R9", trigger="realtime", data_dir=self.tmp)
        cap.apply_pending_deletion(self.tmp)
        self.assertEqual(self.journal(), {"R1": "deleted_by_user", "R9": None}, "경계 뒤에 생긴 행은 막지 않는다")

    def test_corrupt_marker_blocks_every_existing_row_instead_of_locking_forever(self):
        cap.capture(dict(INPUT), source_report_id="R1", trigger="realtime", data_dir=self.tmp)
        with open(os.path.join(self.tmp, cap.DELETION_MARKER), "w", encoding="utf-8") as fh:
            fh.write("not-json")
        self.assertFalse(cap.deletion_cleanup_pending(self.tmp))
        self.assertEqual(self.journal()["R1"], "deleted_by_user")
        self.assertFalse(os.path.exists(os.path.join(self.tmp, cap.DELETION_MARKER)))


if __name__ == "__main__":
    unittest.main()
