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

    def route_delete(self, central):
        """설정 화면 라우트를 실제 로컬 저장소로 실행한다(중앙 호출만 central 로 바꿈)."""
        from web.routers import community_route as route

        with mock.patch.object(cap, "_store", lambda data_dir=None: self.store), \
             mock.patch.object(route, "_account_call", side_effect=lambda fn: central()), \
             mock.patch.object(route.community_gate, "invalidate"), \
             mock.patch.object(route, "_regate", return_value={}), \
             mock.patch.object(route.cas, "get_service"):
            return route._contributions_delete()

    def states(self):
        return sorted(r["s"] for r in self.store.connect().execute(
            "SELECT json_extract(value, '$.state') AS s FROM meta WHERE key LIKE 'deletion_pending:%'"))

    def test_rows_existing_at_deletion_are_blocked_even_with_a_future_clock(self):
        cap.capture(dict(INPUT), source_report_id="R1", trigger="realtime", data_dir=self.tmp)
        cap.capture(dict(INPUT), source_report_id="R2", trigger="realtime", data_dir=self.tmp)
        with self.store.transaction() as tx:
            tx.execute("UPDATE source_journal SET captured_at='2099-01-01T00:00:00.000Z' WHERE source_report_id='R2'")
        self.route_delete(lambda: {"deletion_id": "d1"})
        self.assertEqual(self.journal(), {"R1": "deleted_by_user", "R2": "deleted_by_user"})
        self.assertEqual({r["state"] for r in self.store.connect().execute("SELECT state FROM outbox")}, {"blocked"})
        self.assertEqual(self.pending_keys(), [])
        cap.capture(dict(INPUT, processing_status="일부수용"), source_report_id="R3", trigger="realtime", data_dir=self.tmp)
        self.assertIsNone(self.journal()["R3"], "삭제 뒤 새 관측은 막지 않는다")

    def test_prepared_marker_blocks_but_is_never_applied_or_removed_before_the_center_answers(self):
        """Sol 3차 H-03c: 중앙 응답을 기다리는 동안 업로드·reshare·정리 시도가 끼어들어도 표시를 지우지 않는다."""
        cap.capture(dict(INPUT), source_report_id="R1", trigger="realtime", data_dir=self.tmp)
        seen = {}

        def central():
            cap.capture(dict(INPUT, processing_status="일부수용"), source_report_id="R9", trigger="realtime", data_dir=self.tmp)
            seen["pending"] = cap.deletion_cleanup_pending(self.tmp)
            seen["upload"] = up.request_upload("manual", data_dir=self.tmp)
            seen["reshare"] = up.request_reshare(data_dir=self.tmp)
            seen["states"] = self.states()
            seen["journal"] = self.journal()
            return {"deletion_id": "d1"}

        self.route_delete(central)
        self.assertTrue(seen["pending"])
        self.assertEqual((seen["upload"]["result"], seen["upload"].get("error_code")), ("deferred", "deletion_cleanup_pending"))
        self.assertEqual(seen["reshare"]["error_code"], "deletion_cleanup_pending")
        self.assertEqual(seen["states"], ["prepared"], "중앙 결과 전에는 표시가 그대로")
        self.assertEqual(seen["journal"], {"R1": None, "R9": None}, "중앙 결과 전에는 적용하지 않는다")
        self.assertEqual(self.journal(), {"R1": "deleted_by_user", "R9": "deleted_by_user"}, "확정 때 그 시점까지 전부 차단")
        self.assertEqual(self.pending_keys(), [])
        self.assertEqual(self.sent, [])

    def test_unknown_central_outcome_keeps_the_marker_and_a_retry_confirms(self):
        """Sol 3차 H-03d: 응답 불명(네트워크·5xx)은 실패로 보지 않는다 — 표시 유지, 다시 요청하면 확정."""
        from services.community_auth_service import CommunityAuthError

        cap.capture(dict(INPUT), source_report_id="R1", trigger="realtime", data_dir=self.tmp)

        def lost():
            err = CommunityAuthError("account_network_error", "x")
            err.status = 503
            raise err

        with self.assertRaises(CommunityAuthError) as ctx:
            self.route_delete(lost)
        self.assertEqual(ctx.exception.code, "deletion_unconfirmed")
        self.assertEqual(self.states(), ["prepared"])
        self.assertEqual(cap.deletion_state(self.tmp), "unconfirmed")
        self.assertEqual(up.request_upload("manual", data_dir=self.tmp).get("error_code"), "deletion_cleanup_pending")
        self.assertIsNone(self.journal()["R1"])
        self.route_delete(lambda: {"deletion_id": "d2"})  # 다시 요청(삭제는 여러 번 안전)
        self.assertEqual(self.pending_keys(), [], "앞선 prepared 표시도 함께 확정·적용")
        self.assertEqual(self.journal()["R1"], "deleted_by_user")
        self.assertEqual(self.sent, [])

    def test_definitive_central_refusal_removes_only_its_own_marker(self):
        from services.community_auth_service import CommunityAuthError

        other = cap.begin_deletion(self.tmp)  # 다른 진행 중 삭제의 표시(응답 대기 중)

        def refused():
            err = CommunityAuthError("kakao_required", "x")
            err.status = 403
            raise err

        with self.assertRaises(CommunityAuthError):
            self.route_delete(refused)
        self.assertEqual(self.pending_keys(), ["deletion_pending:" + other])
        self.assertEqual(self.states(), ["prepared"])

    def test_confirmed_marker_whose_apply_failed_blocks_until_applied(self):
        cap.capture(dict(INPUT), source_report_id="R1", trigger="realtime", data_dir=self.tmp)
        with mock.patch.object(cap, "apply_pending_deletion", side_effect=RuntimeError("disk")):
            res = self.route_delete(lambda: {"deletion_id": "d3"})
            self.assertTrue(res["local_cleanup_pending"])
            self.assertEqual(up.request_upload("manual", data_dir=self.tmp).get("error_code"), "deletion_cleanup_pending")
        self.assertEqual(self.states(), ["confirmed"])
        up.request_upload("manual", data_dir=self.tmp)  # 다음 실행이 먼저 적용
        self.assertEqual(self.journal()["R1"], "deleted_by_user")
        self.assertEqual(self.pending_keys(), [])
        self.assertEqual(self.sent, [])

    def test_writer_file_failure_after_central_success_does_not_block_local_confirmation(self):
        """Sol 4차 3: 중앙 성공 뒤 writer 파일 삭제가 실패해도 로컬 확정·적용은 끝나고, 응답이 그 상태를 알린다."""
        from web.routers import community_route as route

        cap.capture(dict(INPUT), source_report_id="R1", trigger="realtime", data_dir=self.tmp)
        with mock.patch.object(cap, "_store", lambda data_dir=None: self.store), \
             mock.patch.object(route, "_account_call", side_effect=lambda fn: {"deletion_id": "d4"}), \
             mock.patch.object(route.community_gate, "invalidate"), \
             mock.patch.object(route, "_regate", return_value={}), \
             mock.patch.object(route.cas, "get_service") as svc:
            svc.return_value.store.save_writer.side_effect = OSError("fsync failed")
            res = route._contributions_delete()
        svc.return_value.store.save_writer.assert_called_once_with(None)
        self.assertFalse(res["local_cleanup_pending"])
        self.assertTrue(res["writer_reset_pending"])
        self.assertEqual(self.journal()["R1"], "deleted_by_user")
        self.assertEqual(self.pending_keys(), [])

    def test_route_does_not_call_central_delete_when_the_marker_cannot_be_written(self):
        from services.community_auth_service import CommunityAuthError

        central = mock.Mock()
        with mock.patch.object(cap, "begin_deletion", side_effect=OSError("readonly")):
            with self.assertRaises(CommunityAuthError):
                self.route_delete(central)
        central.assert_not_called()

    def test_concurrent_applies_of_confirmed_markers_lose_nothing(self):
        for i in range(4):
            cap.capture(dict(INPUT), source_report_id=f"R{i}", trigger="realtime", data_dir=self.tmp)
        cap.begin_deletion(self.tmp)
        cap.begin_deletion(self.tmp)
        with self.store.transaction() as tx:
            tx.execute("UPDATE meta SET value=json_set(value, '$.state', 'confirmed') WHERE key LIKE 'deletion_pending:%'")
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

if __name__ == "__main__":
    unittest.main()
