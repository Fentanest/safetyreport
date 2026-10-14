import json
from pathlib import Path
import unittest

from services.case_fine_supplement import supplement_case_fine
from services.parser import parse_json_details

CASES = json.loads((Path(__file__).parents[1] / 'contracts/case-fine-supplement-vectors.json').read_text())['cases']


class CaseFineSupplementTests(unittest.TestCase):
    def test_shared_observed_excerpts(self):
        for case in CASES:
            with self.subTest(case['name']):
                self.assertEqual(supplement_case_fine(case['status'], case['penalty'], case['text']),
                                 (case['expected_status'], case['expected_penalty']))

    def test_existing_disposition_and_lifecycle_are_preserved(self):
        text = CASES[0]['text']
        for status in ('수용', '불수용', '기타'):
            for penalty in ('과태료', '과태료: 0원', '과태료: 100,000원', '범칙금: 30,000원', '경고'):
                with self.subTest(status=status, penalty=penalty):
                    self.assertEqual(supplement_case_fine(status, penalty, text), (status, penalty))
        for status in ('취하', '이송', '처리중', '진행', '검토중', '보완요청', ''):
            for penalty in ('', '미확인'):
                with self.subTest(status=status, penalty=penalty):
                    self.assertEqual(supplement_case_fine(status, penalty, text), (status, penalty))

    def test_full_parser_adds_after_final_status_without_mutating_raw_status(self):
        text = CASES[0]['text']
        for status, raw in (('수용', 10), ('불수용', 14)):
            payload = {'C_NOW': raw, 'C_APP_GUBUN_NM': '자동차·교통위반-기타 자동차 안전기준 위반',
                       'answers': [{'C_MANAGER_TYPE_NM': status, 'C_MANAGE_CONTENTS': text}]}
            original = json.dumps(payload, ensure_ascii=False)
            parsed = parse_json_details(payload)
            self.assertEqual(parsed['processing_status'], status)
            self.assertEqual(parsed['penalty_amount'], '과태료')
            self.assertEqual(parsed['penalty_points'], '')
            self.assertEqual(parsed['processing_finish'], 'Y')
            self.assertEqual(json.dumps(payload, ensure_ascii=False), original)
        structured = next(c['text'] for c in CASES if '처리결과' in c['text'] and c['expected_status'] == '수용')
        parsed = parse_json_details({'C_NOW': 15, 'C_APP_GUBUN_NM': '자동차·교통위반',
                                     'answers': [{'C_MANAGER_TYPE_NM': '기타', 'C_MANAGE_CONTENTS': structured}]})
        self.assertEqual((parsed['progress_status'], parsed['processing_status'], parsed['penalty_amount']),
                         ('기타', '수용', '과태료'))
        withdrawn = parse_json_details({'C_NOW': 20, 'C_APP_GUBUN_NM': '불법주정차신고',
                                        'answers': [{'C_MANAGER_TYPE_NM': '수용', 'C_MANAGE_CONTENTS': text}]})
        self.assertEqual((withdrawn['processing_status'], withdrawn['penalty_amount']), ('취하', ''))


if __name__ == '__main__':
    unittest.main()
