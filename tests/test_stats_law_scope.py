"""통계 법규 선택지 범위 — 모바일 computeStats 와 같은 규칙(2026-09-25 서버↔모바일 계산 동등성 검사).

available_laws 는 연도·취하 제외·대표건을 적용한 뒤, 법규 필터는 빼고 만든다. 법규 필터로 카테고리가 비어도 선택지는 유지.
"""
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine

from core.utils import logger
from scripts.dev import fixture_server
from services import report_stats_service as rs


class LawScopeTests(unittest.TestCase):
    def setUp(self):
        logger.LoggerFactory.create_logger(mode="crawl")
        self._dir = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self._dir.name) / 'data.db'}")
        fixture_server.seed_engine(self.engine)

    def tearDown(self):
        self.engine.dispose()
        self._dir.cleanup()

    def test_law_filter_keeps_choices_even_when_category_is_empty(self):
        base = rs.get_agency_stats(self.engine, None, mode="raw")
        traffic_law = base["traffic"]["available_laws"][0]
        filtered = rs.get_agency_stats(self.engine, {"law": traffic_law}, mode="raw")
        for category in ("traffic", "parking", "other"):
            self.assertEqual(filtered[category]["available_laws"], base[category]["available_laws"], category)
            self.assertIn("has_empty_law", filtered[category])

    def test_year_filter_narrows_law_choices(self):
        base = rs.get_agency_stats(self.engine, None, mode="raw")
        year = base["available_years"][0]
        by_year = rs.get_agency_stats(self.engine, {"year": year}, mode="raw")
        for category in ("traffic", "parking", "other"):
            self.assertTrue(set(by_year[category]["available_laws"]) <= set(base[category]["available_laws"]))


if __name__ == "__main__":
    unittest.main()
