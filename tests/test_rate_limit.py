"""공식 사이트 요청 속도 제한(2026-10-24 실측: 상세를 간격 없이 보내면 18건 만에 429 `detail_rate_limited`).

429 는 같은 요청을 늘려 가며 기다렸다 다시 보내고, 다 쓰면 오류로 돌려준다. 상세 크롤은 그때 남은 신고를 계속
두드리지 않고 다음 실행으로 넘긴다. 합성 응답만 쓴다(실제 HTTP 없음).
"""
import unittest
from unittest import mock

from core.crawler import api_client, crawldetail_api

LIMITED = {"error": "request_limited", "reason": "detail_rate_limited"}


class FakeResponse:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


class RateLimitTests(unittest.TestCase):
    def setUp(self):
        api_client._next_at = 0.0
        api_client._interval = None
        api_client._ok_streak = 0
        self.sleeps = []
        patcher = mock.patch.object(api_client, "sleep", side_effect=self.sleeps.append)
        patcher.start()
        self.addCleanup(patcher.stop)
        log = mock.patch.object(api_client.logger.LoggerFactory, "logbot", mock.Mock())
        log.start()
        self.addCleanup(log.stop)

    def run_with(self, responses):
        calls = []

        def request(session, method, url, **kw):
            calls.append(url)
            return responses.pop(0)

        with mock.patch.object(api_client.direct_login, "request_with_retry", side_effect=request):
            payload, _ = api_client.get_authorized_json(object(), "https://www.safetyreport.go.kr/x/R1")
        return payload, calls

    def test_429_waits_and_retries_the_same_request(self):
        payload, calls = self.run_with([FakeResponse(429, LIMITED), FakeResponse(429, LIMITED),
                                        FakeResponse(200, {"result": {"C_NO": "R1"}})])
        self.assertEqual(payload, {"result": {"C_NO": "R1"}})
        self.assertEqual(len(calls), 3)
        self.assertEqual(set(calls), {"https://www.safetyreport.go.kr/x/R1"})
        self.assertIn(120, self.sleeps)
        self.assertIn(180, self.sleeps)
        self.assertEqual(api_client.current_interval(), api_client.REQUEST_INTERVAL * 4)

    def test_persistent_429_returns_http_429_after_the_last_wait(self):
        n = len(api_client.RATE_LIMIT_WAITS) + 1
        payload, calls = self.run_with([FakeResponse(429, LIMITED) for _ in range(n)])
        self.assertEqual(payload, {"error": "HTTP 429"})
        self.assertEqual(len(calls), n)
        self.assertEqual([s for s in self.sleeps if s >= 120], list(api_client.RATE_LIMIT_WAITS))
        self.assertEqual(crawldetail_api.classify_fetch_error(payload["error"]), "retryable")

    def test_requests_are_spaced(self):
        self.run_with([FakeResponse(200, {"result": {}})])
        self.run_with([FakeResponse(200, {"result": {}})])
        spaced = [s for s in self.sleeps if 0 < s < 120]
        self.assertEqual(len(spaced), 1)
        self.assertLessEqual(spaced[0], api_client.REQUEST_INTERVAL)

    def test_interval_recovers_after_steady_success(self):
        self.run_with([FakeResponse(429, LIMITED), FakeResponse(200, {"result": {}})])
        self.assertEqual(api_client.current_interval(), api_client.REQUEST_INTERVAL * 2)
        for _ in range(100):
            self.run_with([FakeResponse(200, {"result": {}})])
        self.assertEqual(api_client.current_interval(), api_client.REQUEST_INTERVAL)

    def test_detail_crawl_stops_and_keeps_the_rest_for_next_run(self):
        sink = {}
        with mock.patch.object(crawldetail_api.direct_login, "make_authorized_session", return_value=(object(), None)), \
             mock.patch.object(crawldetail_api, "get_authorized_json", return_value=({"error": "HTTP 429"}, None)) as fetch, \
             mock.patch.object(crawldetail_api.logger.LoggerFactory, "logbot", mock.Mock()):
            out = list(crawldetail_api.crawl_details(report_ids=["A", "B", "C"], status_sink=sink))
        self.assertEqual(out, [])
        self.assertEqual(fetch.call_count, 1)  # B·C 는 두드리지 않았다
        self.assertEqual({k: v[0] for k, v in sink.items()}, {"A": "retryable", "B": "retryable", "C": "retryable"})


if __name__ == "__main__":
    unittest.main()
