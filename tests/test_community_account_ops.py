"""EO R-08: 커뮤니티 계정 업무 순서(services/community_account_ops)를 호출 순서 기록으로 확인한다."""

import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("SAFETYREPORT_DATA_DIR", tempfile.mkdtemp(prefix="sr-r08-"))

from core.storage.exchange import RestoreRefused
from services import community_account_ops as ops
from services.community_account_client import AccountApiError
from services.community_auth_service import CommunityAuthError


class _Recorder:
    def __init__(self, test, *, owner="910001", kakao="910001", owner_error=None, wipe_error=None, gate_state="ok"):
        self.calls = []
        service = mock.Mock()
        service.session_kakao_id.side_effect = lambda: self._log("session_kakao_id", kakao)
        service.current_kakao_id.side_effect = lambda: self._log("current_kakao_id", kakao)
        service.disconnect.side_effect = lambda: self._log("disconnect", {"disconnected": True})
        service.status.side_effect = lambda **k: {"state": "disconnected"}

        def db_owner():
            self.calls.append("db_owner")
            if owner_error:
                raise owner_error
            return owner

        def wipe(reason, then_owner=None):
            self.calls.append(f"wipe:{reason}:{then_owner}")
            if wipe_error:
                raise wipe_error
            return {"wiped": True}

        patches = [
            mock.patch.object(ops.cas, "get_service", return_value=service),
            mock.patch("services.account_data.db_owner", side_effect=db_owner),
            mock.patch("services.account_data.wipe_report_data", side_effect=wipe),
            mock.patch.object(ops.community_gate, "invalidate", side_effect=lambda r: self.calls.append(f"invalidate:{r}")),
            mock.patch.object(ops.community_gate, "status_view", return_value={}),
            mock.patch.object(ops.community_gate, "evaluate", return_value={"state": gate_state}),
            mock.patch.object(ops.community_gate, "refresh_now", side_effect=lambda: self.calls.append("refresh")),
            mock.patch.object(ops.community_gate, "claim_for_this_device"),
        ]
        for p in patches:
            p.start()
            test.addCleanup(p.stop)

    def _log(self, name, value):
        self.calls.append(name)
        return value


class LogoutOrderTest(unittest.TestCase):
    def test_owner_same_invalidates_then_wipes_then_disconnects(self):
        rec = _Recorder(self)
        result = ops.logout()
        self.assertEqual(rec.calls, ["db_owner", "session_kakao_id", "invalidate:logout", "wipe:kakao_logout:None",
                                     "disconnect"])
        self.assertTrue(result["result"]["reports_wiped"])

    def test_other_accounts_data_is_kept(self):
        rec = _Recorder(self, owner="910001", kakao="910002")
        result = ops.logout()
        self.assertEqual(rec.calls, ["db_owner", "session_kakao_id", "invalidate:logout", "disconnect"])
        self.assertFalse(result["result"]["reports_wiped"])

    def test_unknown_owner_changes_nothing(self):
        import sqlite3
        rec = _Recorder(self, owner_error=sqlite3.OperationalError("locked"))
        with self.assertRaises(ops.OperationRefused):
            ops.logout()
        self.assertEqual(rec.calls, ["db_owner"])

    def test_refused_wipe_keeps_the_login(self):
        rec = _Recorder(self, wipe_error=RestoreRefused("크롤링 중"))
        with self.assertRaises(ops.OperationRefused):
            ops.logout()
        self.assertEqual(rec.calls, ["db_owner", "session_kakao_id", "invalidate:logout", "wipe:kakao_logout:None",
                                     "invalidate:logout_refused"])


class AdoptOrderTest(unittest.TestCase):
    def test_only_when_gate_reports_owner_mismatch(self):
        rec = _Recorder(self, gate_state="ok")
        with self.assertRaises(CommunityAuthError) as ctx:
            ops.adopt_db_owner()
        self.assertEqual(ctx.exception.code, "invalid_state")
        self.assertEqual(rec.calls, [])

    def test_needs_the_current_kakao_id(self):
        rec = _Recorder(self, gate_state="db_owner_mismatch", kakao=None)
        with self.assertRaises(CommunityAuthError) as ctx:
            ops.adopt_db_owner()
        self.assertEqual(ctx.exception.code, "not_connected")
        self.assertEqual(rec.calls, ["current_kakao_id"])

    def test_wipes_as_the_current_account_then_rechecks(self):
        rec = _Recorder(self, gate_state="db_owner_mismatch", kakao="910002")
        ops.adopt_db_owner()
        self.assertEqual(rec.calls, ["current_kakao_id", "wipe:db_owner_adopt:910002", "invalidate:db_owner_adopt", "refresh"])

    def test_refused_wipe(self):
        rec = _Recorder(self, gate_state="db_owner_mismatch", kakao="910002", wipe_error=RestoreRefused("x"))
        with self.assertRaises(ops.OperationRefused):
            ops.adopt_db_owner()
        self.assertEqual(rec.calls, ["current_kakao_id", "wipe:db_owner_adopt:910002"])


class AccountCallTest(unittest.TestCase):
    def _service(self):
        service = mock.Mock()
        service.config.return_value = mock.Mock(supabase_url="http://127.0.0.1:1", publishable_key="pk")
        service.get_access_token.return_value = "token"
        return service

    def test_lost_central_response_is_a_retryable_503(self):
        with mock.patch.object(ops.cas, "get_service", return_value=self._service()):
            def call(client, token):
                raise AccountApiError("network_error")
            with self.assertRaises(CommunityAuthError) as ctx:
                ops.account_call(call)
        self.assertEqual((ctx.exception.code, ctx.exception.status), ("account_network_error", 503))

    def test_known_central_refusals_keep_their_status_and_extra(self):
        with mock.patch.object(ops.cas, "get_service", return_value=self._service()):
            def call(client, token):
                raise AccountApiError("writer_conflict", 409, None, {"active_writer": {"device_label": "PC"}},
                                      transient=False)
            with self.assertRaises(CommunityAuthError) as ctx:
                ops.account_call(call)
        self.assertEqual((ctx.exception.code, ctx.exception.status), ("writer_conflict", 409))
        self.assertEqual(ctx.exception.extra["active_writer"]["device_label"], "PC")

    def test_consent_revoke_stops_uploads_before_calling_the_centre(self):
        calls = []
        with mock.patch.object(ops.community_gate, "current_grant_id", return_value="g-1"), \
                mock.patch.object(ops.community_gate, "invalidate", side_effect=lambda r: calls.append(f"invalidate:{r}")), \
                mock.patch.object(ops, "account_call", side_effect=lambda fn: calls.append("central") or {"revoked": True}), \
                mock.patch.object(ops, "regate", side_effect=lambda r: calls.append(f"regate:{r}") or {}):
            result = ops.consent_revoke()
        self.assertEqual(calls, ["invalidate:consent_revoked", "central", "regate:consent_revoked"])
        self.assertTrue(result["result"]["revoked"])


if __name__ == "__main__":
    unittest.main()
