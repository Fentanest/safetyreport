import json
from pathlib import Path
import unittest
from services.law_values import matches_laws, matches_law_keys, canonical_law, read_laws, available_laws, law_memberships, primary_law
from services.parser import _violation_law
from services.fine_estimate import extract_fine_amount, estimate_if_unknown

class LawValueTests(unittest.TestCase):
    def test_shared_search(self):
        cases=json.loads((Path(__file__).parents[1]/'contracts/law-search-vectors.json').read_text())['cases']
        for c in cases:
            with self.subTest(c['name']): self.assertEqual(matches_laws(c['value'],c['query']),c['expected'])

    def test_shared_canonical_and_key_membership(self):
        vectors=json.loads((Path(__file__).parents[1]/'contracts/law-search-vectors.json').read_text())
        for c in vectors['canonical_cases']:
            with self.subTest('canonical '+c['name']): self.assertEqual(canonical_law(c['value']),c['expected'])
        for c in vectors['key_cases']:
            with self.subTest('key '+c['name']): self.assertEqual(matches_law_keys(c['value'],c['query']),c['expected'])

    def test_shared_stats_bars_equal_key_membership(self):
        # 막대 건수(소속 집계) == 같은 filter 의 저장 키 소속 판정 행 수. Dart local_statistics 도 같은 벡터를 쓴다.
        from services.stats.metrics import _overview_violation_laws
        import pandas as pd
        for c in json.loads((Path(__file__).parents[1]/'contracts/law-search-vectors.json').read_text())['stats_cases']:
            bars=_overview_violation_laws(pd.DataFrame({'위반법규':c['values']}))
            self.assertEqual(bars,c['expected'])
            for bar in bars:
                with self.subTest(c['name']+' '+bar['name']):
                    self.assertEqual(sum(matches_law_keys(v,bar['filter']) for v in c['values']),bar['count'])

    def test_multiple_parser_to_search_and_dedup(self):
        answer='도로교통법 제32조, 제33조, 제34조 위반. 신고된 차량에 대해 과태료부과 처분을 하였습니다.'
        value=_violation_law(answer)
        self.assertEqual(read_laws(value),('도로교통법 제32조','도로교통법 제33조','도로교통법 제34조'))
        self.assertEqual(read_laws(value+'\n도로교통법 제33조'),read_laws(value))
        for q in read_laws(value): self.assertTrue(matches_laws(value,q))
        self.assertEqual(available_laws([value,value]),list(read_laws(value)))

    def test_no_wrong_basis_added(self):
        value=_violation_law('도로교통법 제23조를 위반한 사실이 확인되어 차량 소유주에게 과태료 40,000원을 부과하고자 합니다. 추가로 명확하게 판단하기 어려워 도로교통법 제5조로 단속이 어렵습니다.')
        self.assertEqual(read_laws(value),('도로교통법 제23조',))

    def test_hierarchical_memberships_count_once(self):
        value='도로교통법 제13조제1항\n도로교통법 제13조제3항'
        universe=set(read_laws(value))|{'도로교통법 제13조'}
        self.assertEqual(len(law_memberships(value,universe)),3)
        self.assertEqual(law_memberships(value,universe).count('도로교통법 제13조'),1)

    def test_secondary_law_does_not_change_money(self):
        row={'category':'traffic','차량번호':'12가3456','위반법규':'도로교통법 제23조','ssnpc_cd':'TV','ssnpc_type':'09'}
        multi=dict(row,위반법규=row['위반법규']+'\n도로교통법 제5조')
        for text in ['과태료: 40,000원','과태료: 7,0000원','과태료']:
            self.assertEqual(extract_fine_amount(text,multi),extract_fine_amount(text,row))
            self.assertEqual(estimate_if_unknown(text,multi),estimate_if_unknown(text,row))
        self.assertEqual(primary_law(multi['위반법규']),row['위반법규'])
