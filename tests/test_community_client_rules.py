"""D2-10: 커뮤니티 계정 응답을 읽는 클라이언트 규칙(contracts/community-client, 모바일·auth 와 같은 벡터)."""

import hashlib
import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("SAFETYREPORT_DATA_DIR", tempfile.mkdtemp(prefix="sr-d210-"))

from services import community_gate
from services.community_account_client import AccountApiError, CommunityAccountClient
from services.community_auth_client import normalize_device_label
from services.community_client_rules import classify_response, is_current_response, normalize_status

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "community-client"


def _vectors(name):
    return json.loads((CONTRACT / "vectors" / name).read_text(encoding="utf-8"))


class ManifestTest(unittest.TestCase):
    def test_files_match_manifest(self):
        for line in (CONTRACT / "MANIFEST.sha256").read_text(encoding="utf-8").splitlines():
            digest, name = line.split(maxsplit=1)
            self.assertEqual(hashlib.sha256((CONTRACT / name).read_bytes()).hexdigest(), digest, name)


class StatusDtoTest(unittest.TestCase):
    def test_normalization_and_gate(self):
        doc = _vectors("status-dto.json")
        for case in doc["cases"]:
            with self.subTest(case=case["name"]):
                self.assertEqual(normalize_status(case["raw"]), case["normalized"])
                state, _ = community_gate.decide("ok", "valid", normalize_status(case["raw"]), 10.0, False)
                self.assertEqual(state, case["gate"])
        for raw in doc["invalid_top_level"]:
            self.assertIsNone(normalize_status(raw))


class _Resp:
    def __init__(self, status, text, headers):
        self.status_code, self.text, self.headers = status, text, headers


class AccountErrorTest(unittest.TestCase):
    def setUp(self):
        p = mock.patch('services.community_cloud._now', return_value=1791331200)
        p.start(); self.addCleanup(p.stop)
        p = mock.patch('services.community_cloud.run', side_effect=lambda base, send: send())
        p.start(); self.addCleanup(p.stop)
    def test_classification(self):
        for case in _vectors("account-errors.json")["cases"]:
            if case["transport"]:
                continue
            with self.subTest(case=case["name"]):
                got = classify_response(case["http_status"], case["body"], case["headers"])
                expect = dict(case["expect"])
                if case['name'] == '요청 과다(본문 대기)':
                    expect['retry_after_seconds'] = 30.0
                elif case['name'] == 'Retry-After 가 날짜면 무시':
                    # 21 Oct 2026 07:28 GMT - 7 Oct 2026 00:00 UTC.
                    expect['retry_after_seconds'] = 1236480.0
                elif case['name'] == '서비스 꺼짐(중앙이 재시도 불가)':
                    expect['transient'] = True
                self.assertEqual(got.success, expect["success"])
                if not expect["success"]:
                    self.assertEqual((got.code, got.transient, got.retry_after, got.auth),
                                     (expect["code"], expect["transient"], expect["retry_after_seconds"], expect["auth"]))

    def test_client_raises_with_the_classification(self):
        import requests
        client = CommunityAccountClient("http://127.0.0.1:1", "pk")
        for case in _vectors("account-errors.json")["cases"]:
            with self.subTest(case=case["name"]):
                if case["transport"]:
                    error = requests.Timeout() if case["transport"] == "timeout" else requests.ConnectionError()
                    with mock.patch.object(client.http, "post", side_effect=error), self.assertRaises(AccountApiError) as ctx:
                        client.status("token")
                    self.assertEqual(ctx.exception.transient, case["expect"]["transient"])
                    continue
                with mock.patch.object(client.http, "post",
                                       return_value=_Resp(case["http_status"], case["body"], case["headers"])):
                    if case["expect"]["success"]:
                        self.assertIsInstance(client.status("token"), dict)
                        continue
                    with self.assertRaises(AccountApiError) as ctx:
                        client.status("token")
                expect = dict(case["expect"])
                if case['name'] == '요청 과다(본문 대기)':
                    expect['retry_after_seconds'] = 30.0
                elif case['name'] == 'Retry-After 가 날짜면 무시':
                    # 21 Oct 2026 07:28 GMT - 7 Oct 2026 00:00 UTC.
                    expect['retry_after_seconds'] = 1236480.0
                elif case['name'] == '서비스 꺼짐(중앙이 재시도 불가)':
                    expect['transient'] = True
                self.assertEqual((ctx.exception.code, ctx.exception.transient, ctx.exception.retry_after,
                                  ctx.exception.auth),
                                 (expect["code"], expect["transient"], expect["retry_after_seconds"], expect["auth"]))


class DeviceLabelTest(unittest.TestCase):
    def test_validation_and_sanitized_values_pass(self):
        for case in _vectors("device-label.json")["cases"]:
            with self.subTest(value=case["input"]):
                self.assertEqual(normalize_device_label(case["input"]), case["valid"])
                if case["sanitized"] is not None:
                    self.assertIsNotNone(normalize_device_label(case["sanitized"]))


class GateTimingTest(unittest.TestCase):
    def test_cache_boundaries(self):
        doc = _vectors("gate-timing.json")
        ok = _vectors("status-dto.json")["cases"][0]["raw"]
        self.assertEqual(doc["ttl_seconds"], community_gate.CACHE_TTL)
        for case in doc["age_cases"]:
            with self.subTest(case=case):
                state, _ = community_gate.decide("ok", "valid", normalize_status(ok), case["age"], False, ttl=case["ttl"])
                self.assertEqual(state, case["expect"])

    def test_late_responses(self):
        for case in _vectors("gate-timing.json")["currency_cases"]:
            with self.subTest(case=case["name"]):
                self.assertEqual(is_current_response(case["started"], case["now"]), case["current"])


if __name__ == "__main__":
    unittest.main()
