import json
from pathlib import Path
import unittest
from services.parser import _violation_law
from services.law_values import canonical_law, primary_law
from services import law_citations
from services.law_selection import choose

ROOT = Path(__file__).parents[1]


class LawParserTests(unittest.TestCase):
    def test_shared_cases(self):
        data = json.loads((ROOT / 'contracts/law-parser-vectors.json').read_text())
        for case in data['cases']:
            with self.subTest(case=case['name']):
                value = _violation_law(case['answer'])
                self.assertEqual(canonical_law(primary_law(value)), case['primary'])
                if 'selected' in case:
                    self.assertEqual(choose(case['answer'])['selected'], case['selected'])
                if 'value' in case:
                    self.assertEqual(value, case['value'])

    def test_legacy_format_retained(self):
        self.assertEqual(_violation_law('도로교통법 제13조3항 위반한 사실이 확인되었습니다.'), '도로교통법 제13조3항')

    def test_recognition_contract_matches_code(self):
        data = json.loads((ROOT / 'contracts/law-recognition.json').read_text())
        self.assertEqual(law_citations.ALIASES, data['aliases'])
        self.assertEqual(law_citations.article.pattern, data['article_pattern'])
        self.assertEqual(law_citations.short.pattern, data['short_pattern'])

    def test_digit_boundary_kept_only_between_digits(self):
        text, mapping = law_citations.normalized_law_copy('도로교통법 제 32 조의2 \u3000 1항 및 제33조')
        self.assertEqual(text, '도로교통법제32조의2 1항및제33조')
        self.assertEqual(len(text), len(mapping))
        space = text.index(' ')
        self.assertEqual(mapping[space], (14, 15))  # first whitespace of the run

    def test_inherited_paragraph_uses_ascii_digits_only(self):
        keys = lambda t: [r['key'] for r in law_citations.parse_laws(t)[0]]
        self.assertEqual(keys('도로교통법 제32조를 위반하여 같은 조 제3항에 따라'),
                         ['도로교통법 제32조', '도로교통법 제32조제3항'])
        for digit in ('\uff13', '\u0663'):
            self.assertEqual(keys(f'도로교통법 제32조를 위반하여 같은 조 제{digit}항에 따라'), ['도로교통법 제32조'])

    def test_entity_rule_matches_dart_and_browser(self):
        decode = law_citations.decode_entity
        self.assertEqual(decode('&nbsp;'), '\xa0')
        self.assertEqual(decode('&notit;'), '\xacit;')
        self.assertEqual(decode('&#1;'), '\x01')
        self.assertEqual(decode('&#x10FFFF;'), '\U0010ffff')
        self.assertEqual(decode('&#0;'), '\ufffd')
        self.assertEqual(decode('&#x80;'), '\u20ac')
        self.assertEqual(decode('&#xD800;'), '\ufffd')
        self.assertEqual(decode('&#' + '9' * 5000 + ';'), '\ufffd')
        self.assertEqual(law_citations.decode_entities('a&nbspb&amp;c'), 'a&nbspb&c')


if __name__ == '__main__':
    unittest.main()
