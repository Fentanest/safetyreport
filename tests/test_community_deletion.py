"""공유 자료 삭제 뒤 로컬 차단 (Sol 통합 검토 H-03, 2차 H-03a/b).

삭제 대기 표시는 journal 과 같은 community.db 에 트랜잭션으로 남기고(중앙 삭제 요청 전), 적용도 한 트랜잭션이다.
표시를 못 쓰면 중앙 삭제를 요청하지 않는다. 표시가 남아 있으면 업로드·reshare 를 보내지 않는다. 표시는 id 별이라 동시 삭제가 서로를 지우지 않는다.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import threading
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
        self.sent = []
        for p in (mock.patch.object(up, "_gate_check", return_value={"state": "ok", "can_enter": True, "reasons": []}),
                  mock.patch.object(client, "post_envelope", side_effect=lambda env, **k: self.sent.append(env) or
                                    client.IngestResponse(ok=False, http_status=503, code="busy", request_id=None, results=[]))):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(setattr, up, "_active_run", None)

    def journal(self):
        return {r["source_report_id"]: r["blocked_reason"] for r in
                self.store.connect().execute("SELECT source_report_id, blocked_reason FROM source_journal")}

    def pending_keys(self):
        return [r["key"] for r in self.store.connect().execute("SELECT key FROM meta WHERE key LIKE 'deletion_pending:%'")]

    def test_rows_existing_at_deletion_are_blocked_even_with_a_future_clock(self):
        cap.capture(dict(INPUT), source_report_id="R1", trigger="realtime", data_dir=self.tmp)
        cap.capture(dict(INPUT), source_report_id="R2", trigger="realtime", data_dir=self.tmp)
        with self.store.transaction() as tx:
            tx.execute("UPDATE source_journal SET captured_at='2099-01-01T00:00:00.000Z' WHERE source_report_id='R2'")
        cap.begin_deletion(self.tmp)
        cap.apply_pending_deletion(self.tmp)
        self.assertEqual(self.journal(), {"R1": "deleted_by_user", "R2": "deleted_by_user"})
        self.assertEqual({r["state"] for r in self.store.connect().execute("SELECT state FROM outbox")}, {"blocked"})
        self.assertEqual(self.pending_keys(), [])
        cap.capture(dict(INPUT, processing_status="일부수용"), source_report_id="R3", trigger="realtime", data_dir=self.tmp)
        self.assertIsNone(self.journal()["R3"], "삭제 뒤 새 관측은 막지 않는다")

    def test_pending_marker_blocks_upload_and_reshare_until_applied(self):
        cap.capture(dict(INPUT), source_report_id="R1", trigger="realtime", data_dir=self.tmp)
        cap.begin_deletion(self.tmp)
        with mock.patch.object(cap, "apply_pending_deletion", side_effect=RuntimeError("disk")):
            run = up.request_upload("manual", data_dir=self.tmp)
            self.assertEqual((run["result"], run.get("error_code")), ("deferred", "deletion_cleanup_pending"))
            self.assertEqual(up.request_reshare(data_dir=self.tmp)["error_code"], "deletion_cleanup_pending")
            self.assertEqual(self.sent, [])
        self.assertIsNone(self.journal()["R1"], "아직 적용 전")
        self.assertEqual(len(self.pending_keys()), 1, "적용 실패면 표시가 남는다")
        up.request_upload("manual", data_dir=self.tmp)  # 다음 실행이 먼저 적용한다
        self.assertEqual(self.journal()["R1"], "deleted_by_user")
        self.assertEqual(self.pending_keys(), [])
        self.assertEqual(self.sent, [], "막힌 행은 보내지 않는다")

    def test_route_does_not_call_central_delete_when_the_marker_cannot_be_written(self):
        from services.community_auth_service import CommunityAuthError
        from web.routers import community_route as route

        central = mock.Mock()
        with mock.patch.object(cap, "begin_deletion", side_effect=OSError("readonly")), \
             mock.patch.object(route, "_account_call", central):
            with self.assertRaises(CommunityAuthError):
                route._contributions_delete()
        central.assert_not_called()

    def test_failed_central_delete_removes_only_its_own_marker(self):
        from services.community_auth_service import CommunityAuthError
        from web.routers import community_route as route

        other = cap.begin_deletion(self.tmp)  # 다른 진행 중 삭제의 표시
        with mock.patch.object(cap, "_store", lambda data_dir=None: self.store), \
             mock.patch.object(route, "_account_call", side_effect=CommunityAuthError("account_busy", "x")), \
             mock.patch.object(route.community_gate, "invalidate"):
            with self.assertRaises(CommunityAuthError):
                route._contributions_delete()
        self.assertEqual(self.pending_keys(), ["deletion_pending:" + other])

    def test_concurrent_deletions_each_apply_and_none_is_lost(self):
        for i in range(4):
            cap.capture(dict(INPUT), source_report_id=f"R{i}", trigger="realtime", data_dir=self.tmp)
        ids = [cap.begin_deletion(self.tmp) for _ in range(2)]
        errors = []

        def work():
            try:
                cap.apply_pending_deletion(self.tmp)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=work) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(self.pending_keys(), [])
        self.assertEqual(set(self.journal().values()), {"deleted_by_user"})
        self.assertEqual(len(ids), 2)


if __name__ == "__main__":
    unittest.main()
