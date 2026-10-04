"""EO R-11: 상세 파서 단계(본문·위치·첨부 → 선택 답변 → 판정 → 조립). 전체 결과는 tests/test_parser_vectors.py(모바일과 같은 벡터)."""

import copy
import json
import pathlib
import unittest

from services import parser

ROOT = pathlib.Path(__file__).resolve().parents[1]
VECTORS = json.loads((ROOT / "contracts" / "parser-vectors.json").read_text(encoding="utf-8"))


def _inputs():
    cases = VECTORS.get("cases", VECTORS) if isinstance(VECTORS, dict) else VECTORS
    for case in cases:
        for key in ("input", "result_data", "detail", "payload"):
            if isinstance(case, dict) and isinstance(case.get(key), dict):
                yield case[key]
                break


class ParserStagesTest(unittest.TestCase):
    def test_parsing_does_not_change_the_input(self):
        for data in _inputs():
            before = copy.deepcopy(data)
            parser.parse_json_details(data)
            self.assertEqual(data, before)

    def test_selected_answer_is_the_last_one_and_keeps_agency_code_text(self):
        answers = [{"C_MANAGER_TYPE_NM": "수용", "C_MANAGE_ORG": "1111111"},
                   {"C_MANAGER_TYPE_NM": "진행", "C_R_PROC_STAT_NM": "불수용", "C_MANAGE_ORG": "0012345",
                    "C_MANAGE_CONTENTS": "<p>도로교통법 제 32 조 위반</p>", "C_DATE": "2026-09-10 11:00:00"}]
        selected = parser._select_answer(answers, "진행")
        self.assertEqual(selected["processing_status"], "불수용")
        self.assertEqual(selected["processing_agency_code"], "0012345")
        self.assertEqual(selected["response_date"], "2026-09-10")
        self.assertEqual(parser._violation_law(selected["processing_content"]), "도로교통법 제32조")
        self.assertEqual(parser._select_answer([], "진행")["processing_finish"], "N")

    def test_final_status_order(self):
        self.assertEqual(parser._final_status("수용", "취하", False), "취하")
        self.assertEqual(parser._final_status(" 수용 ", "진행", False), "수용")
        self.assertEqual(parser._final_status("처리중", "답변완료", False), "답변완료")
        self.assertEqual(parser._final_status("", "진행", True), "보완요청")
        self.assertEqual(parser._final_status("", "진행", False), "처리중")

    def test_completed_supplement_replaces_location_with_its_own_coordinates_only(self):
        data = {"RN_ADRES": "원래 주소", "C_A_LAT": "37.1", "C_A_LOT": "127.1", "SPLMNT_CMPTN_DT": "2026-09-01",
                "SPLMNT_CMPTN_YN": "Y", "SPLMNT_RN_ADRES": "보완 주소", "SPLMNT_VHRNO": "12가 3456",
                "SPLMNT_DEVEL_DATE": "20260901", "SPLMNT_DEVEL_TIME": "0930"}
        located = parser._apply_completed_supplement(data, parser._extract_body(data))
        self.assertEqual((located["violation_location"], located["car_number"], located["occurrence_date"],
                          located["occurrence_time"]), ("보완 주소", "12가3456", "2026-09-01", "09:30"))
        self.assertEqual(located["violation_latitude"], parser._official_coordinates(data, "SPLMNT_")[0])

    def test_penalty_prefers_fines_and_points_from_the_answer(self):
        self.assertEqual(parser._penalty("", "수용", "범칙금 30,000원, 벌점 15점"), ("범칙금: 30,000원", "벌점: 15점"))
        self.assertEqual(parser._penalty("", "수용", "과태료 40,000원"), ("과태료: 40,000원", ""))
        self.assertEqual(parser._penalty("불법주정차신고", "수용", ""), ("과태료", ""))
        self.assertEqual(parser._penalty("불법주정차신고", "불수용", ""), ("", ""))


if __name__ == "__main__":
    unittest.main()
