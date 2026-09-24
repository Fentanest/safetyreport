"""추정 과태료 규칙(services/fine_estimate.py) — 공유 테스트 벡터(tests/fixtures/fine_estimate_vectors.json)로 검증한다.
같은 파일을 모바일 레포도 가진다. 규칙을 바꾸면 벡터와 RULE_VERSION 을 양쪽에서 함께 바꾼다."""
import json
import pathlib
import unittest

from services import fine_estimate

VECTORS = json.loads((pathlib.Path(__file__).parent / "fixtures" / "fine_estimate_vectors.json").read_text(encoding="utf-8"))


class FineEstimateVectorTest(unittest.TestCase):
    def test_rule_version_matches_vectors(self):
        self.assertEqual(fine_estimate.RULE_VERSION, VECTORS["rule_version"])

    def test_plate_classification(self):
        for case in VECTORS["plates"]:
            with self.subTest(plate=case["plate"]):
                self.assertEqual(fine_estimate.vehicle_kind(case["plate"]), case["kind"])
                self.assertEqual(fine_estimate.fine_class(case["plate"]), case["class"])

    def test_estimate_cases(self):
        for case in VECTORS["cases"]:
            with self.subTest(case=case["name"]):
                self.assertEqual(fine_estimate.classify(case["record"]), case["rule"])
                result = fine_estimate.estimate(case["record"])
                self.assertEqual(result["amount"] if result else None, case["amount"])


if __name__ == "__main__":
    unittest.main()
