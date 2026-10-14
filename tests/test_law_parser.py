import json
from pathlib import Path
import unittest
from services.parser import _violation_law
from services.law_values import canonical_law, primary_law

class LawParserTests(unittest.TestCase):
    def test_shared_cases(self):
        data = json.loads((Path(__file__).parents[1] / 'contracts/law-parser-vectors.json').read_text())
        for case in data['cases']:
            with self.subTest(case=case['name']):
                self.assertEqual(canonical_law(primary_law(_violation_law(case['answer']))), case['primary'])

    def test_legacy_format_retained(self):
        self.assertEqual(_violation_law('도로교통법 제13조3항 위반한 사실이 확인되었습니다.'), '도로교통법 제13조3항')
