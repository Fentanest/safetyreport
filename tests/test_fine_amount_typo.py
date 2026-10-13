"""PC/모바일 공유 벡터: 숫자, 실제 통계 집계, 업로드 payload, 원문 보존."""
import json
import hashlib
from pathlib import Path
import unittest

import pandas as pd
from services import community_capture as capture, fine_estimate
from services.stats import metrics
from web.routers.data import _with_fine_estimates

VECTORS = json.loads((Path(__file__).parent / 'fixtures/fine_amount_typo_vectors.json').read_text())


def adapter(case):
    r = case['record']
    return dict(processing_status='수용', penalty_amount=case['raw'], category=r.get('category'),
                entry_value=r.get('entry_value'), ssnpc_cd=r.get('ssnpc_cd'), ssnpc_type=r.get('ssnpc_type'),
                car_number=r.get('차량번호'), report_name=r.get('신고명'), violation_law=r.get('위반법규'),
                occurrence_time=r.get('발생시각'), photo_first_at=r.get('사진_첫촬영'), photo_last_at=r.get('사진_끝촬영'))


class FineAmountTypoTest(unittest.TestCase):
    def test_shared_table_hash_and_vehicle_amounts(self):
        table_path = Path(fine_estimate.__file__).parent / 'data/traffic_fine_table.json'
        self.assertEqual(hashlib.sha256(table_path.read_bytes()).hexdigest(), VECTORS['table_sha256'])
        for case in VECTORS['table_cases']:
            with self.subTest(case):
                self.assertEqual(fine_estimate.legal_fine_amounts({
                    '위반법규': case['law'], '차량번호': case['plate'],
                }), set(case['amounts']))

    def test_article_changes_unknown_target_without_changing_estimator(self):
        r = {'category': 'traffic', '신고명': '끼어들기 금지 위반',
             '위반법규': '도로교통법 제23조', '차량번호': '63너1234'}
        self.assertIsNone(fine_estimate.classify(r))
        self.assertIsNone(fine_estimate.estimate(r))
        self.assertFalse(fine_estimate.is_fine_amount_unknown('과태료: 40,0000원', r))
        r.update(category='parking', 위반법규='도로교통법 제32조')
        self.assertEqual(fine_estimate.estimate(r)['amount'], 40000)
        self.assertIsNone(fine_estimate.estimate_if_unknown('과태료: 120,0000원', r))

    def test_shared_rule_version(self):
        self.assertEqual(fine_estimate.RULE_VERSION, VECTORS['rule_version'])

    def test_four_path_vectors(self):
        for c in VECTORS['cases']:
            with self.subTest(c['name']):
                r = dict(c['record'], **{'범칙금_과태료': c['raw']})
                original = dict(r)
                self.assertEqual(fine_estimate.extract_fine_amount(c['raw'], r), c['stats_won'])
                self.assertEqual(capture._parse_amount(c['raw'], r), (c['confirmed_won'], c['kind']))
                p = capture.build_payload(adapter(c))
                self.assertEqual(p['amount']['confirmed_won'], c['confirmed_won'])
                prepared = metrics._prepare_metrics(pd.DataFrame([r]))
                self.assertEqual(prepared['_metric_fine_amount'].iloc[0], c['stats_won'])
                self.assertEqual(metrics._fine_amounts(pd.DataFrame([r])).iloc[0], c['stats_won'])
                self.assertEqual(r, original)
                self.assertEqual(fine_estimate.corrected_fine_text(c['raw'], r), c['corrected_text'])
                web_row = _with_fine_estimates([dict(r)])[0]
                self.assertEqual(web_row['fine_corrected_text'], c['corrected_text'])
                self.assertEqual(web_row['범칙금_과태료'], c['raw'])
                estimate = fine_estimate.estimate_if_unknown(c['raw'], r)
                self.assertEqual('fine_estimate' in p, estimate is not None)
                if 'estimated_won' in c:
                    self.assertEqual(estimate['amount'], c['estimated_won'])
                    self.assertEqual(estimate['rule'], c['estimated_rule'])
                    self.assertEqual(p['fine_estimate'], {
                        'won': c['estimated_won'], 'rule': c['estimated_rule'],
                        'rule_version': VECTORS['rule_version'],
                    })
                    self.assertEqual(prepared['_metric_estimated_amount'].iloc[0], c['estimated_won'])
                    self.assertEqual(metrics._estimated_fine_totals(pd.DataFrame([r]))['estimated_fine_amount'], c['estimated_won'])
                    self.assertEqual(web_row['fine_estimate_amount'], c['estimated_won'])
                self.assertEqual(metrics._count_fine_amount_unknown(prepared), int('과태료' in c['raw'] and c['stats_won'] == 0))

    def test_mixed_nullable_context_rows(self):
        cases = VECTORS['cases']
        frame = pd.DataFrame([dict(c['record'], **{'범칙금_과태료': c['raw']}) for c in cases])
        self.assertEqual(metrics._fine_amounts(frame).tolist(), [c['stats_won'] for c in cases])
        self.assertEqual(metrics._prepare_metrics(frame)['_metric_fine_amount'].tolist(), [c['stats_won'] for c in cases])

    def test_ambiguity_and_no_match(self):
        for c in VECTORS['candidate_cases']:
            with self.subTest(c['name']):
                self.assertEqual(fine_estimate.parse_amount_number(c['number'], c['legal_amounts']), c['confirmed_won'])

    def test_corrected_display_only_and_hash_changes_once(self):
        case = VECTORS['cases'][0]
        self.assertEqual(fine_estimate.corrected_fine_text(case['raw'], case['record']), '과태료: 40,000원')
        self.assertIsNone(fine_estimate.corrected_fine_text('과태료: 400,000원', case['record']))
        current = capture.build_payload(adapter(case))
        previous = dict(current, amount=dict(current['amount'], confirmed_won=None))
        previous['fine_estimate'] = {'won': 40000, 'rule': 'parking', 'rule_version': fine_estimate.RULE_VERSION}
        self.assertNotEqual(capture.payload_sha256(previous), capture.payload_sha256(current))
        self.assertEqual(capture.decide_event({'eligible': True, 'payload_sha256': capture.payload_sha256(previous)}, current), 'completed_observation')
        self.assertIsNone(capture.decide_event({'eligible': True, 'payload_sha256': capture.payload_sha256(current)}, current))
        self.assertEqual(capture.PARSER_VERSION, 'pc-parser-7')
