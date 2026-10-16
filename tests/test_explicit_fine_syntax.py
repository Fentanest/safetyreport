import json
from pathlib import Path
import unittest
from services import parser, fine_estimate
from services.fine_amount_syntax import explicit_fine_number

VECTORS = json.loads((Path(__file__).parents[1] / 'contracts/explicit-fine-vectors.json').read_text())['cases']
RECORD = {'category': 'parking', 'entry_value': '불법주정차신고-기타 불법주정차', '위반법규': '도로교통법 제32조', '차량번호': '12가3456'}

class ExplicitFineSyntaxTests(unittest.TestCase):
    def test_shared_vectors_and_existing_number_parser(self):
        for case in VECTORS:
            with self.subTest(case['name']):
                number = explicit_fine_number(case['text'])
                self.assertEqual(number, case['number'])
                raw = f'과태료: {number}원' if number is not None else '과태료'
                self.assertEqual(fine_estimate.extract_fine_amount(raw, RECORD), case['amount'])
                estimate = fine_estimate.estimate_if_unknown(raw, RECORD)
                self.assertEqual(estimate is None, case['amount'] > 0)

    def test_real_parser_and_shared_disposition_contract(self):
        for case in VECTORS:
            with self.subTest(case['name']):
                detail = {'C_NOW': 10, 'C_APP_GUBUN_NM': RECORD['entry_value'],
                          'answers': [{'C_MANAGER_TYPE_NM': '수용', 'C_MANAGE_CONTENTS': case['text']}]}
                got = parser.parse_json_details(detail)
                expected = f"과태료: {case['number']}원" if case['number'] is not None else '과태료'
                if case['name'] == 'penalty distinct':
                    self.assertEqual(got['penalty_amount'], '범칙금: 30,000원')
                    self.assertEqual(got['penalty_points'], '벌점: 15점')
                else:
                    self.assertEqual(got['penalty_amount'], expected)
                self.assertEqual(got['processing_status'], '수용')

    def test_no_sentence_based_disposition_introduced(self):
        self.assertEqual(parser._penalty('자동차·교통위반-기타 자동차 안전기준 위반', '수용',
                         '해당 차량에 과태료를 부과할 예정입니다.'), ('', ''))

    def test_confirmed_amount_not_estimated_in_statistics_or_upload(self):
        import pandas as pd
        from services.stats.metrics import _prepare_metrics
        from services import community_capture
        raw = parser._penalty(RECORD['entry_value'], '수용', '과태료: 10만원')[0]
        r = dict(RECORD, 범칙금_과태료=raw)
        metrics = _prepare_metrics(pd.DataFrame([r]))
        self.assertEqual(metrics['_metric_fine_amount'].iloc[0], 100000)
        self.assertEqual(metrics['_metric_estimated_amount'].iloc[0], 0)
        self.assertEqual(community_capture._parse_amount(raw, r), (100000, 'fine'))

    def test_huge_digit_runs_are_not_amounts(self):
        # Beyond Python's int() digit limit; must not raise and must match Dart.
        for text in ('과태료 ' + '1' * 5000 + '만원', '과태료 ' + '1' * 5000 + '원',
                     '과태료 40,000원, 안내 ' + '9' * 5000 + '원'):
            self.assertIsNone(explicit_fine_number(text))
        detail = {'C_NOW': 10, 'C_APP_GUBUN_NM': RECORD['entry_value'],
                  'answers': [{'C_MANAGER_TYPE_NM': '수용', 'C_MANAGE_CONTENTS': '과태료 ' + '1' * 5000 + '만원'}]}
        self.assertEqual(parser.parse_json_details(detail)['penalty_amount'], '과태료')
