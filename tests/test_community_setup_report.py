"""설정 단계 보고(4-2): 반복 억제, 60초, connection_registered 뒤 중단, 헤더, 금지 값 없음."""
import json
import tempfile
import unittest
from unittest import mock

from services import community_setup_report as sr
from services.community_store import CommunityStore


class SetupReportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = CommunityStore(self.tmp.name + "/community.db")
        self.calls = []
        self.clock = [1000.0]
        self.status = 200
        patches = [
            mock.patch.object(sr, "_store", return_value=self.store),
            mock.patch.object(sr, "_now", lambda: self.clock[0]),
            mock.patch.object(sr, "_credentials", return_value=("https://x.supabase.example", "pub-key", "tok")),
            mock.patch.object(sr, "_http_post", side_effect=self._post),
            mock.patch("services.community_gate._platform", return_value="windows"),
            mock.patch("services.community_ingest_client._client_version", return_value="3.6.2"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def _post(self, url, headers, body):
        self.calls.append((url, headers, json.loads(body)))
        return self.status, b"{}", {}

    def advance(self, seconds=61):
        self.clock[0] += seconds

    def test_body_and_headers(self):
        sr.report(stage="official_login_failed", reason="captcha_required")
        url, headers, body = self.calls[0]
        self.assertTrue(url.endswith("/functions/v1/community-account/setup-progress"))
        self.assertEqual(headers["X-Client-Version"], "3.6.2")
        self.assertEqual(headers["X-Client-App"], "pc/windows")
        self.assertEqual(body, {"protocol": 1, "client_version": "3.6.2", "source_app": "safetyreport", "platform": "windows",
                                "mode": "server", "stage": "official_login_failed", "reason": "captcha_required", "client": None})
        self.assertLessEqual(len(json.dumps(body)), 2048)

    def test_same_content_not_repeated_but_changed_reason_is(self):
        sr.report(stage="official_login_failed", reason="captcha_required")
        self.advance()
        sr.report(stage="official_login_failed", reason="captcha_required")
        self.assertEqual(len(self.calls), 1)
        sr.report(stage="official_login_failed", reason="invalid_credentials")
        self.assertEqual(len(self.calls), 2)

    def test_not_twice_within_60_seconds_and_resent_on_next_change(self):
        sr.report(stage="kakao_ok")
        self.advance(10)
        sr.report(stage="consent_ok")  # 60초 안: 보내지 않고 기록도 안 함
        self.assertEqual(len(self.calls), 1)
        self.advance(60)
        sr.report(stage="consent_ok")
        self.assertEqual([c[2]["stage"] for c in self.calls], ["kakao_ok", "consent_ok"])

    def test_failure_is_quiet_and_not_recorded_as_sent(self):
        self.status = 429
        sr.report(stage="kakao_ok")
        self.status = 200
        self.advance()
        sr.report(stage="kakao_ok")
        self.assertEqual(len(self.calls), 2)

    def test_stops_after_connection_registered_until_new_kakao_login(self):
        sr.report(stage="official_login_ok")
        self.advance()
        sr.report(stage="connection_registered")
        self.advance()
        sr.report(stage="official_login_failed", reason="network")
        self.assertEqual(len(self.calls), 2)
        self.advance()
        sr.report(stage="kakao_ok")  # 새 설정 흐름
        self.assertEqual(len(self.calls), 3)

    def test_invalid_input_never_raises_or_sends(self):
        sr.report(stage="client_server_checked")
        sr.report(stage="unknown")
        sr.report(stage="official_login_failed", reason="Bad Reason http://x")
        self.assertEqual(self.calls[0][2]["reason"], None)
        self.assertEqual(len(self.calls), 1)

    def test_success_stage_reason_forced_null_and_no_forbidden_keys(self):
        sr.report(stage="official_login_ok", reason="whatever")
        body = self.calls[0][2]
        self.assertIsNone(body["reason"])
        self.assertEqual(set(body), {"protocol", "client_version", "source_app", "platform", "mode", "stage", "reason", "client"})

    def test_transport_error_is_swallowed(self):
        with mock.patch.object(sr, "_http_post", side_effect=OSError("down")):
            sr.report(stage="kakao_ok")


if __name__ == "__main__":
    unittest.main()
