"""상세 원본 정규 직렬화 계약 벡터(contracts/raw-detail-vectors.json, 2026-10-17 명세 1-1).

서버·모바일이 같은 입력에서 같은 정규 바이트와 sha 를 내야 한다.
이 시험은 서버 쪽(Python core/utils/raw_detail.py)을 벡터와 대조한다.
"""
import json
import unittest
from pathlib import Path

from core.utils import raw_detail

VECTORS = json.loads((Path(__file__).resolve().parents[1] / "contracts" / "raw-detail-vectors.json").read_text(encoding="utf-8"))


class RawDetailVectorTests(unittest.TestCase):
    def test_reporter_keys_match_the_contract(self):
        self.assertEqual(list(raw_detail.REPORTER_KEYS), VECTORS["reporter_keys"])

    def test_canonical_bytes_sha_and_size_match_vectors(self):
        for case in VECTORS["cases"]:
            with self.subTest(name=case["name"]):
                payload = raw_detail.canonical_bytes(case["input"])
                self.assertEqual(payload, case["canonical"].encode("utf-8"))
                self.assertEqual(raw_detail.sha256_hex(payload), case["sha256"])
                self.assertEqual(len(payload), case["size"])

    def test_reporter_keys_are_removed_only_inside_result(self):
        cleaned = raw_detail.sanitize_envelope(VECTORS["cases"][0]["input"])
        for key in VECTORS["reporter_keys"]:
            self.assertNotIn(key, cleaned["result"])
        # 공무원 담당자 이름·연락처, 본문, 첨부 정보는 남는다
        answer = cleaned["result"]["answers"][0]
        self.assertEqual(answer["C_MANAGE_MAN"], "김담당")
        self.assertEqual(answer["C_MANAGE_MAN_PHONE"], "02-123-4567")
        # 원본 입력은 바꾸지 않는다
        self.assertIn("C_NAME", VECTORS["cases"][0]["input"]["result"])

    def test_compress_decompress_verify_round_trip(self):
        for case in VECTORS["cases"]:
            with self.subTest(name=case["name"]):
                payload = raw_detail.canonical_bytes(case["input"])
                blob = raw_detail.compress(payload)
                self.assertEqual(raw_detail.verify(blob, case["sha256"]), payload)

    def test_verify_rejects_a_corrupt_row(self):
        payload = raw_detail.canonical_bytes(VECTORS["cases"][0]["input"])
        blob = raw_detail.compress(payload)
        with self.assertRaises(ValueError):
            raw_detail.verify(blob, "0" * 64)
        with self.assertRaises(ValueError):
            raw_detail.verify(b"not-zlib", raw_detail.sha256_hex(payload))

    def test_parser_ignores_the_reporter_keys(self):
        """파서는 신고자 키 5개를 쓰지 않는다: 제거 전후 파싱 결과가 같다(명세 1-3)."""
        import services.parser as doc_parser

        base = {"C_NO": 1, "C_A_CONTENTS": "본문", "answers": []}
        full = dict(base, C_NAME="홍길동", C_EMAIL="a@b.c", C_PHONE="01012345678",
                    C_PHONE2="010-1234-5678", C_ID="fixtureid1")
        self.assertEqual(doc_parser.parse_json_details(dict(full)), doc_parser.parse_json_details(dict(base)))


if __name__ == "__main__":
    unittest.main()
