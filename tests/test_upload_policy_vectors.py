"""UC-1 공통 판정 벡터(contracts/upload-control/vectors.json) — 모바일 test/community/upload_policy_vectors_test.dart 와 같은 파일·같은 결과."""
import hashlib
import json
import unittest
from datetime import datetime
from pathlib import Path

from services import community_upload_policy as policy

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / "contracts" / "upload-control"
VECTORS = json.loads((FOLDER / "vectors.json").read_text(encoding="utf-8"))
NOW = datetime.fromisoformat(VECTORS["now"].replace("Z", "+00:00"))


class UploadPolicyVectorTest(unittest.TestCase):
    def test_contract_files_match_the_manifest(self):
        for line in (FOLDER / "MANIFEST.sha256").read_text(encoding="utf-8").splitlines():
            digest, _, name = line.partition("  ")
            self.assertEqual(hashlib.sha256((FOLDER / name).read_bytes()).hexdigest(), digest, name)

    def test_backoff(self):
        for v in VECTORS["backoff"]:
            with self.subTest(v=v):
                self.assertAlmostEqual(policy.backoff_seconds(v["n"], v["u"]), v["seconds"], places=6)
                self.assertLessEqual(policy.backoff_seconds(v["n"], v["u"]), policy.BACKOFF_CAP_SECONDS)

    def test_retry_after(self):
        for v in VECTORS["retry_after"]:
            with self.subTest(name=v["name"]):
                self.assertEqual(policy.parse_retry_after(v["headers"], v["body"], NOW), v["hint"])

    def test_retry_delay(self):
        for v in VECTORS["retry_delay"]:
            with self.subTest(v=v):
                self.assertAlmostEqual(policy.retry_delay_seconds(v["n"], v["u"], v["hint"]), v["seconds"], places=6)

    def test_responses(self):
        for v in VECTORS["responses"]:
            with self.subTest(name=v["name"]):
                body = None if v["body"] is None else v["body"].encode("utf-8")
                got = policy.interpret_response(v["sent"], v["status"], v["headers"], body, NOW)
                exp = v["expect"]
                self.assertEqual(got.kind, exp["kind"])
                if exp["kind"] == "ack":
                    self.assertEqual(got.request_id, exp["request_id"])
                    self.assertEqual({k: e.outcome for k, e in got.events.items()}, exp["events"])
                    self.assertEqual(got.missing, exp["missing"])
                else:
                    self.assertEqual(got.error_class, exp["class"])
                    self.assertEqual(got.scope, exp.get("scope"))
                    if "hint" in exp:
                        self.assertEqual(got.hint, exp["hint"])
                    if "code" in exp:
                        self.assertEqual(got.code, exp["code"])
                    if "request_id" in exp:
                        self.assertEqual(got.request_id, exp["request_id"])


if __name__ == "__main__":
    unittest.main()
