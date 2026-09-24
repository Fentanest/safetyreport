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
from services import fine_estimate, report_stats_service


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
        # S-10: 담당자 없는 처리중(90000006)도 처리기관이 있으면 기관표에 들어가고 in_progress 로 센다(보완요청 포함 2건).
        self.assertEqual((gangseo["total"], gangseo["fines"], gangseo["warnings"], gangseo["rejects"],
                          gangseo["in_progress"], gangseo["unconfirmed"]), (5, 2, 1, 0, 2, 0))
        # 처리일 = 답변일(날짜) − 신고일(날짜): 12/30→1/2=3, 1/3→1/9=6, 9/1→9/20=19 → 9.3 (완료 건만)
        self.assertEqual(gangseo["avg_days"], 9.3)
        self.assertEqual(gangseo["total_fine_amount"], 80000)
        self.assertEqual((gangseo["avg_rating"], gangseo["rating_count"]), (4.5, 2))
        self.assertEqual(rows["경기도 부천원미경찰서"]["avg_days"], 7.0)
        incheon = rows["인천광역시 서부경찰서"]
        self.assertEqual((incheon["avg_rating"], incheon["rating_count"]), (1.5, 2))
        # (b) 열 분리: 파서가 '미확인'으로 둔 일부수용 1건은 처분 미확인
        self.assertEqual((incheon["unconfirmed"], incheon["disposition_unknown"], incheon["no_penalty"], incheon["unclassified"]), (1, 1, 0, 0))
        self.assertEqual(incheon["avg_days"], 8.0)
        long_name = [a for a in rows if a.startswith("서울특별시 강서구청 교통행정과")][0]
        # 금액 없는 '과태료' → 확정 0원 + 금액 미확인 1건. 버스전용차로 추정(승용 5만)은 확정과 따로.
        self.assertEqual((rows[long_name]["total_fine_amount"], rows[long_name]["fine_amount_unknown"]), (0, 1))
        self.assertEqual((rows[long_name]["estimated_fine_amount"], rows[long_name]["estimated_fine_count"]), (50000, 1))
        self.assertEqual(rows[long_name]["avg_days"], 7.0)
        # 취하(90000009)만 빠진다
        self.assertEqual(sum(r["total"] for r in rows.values()), 10)

    def test_other_category_split_no_penalty(self):
        stats = report_stats_service.get_agency_stats(self.engine, {}, mode="canonical")
        rows = {r["agency"]: r for r in stats["other"]["by_agency"]}
        road = rows["서울특별시 강서구청 도로과"]  # 도로 파손 수용 1 + 보완요청 1 (시설물 = 과태료 대상 아님)
        self.assertEqual((road["no_penalty"], road["in_progress"], road["unconfirmed"]), (1, 1, 1))
        # 쓰레기 메뉴가 아닌 '쓰레기, 폐기물' 신고명은 기타 메뉴(안전신고-생활안전)라 추정하지 않는다
        self.assertEqual(stats["other"]["estimated_fine_count"], 0)

    def test_parking_estimates_use_lowest_standard(self):
        stats = report_stats_service.get_agency_stats(self.engine, {}, mode="canonical")
        # 주정차 '과태료'(금액 없음) 2건: 90000101(기타 불법주정차, 승용 4만), 90000103(승용 4만)
        self.assertEqual((stats["parking"]["total_fine_amount"], stats["parking"]["estimated_fine_amount"],
                          stats["parking"]["estimated_fine_count"]), (0, 80000, 2))
        self.assertEqual(stats["parking"]["estimate_rule_version"], fine_estimate.RULE_VERSION)

    def test_year_filter_uses_answer_date(self):
        # 2025-12-30 신고 / 2026-01-02 답변 건은 2025 가 아니라 2026 에 잡힌다
        stats_2025 = report_stats_service.get_agency_stats(self.engine, {"year": "2025"}, mode="canonical")
        self.assertEqual(stats_2025["traffic"]["by_agency"], [])
        self.assertEqual(report_stats_service.get_agency_stats(self.engine, {}, mode="canonical")["available_years"], ["2026"])

    def test_law_filter_applies_to_category_total(self):
        # 2026-09-24 결정: 웹 배너는 카테고리 필터 합계(traffic.total_fine_amount)를 쓴다.
        # API 호환 필드 traffic_total_fine 은 기존처럼 법규 필터 전 값이다(모바일 소비자 계약).
        stats = report_stats_service.get_agency_stats(self.engine, {"law": "도로교통법 제13조"}, mode="canonical")
        self.assertEqual(stats["traffic"]["total_fine_amount"], 0)
        self.assertEqual(stats["traffic_total_fine"], 80000)


if __name__ == "__main__":
    unittest.main()
