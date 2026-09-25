"""파서 공통 기대값 (contracts/parser-vectors.json — 모바일 test/storage/parser_vectors_test.dart 와 같은 파일).

서버 services/parser.parse_json_details 결과를 DB 열 이름으로 옮겨 기대값과 비교한다. 적힌 키만 본다.
"""
import json
import unittest
from pathlib import Path

from services import parser

VECTORS = json.loads((Path(__file__).resolve().parents[1] / "contracts" / "parser-vectors.json").read_text(encoding="utf-8"))


def as_columns(detail: dict) -> dict:
    d = parser.parse_json_details(detail)
    title = d["title_fields"]
    sup = d["supplement_summary"]
    return {
        "신고번호": title["신고번호"], "신고명": title["신고명"], "신고일": title["신고일"], "상태": title["상태"],
        "만족도조사여부": title["만족도조사여부"], "별점": title.get("별점"),
        "entry_value": d["entry_value"], "처리상태": d["processing_status"], "종결여부": d["processing_finish"],
        "처리기관": d["processing_agency"], "담당자": d["person_in_charge"], "답변일": d["response_date"],
        "처리내용": d["processing_content"], "위반법규": d["violation_law"], "범칙금_과태료": d["penalty_amount"], "벌점": d["penalty_points"],
        "차량번호": d["car_number"], "발생일자": d["occurrence_date"], "발생시각": d["occurrence_time"], "위반장소": d["violation_location"],
        "신고내용": d["report_content"], "첨부사진": d["attached_photos"], "첨부파일": d["attachment_files"], "지도": d["map_image"],
        "raw_content": d["raw_content"],
        "보완횟수": int(sup.get("count") or 0), "보완_미응답": sup.get("is_open") or "N", "보완_요청자": sup.get("requester") or "",
        "보완_요청_내용": sup.get("request_text") or "", "보완_신고자_의견": sup.get("reporter_opinion") or "",
    }


class ParserVectorTests(unittest.TestCase):
    def test_every_vector(self):
        for case in VECTORS["cases"]:
            with self.subTest(case=case["name"]):
                got = as_columns(case["detail"])
                for key, want in case["expect"].items():
                    self.assertEqual(got[key], want, f"{case['name']} · {key}")


if __name__ == "__main__":
    unittest.main()
