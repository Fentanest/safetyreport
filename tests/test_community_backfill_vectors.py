"""재전송·사진 보충과 일반 캡처의 추정 판정 공통 사례(tests/fixtures/community_backfill_vectors.json).

모바일 test/community/backfill_vectors_test.dart 가 바이트 동일한 같은 파일을 읽어 같은 결과를 확인한다.
"""
import json
import unittest
from pathlib import Path

from services import community_capture as cap
from services import fine_estimate

VECTORS = json.loads((Path(__file__).parent / "fixtures" / "community_backfill_vectors.json").read_text(encoding="utf-8"))


def _adapter(row: dict) -> dict:
    return cap._resend_adapter_input(row)


def _journal_payload(case: dict) -> dict:
    row = {**VECTORS["base_personal"], **case["personal"], "범칙금_과태료": case["journal_raw"]}
    payload = cap.build_payload(_adapter(row))
    override = case["journal_override"]
    if "confirmed_won" in override:
        payload["amount"] = {**payload["amount"], "confirmed_won": override["confirmed_won"]}
    if "fine_estimate" in override:
        payload.pop("fine_estimate", None)
        if override["fine_estimate"] is not None:
            payload["fine_estimate"] = override["fine_estimate"]
    return payload


class BackfillVectorTests(unittest.TestCase):
    def test_rule_version_matches(self):
        self.assertEqual(VECTORS["rule_version"], fine_estimate.RULE_VERSION)

    def test_cases(self):
        for case in VECTORS["cases"]:
            with self.subTest(case=case["name"]):
                row = {**VECTORS["base_personal"], **case["personal"]}
                expected = case["expected"]
                captured = cap.build_payload(_adapter(row))
                self.assertEqual({
                    "confirmed_won": captured["amount"]["confirmed_won"],
                    "disposition": captured["disposition"],
                    "violation_law": captured["violation_law"],
                    "fine_estimate": captured.get("fine_estimate"),
                }, expected["capture"])
                journal = _journal_payload(case)
                backfilled = cap._backfill_payload(journal, _adapter(row))
                self.assertEqual(backfilled.get("fine_estimate"), expected["backfill_fine_estimate"])
                # 추정 외 필드는 journal 그대로(amount 포함 — 확정 금액은 다음 실시간 수집이 갱신).
                rest = dict(backfilled)
                rest.pop("fine_estimate", None)
                journal.pop("fine_estimate", None)
                self.assertEqual(rest, journal)

    def test_backfill_is_idempotent(self):
        for case in VECTORS["cases"]:
            with self.subTest(case=case["name"]):
                row = {**VECTORS["base_personal"], **case["personal"]}
                once = cap._backfill_payload(_journal_payload(case), _adapter(row))
                self.assertEqual(cap._backfill_payload(once, _adapter(row)), once)


if __name__ == "__main__":
    unittest.main()
