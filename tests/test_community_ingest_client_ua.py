"""업로드 클라이언트는 Python 기본 User-Agent 를 보내지 않는다 — 셀프호스트 앞단 Cloudflare 가 봇으로 막는다(2026-10-12)."""
import unittest
from unittest import mock

from services import community_ingest_client as client


class _Resp:
    status = 200
    headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n):
        return b"{}"


class UserAgentTest(unittest.TestCase):
    def test_post_sends_app_user_agent(self):
        seen = {}

        class _Opener:
            def open(self, request, timeout=None):
                seen.update(request.header_items())
                return _Resp()

        with mock.patch.object(client.urllib.request, "build_opener", return_value=_Opener()):
            client._http_post("https://example.invalid/x", {"Content-Type": "application/json"}, b"{}", 5)
        ua = {k.lower(): v for k, v in seen.items()}.get("user-agent", "")
        self.assertTrue(ua.startswith("safetyreport/"), ua)
        self.assertNotIn("Python-urllib", ua)


if __name__ == "__main__":
    unittest.main()
