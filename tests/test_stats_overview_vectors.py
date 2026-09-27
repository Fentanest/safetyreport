"""통계 개편(2026-09-28) 회귀 테스트.

- contracts/stats-overview-vectors.json: 서버·모바일이 같은 입력으로 같은 요약·기관표 행을 내는지(두 레포 바이트 동일 파일).
- 웹 통계 화면의 한 번 읽기(get_stats_page)가 기존 두 함수 결과와 같은지.
- 작은 지도·전체 지도(/stats/map)에 통계 조건을 넘기면 통계와 같은 모집단을 쓰는지(좌표 있는 건만 지도에 표시).
"""
import json
import os
import tempfile
import unittest
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, update

import settings.settings as app_settings
from core.database import database, models
from core.utils import logger
from scripts.dev import fixture_server
from services import report_stats_service

ROOT = Path(__file__).resolve().parents[1]
VECTORS = json.loads((ROOT / "contracts" / "stats-overview-vectors.json").read_text(encoding="utf-8"))
AGENCY_KEYS = ("agency", "total", "avg_days", "avg_days_count", "fines", "fine_amount_unknown", "total_fine_amount",
               "estimated_fine_amount", "estimated_fine_count", "in_progress", "disposition_unknown", "no_penalty", "unclassified")


class StatsOverviewVectorTest(unittest.TestCase):
    def test_overview_summary_matches_shared_vectors(self):
        for case in VECTORS["cases"]:
            with self.subTest(case=case["name"]):
                frame = pd.DataFrame(case["rows"]) if case["rows"] else pd.DataFrame()
                summary = report_stats_service._sanitize_jsonable(report_stats_service._summarize_overview_frame(frame))
                self.assertEqual(summary, case["expected_overview"])

    def test_agency_rows_match_shared_vectors(self):
        for case in VECTORS["cases"]:
            if not case["rows"]:
                continue
            with self.subTest(case=case["name"]):
                agency, _, _ = report_stats_service._build_stats_tables(pd.DataFrame(case["rows"]))
                rows = sorted(({k: r[k] for k in AGENCY_KEYS} for r in report_stats_service._sanitize_jsonable(agency)),
                              key=lambda r: r["agency"])
                self.assertEqual(rows, case["expected_agency_rows"])

    def test_disposition_items_add_up_with_overlap(self):
        # 일곱 항목의 합 = 총 건수 + overlap(과태료·경고/범칙금·불수용이 한 신고에 겹친 수)
        for case in VECTORS["cases"]:
            s = case["expected_overview"]
            d = s["disposition"]
            seven = sum(d[k] for k in ("fines", "warnings", "rejects", "in_progress", "disposition_unknown", "no_penalty", "unclassified"))
            self.assertEqual(seven, s["total"] + d["overlap"], case["name"])
            self.assertEqual(d["unconfirmed"], d["disposition_unknown"] + d["no_penalty"] + d["unclassified"], case["name"])

    def test_confirmed_and_estimated_amounts_stay_separate(self):
        case = next(c for c in VECTORS["cases"] if c["name"] == "traffic_mix")
        fa = case["expected_overview"]["fine_amount"]
        self.assertEqual((fa["confirmed_amount"], fa["confirmed_count"]), (100000, 2))
        self.assertEqual((fa["estimated_amount"], fa["estimated_count"], fa["unknown_count"]), (50000, 1, 1))
        self.assertNotIn("total_amount", fa)  # 합친 한 숫자를 내려주지 않는다(PROJECT_RULES §3-2)


class StatsPageAndMapTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        logger.LoggerFactory.create_logger(mode="crawl")
        fd, cls.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        cls.engine = create_engine(f"sqlite:///{cls.db_path}")
        fixture_server.seed_engine(cls.engine)
        # 교통 90000001·90000011·90000012 와 주정차 90000101 에만 좌표를 준다(나머지는 좌표 없음)
        coords = {"90000001": (37.561, 126.825), "90000011": (37.5605, 126.83), "90000012": (37.5605, 126.83),
                  "90000101": (37.558, 126.86)}
        with cls.engine.begin() as conn:
            for table in (models.detail_traffic_table, models.detail_parking_table):
                for rid, (lat, lng) in coords.items():
                    conn.execute(update(table).where(table.c.ID == rid).values(위도=lat, 경도=lng))
        database.merge_final(cls.engine)
        cls._saved = (app_settings._instance.exclude_withdraw, app_settings._instance.normalize_police)
        app_settings._instance.exclude_withdraw = True
        app_settings._instance.normalize_police = True

    @classmethod
    def tearDownClass(cls):
        app_settings._instance.exclude_withdraw, app_settings._instance.normalize_police = cls._saved
        cls.engine.dispose()
        os.remove(cls.db_path)

    def test_stats_page_matches_separate_calls(self):
        for filters in ({}, {"year": "2026"}, {"law": "도로교통법 제5조"}, {"law": "__없음__"}, {"agency": "강서", "excludePolice": True}):
            with self.subTest(filters=filters):
                records, overview = report_stats_service.get_stats_page(self.engine, filters, mode="canonical")
                self.assertEqual(records, report_stats_service.get_agency_stats(self.engine, filters, mode="canonical"))
                self.assertEqual(overview, report_stats_service.get_stats_overview(self.engine, filters, mode="canonical"))

    def test_fixture_overview_new_fields(self):
        _, overview = report_stats_service.get_stats_page(self.engine, {}, mode="canonical")
        t = overview["traffic"]
        # canonical 교통 10건(중복 1·취하 1 제외): 과태료 3(40,000원 2건 + 금액 없음 1건), 경고/범칙금 2, 불수용/기타 2
        self.assertEqual(t["total"], 10)
        self.assertEqual((t["disposition"]["fines"], t["disposition"]["warnings"], t["disposition"]["rejects"]), (3, 2, 2))
        self.assertEqual(t["fine_amount"]["confirmed_amount"], 80000)
        self.assertEqual((t["fine_amount"]["unknown_count"], t["fine_amount"]["estimated_count"]), (1, 1))
        self.assertEqual(sum(m["count"] for m in t["monthly_answered"]), 8)  # 답변일 없는 처리중·보완요청 2건 제외
        self.assertEqual(t["report_types"][0], {"name": "신호위반", "count": 6})
        # 기관표 행에 평균 처리기간 표본 수가 붙는다(합계 행의 가중치)
        records, _ = report_stats_service.get_stats_page(self.engine, {}, mode="canonical")
        rows = {r["agency"]: r for r in records["traffic"]["by_agency"]}
        self.assertEqual(rows["서울특별시 강서경찰서"]["avg_days_count"], 3)
        weighted = sum(r["avg_days"] * r["avg_days_count"] for r in rows.values() if r["avg_days"] is not None)
        base = sum(r["avg_days_count"] for r in rows.values())
        # 이 fixture 는 모든 신고에 처리기관이 있어 기관 가중 평균 = 요약 평균
        self.assertAlmostEqual(weighted / base, overview_avg(self), places=1)

    def test_map_uses_same_population_as_stats(self):
        for filters in ({}, {"law": "도로교통법 제5조"}, {"agency": "강서"}, {"year": "2026", "reportName": "신호"}):
            with self.subTest(filters=filters):
                _, overview = report_stats_service.get_stats_page(self.engine, filters, mode="canonical")
                payload = report_stats_service.get_report_map_stats(
                    self.engine, year=filters.get("year"), category="traffic", mode="canonical", filters=filters or None)
                self.assertEqual(payload["meta"]["total_reports"], overview["traffic"]["total"])
                self.assertLessEqual(payload["meta"]["geocoded_reports"], payload["meta"]["total_reports"])

    def test_map_target_agency_and_person(self):
        filters = {"targetAgency": "서울특별시 강서경찰서", "targetPerson": "김담당"}
        payload = report_stats_service.get_report_map_stats(self.engine, category="traffic", mode="canonical", filters=filters)
        # 강서경찰서·김담당: 90000001, 90000002, 90000010, 90000011(대표건) → 좌표는 01·11 두 곳
        self.assertEqual(payload["meta"]["total_reports"], 4)
        self.assertEqual(payload["meta"]["geocoded_reports"], 2)
        missing = report_stats_service.get_report_map_missing_groups(self.engine, category="traffic", mode="canonical", filters=filters)
        self.assertEqual(missing["meta"]["report_count"], 2)
        # 조건 없으면 예전과 같은 전체 지도
        plain = report_stats_service.get_report_map_stats(self.engine, category="traffic", mode="canonical")
        self.assertEqual(plain["meta"]["total_reports"], 10)


def overview_avg(test):
    _, overview = report_stats_service.get_stats_page(test.engine, {}, mode="canonical")
    return overview["traffic"]["avg_days"]


if __name__ == "__main__":
    unittest.main()
