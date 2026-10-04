"""상세 검색의 공통 순서를 유지한다. 동작 검증은 Playwright list-interactions가 맡는다."""

import unittest
from pathlib import Path
import re
from html import unescape

from jinja2 import Environment


TEMPLATES = Path(__file__).resolve().parents[1] / "web" / "templates"


class SearchFormOrderTest(unittest.TestCase):
    def _labels(self, name, form_marker):
        source = (TEMPLATES / name).read_text(encoding="utf-8")
        Environment().parse(source)
        self.assertIn(form_marker, source)
        form = source.split(form_marker, 1)[1].split("</form>", 1)[0]
        # 라벨에 for= 등 다른 속성이 붙어도 순서를 읽는다(기술일지 F-06 라벨 연결)
        labels = re.findall(r'<label class="form-label[^"]*"[^>]*>(.*?)</label>', form, re.DOTALL)
        return form, [unescape(re.sub(r"<[^>]*>", "", label)).strip() for label in labels]

    def test_report_list_follows_requested_order(self):
        form, labels = self._labels("data_table.html", 'id="advSearchForm"')
        self.assertEqual(labels[:8], [
            "차량번호", "신고번호", "ID", "처리기관", "담당자",
            "범칙금/과태료", "처리상태", "별점",
        ])
        self.assertEqual(form.count('id="searchId"'), 1)

    def test_statistics_keeps_only_its_available_fields_in_order(self):
        _, labels = self._labels("stats.html", 'action="/stats"')
        self.assertEqual(labels[:3], ["처리기관", "신고명", "위반장소"])


if __name__ == "__main__":
    unittest.main()
