"""통계 집계의 현재 의미를 합성 fixture 로 고정한다(docs/design/statistics-spec.md 의 기대값 표와 같다).

기대값은 scripts/dev/fixture_server.py 의 24건을 손으로 센 값이다. 시안 이미지 숫자와 무관하다.
설정 기본값: use_representative_records(canonical), exclude_withdraw=True, normalize_police=True.
"""
import os
import tempfile
import unittest

from sqlalchemy import create_engine

import settings.settings as app_settings
from core.utils import logger
from scripts.dev import fixture_server
from services import report_stats_service


class StatsFixtureSemanticsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        logger.LoggerFactory.create_logger(mode="crawl")
        fd, cls.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        cls.engine = create_engine(f"sqlite:///{cls.db_path}")
        fixture_server.seed_engine(cls.engine)
        cls._saved = (app_settings._instance.exclude_withdraw, app_settings._instance.normalize_police)
        app_settings._instance.exclude_withdraw = True
        app_settings._instance.normalize_police = True

    @classmethod
    def tearDownClass(cls):
        app_settings._instance.exclude_withdraw, app_settings._instance.normalize_police = cls._saved
        cls.engine.dispose()
        os.remove(cls.db_path)

    def test_duplicate_pair_collapses_only_in_canonical(self):
        raw = report_stats_service.get_dashboard_stats(self.engine, mode="raw")
        canonical = report_stats_service.get_dashboard_stats(self.engine, mode="canonical")
        self.assertEqual(raw["total"], 24)
        self.assertEqual(canonical["total"], 23)  # 90000012(불수용)이 대표건 90000011(과태료)에 흡수

    def test_dashboard_counts_and_percent_denominator(self):
        d = report_stats_service.get_dashboard_stats(self.engine, mode="canonical")
        counts = {k: d[k] for k in ("acceptCount", "partialCount", "rejectCount", "processingCount",
                                     "supplementCount", "completedCount", "withdrawCount", "withdrawGraphCount")}
        self.assertEqual(counts, {"acceptCount": 9, "partialCount": 2, "rejectCount": 4, "processingCount": 3,
                                  "supplementCount": 2, "completedCount": 16, "withdrawCount": 2, "withdrawGraphCount": 0})
        # 취하 제외 시 분모 = 수용+일부+불수용/기타+처리중+보완 = 20 (답변완료 1건·취하 2건은 분모 밖, total 23 은 포함)
        self.assertEqual((d["accept_pct"], d["partial_pct"], d["reject_pct"], d["processing_pct"], d["supplement_pct"]),
                         (45.0, 10.0, 20.0, 15.0, 10.0))
        self.assertEqual(d["withdraw_pct"], 0)

    def test_dashboard_traffic_disposition_bar(self):
        d = report_stats_service.get_dashboard_stats(self.engine, mode="canonical")
        self.assertEqual((d["tFineCount"], d["tPenaltyCount"], d["tRejectCount"], d["tUnconfirmedCount"]), (3, 2, 2, 1))
        self.assertEqual((d["tfine_pct"], d["tpenalty_pct"], d["treject_pct"], d["tunconfirmed_pct"]), (37.5, 25.0, 25.0, 12.5))

    def test_traffic_agency_rows(self):
        stats = report_stats_service.get_agency_stats(self.engine, {}, mode="canonical")
        rows = {r["agency"]: r for r in stats["traffic"]["by_agency"]}
        gangseo = rows["서울특별시 강서경찰서"]  # '교통과' 접미사는 normalize_police 로 제거
        self.assertEqual((gangseo["total"], gangseo["fines"], gangseo["warnings"], gangseo["rejects"], gangseo["unconfirmed"]),
                         (4, 2, 1, 0, 1))
        self.assertEqual(gangseo["avg_days"], 8.3)  # (2+5+18)/3, 답변일 없는 보완요청 건은 표본 제외
        self.assertEqual(gangseo["total_fine_amount"], 80000)
        self.assertEqual((gangseo["avg_rating"], gangseo["rating_count"]), (4.5, 2))
        self.assertEqual(rows["경기도 부천원미경찰서"]["avg_days"], 6.0)
        self.assertEqual((rows["인천광역시 서부경찰서"]["avg_rating"], rows["인천광역시 서부경찰서"]["rating_count"]), (1.5, 2))
        long_name = [a for a in rows if a.startswith("서울특별시 강서구청 교통행정과")][0]
        self.assertEqual(rows[long_name]["total_fine_amount"], 0)  # '과태료'만 있고 금액 없음 → 0 으로 합산
        # 담당자 없는 처리중(90000006)과 취하(90000009)는 기관 통계에서 빠진다
        self.assertEqual(sum(r["total"] for r in rows.values()), 9)

    def test_year_filter_uses_answer_date(self):
        # 2025-12-30 신고 / 2026-01-02 답변 건은 2025 가 아니라 2026 에 잡힌다
        stats_2025 = report_stats_service.get_agency_stats(self.engine, {"year": "2025"}, mode="canonical")
        self.assertEqual(stats_2025["traffic"]["by_agency"], [])
        self.assertEqual(report_stats_service.get_agency_stats(self.engine, {}, mode="canonical")["available_years"], ["2026"])

    def test_law_filter_does_not_change_traffic_total_fine_banner(self):
        # 현재 동작 기록(결함 후보 D-STAT-1): 배너의 traffic_total_fine 은 법규 필터 전 값이다.
        stats = report_stats_service.get_agency_stats(self.engine, {"law": "도로교통법 제13조"}, mode="canonical")
        self.assertEqual(stats["traffic"]["total_fine_amount"], 0)
        self.assertEqual(stats["traffic_total_fine"], 80000)


if __name__ == "__main__":
    unittest.main()
