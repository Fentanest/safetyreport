"""다시 불러오기 시작 409 에 사용자가 할 일을 담는다 — '시작 조건을 확인해 주세요' 같은 막연한 문구 대신(2026-10-12 사용자 지적)."""
import json
import unittest

from web.routers import community_rebuild_route as route


class PrerequisiteMessageTest(unittest.TestCase):
    def _body(self, payload):
        resp = route._state_response(payload)
        self.assertEqual(resp.status_code, 409)
        return json.loads(resp.body)

    def test_manifest_failure_reported_only_in_last_error_gets_its_message(self):
        body = self._body({"state": "prerequisites_required", "reason": None, "last_error": "manifest_unavailable"})
        self.assertEqual(body["code"], "prerequisites_required")
        self.assertIn("공유한 목록", body["message"])
        self.assertEqual(body["detail"], body["message"])

    def test_reason_takes_precedence(self):
        body = self._body({"state": "prerequisites_required", "reason": "official_account_missing", "last_error": "x"})
        self.assertIn("안전신문고 계정", body["message"])

    def test_unknown_reason_has_actionable_fallback(self):
        body = self._body({"state": "prerequisites_required"})
        self.assertNotIn("시작 조건을 확인", body["message"])
        self.assertTrue(body["message"])

    def test_normal_state_is_200(self):
        self.assertEqual(route._state_response({"state": "running"}).status_code, 200)


if __name__ == "__main__":
    unittest.main()
