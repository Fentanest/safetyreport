"""5분류(스키마 6, 2026-10-11) 통계·지도·데이터 라우트 회귀 테스트.

- 통계: `_load_stats_frames`/`get_stats_overview`(taxonomy=5)가 안전신고·생활불편을 따로 내고,
  taxonomy=3(구앱 호환, 기본값)은 셋을 묶은 예전 `other` 그대로다 — 합이 서로 같아야 한다.
- 모바일 API: `/api/v1/stats`, `/api/v1/stats/overview` 는 `X-SR-Taxonomy: 5` 가 없으면 3분류,
  있으면 5분류(report_category.CATEGORIES 그대로).
- 지도: `category_breakdown` 라벨은 taxonomy=5 면 CATEGORY_LABELS(5, '기타'), taxonomy=3(기본, 구앱 호환)이면
  예전 라벨 그대로('기타위반' — 구앱 지도 화면이 이 문자열을 비교해서 찾는다).
- 목록: `/data/safety`, `/data/living` 는 그 분류만 보인다(`/data/other` 는 기타만, 제목 "기타 조회").
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, update

from core.database import database, models
from core.utils import logger
from core.utils.taxonomy_compat import taxonomy_compat_middleware
from scripts.dev import fixture_server
from services import data_service, report_category
from services import report_stats_service as stats
from web.routers import api_route


def _seed_five_category_engine(test_case):
    """fixture 는 traffic·parking·safety(entry_value 때문)만 만든다. 두 safety 건을 living·other 로
    옮겨 5분류를 모두 갖춘 engine 을 돌려준다: (engine, api_key, ids by category)."""
    directory = tempfile.TemporaryDirectory()
    test_case.addCleanup(directory.cleanup)
    engine = create_engine(f"sqlite:///{Path(directory.name) / 'data.db'}")
    test_case.addCleanup(engine.dispose)
    seeded = fixture_server.seed_engine(engine)

    with engine.connect() as conn:
        safety_ids = sorted(r[0] for r in conn.execute(
            select(models.report_table.c.ID).where(models.report_table.c.category == "safety")))
        traffic_id = conn.execute(
            select(models.report_table.c.ID).where(models.report_table.c.category == "traffic")
            .order_by(models.report_table.c.ID)).scalar()
        parking_id = conn.execute(
            select(models.report_table.c.ID).where(models.report_table.c.category == "parking")
            .order_by(models.report_table.c.ID)).scalar()
    test_case.assertGreaterEqual(len(safety_ids), 3, "fixture 의 '기타' 신고는 entry_value 때문에 전부 safety 로 분류된다")
    living_id, other_id, safety_id = safety_ids[0], safety_ids[1], safety_ids[2]
    with engine.begin() as conn:
        conn.execute(update(models.report_table).where(models.report_table.c.ID == living_id).values(category="living"))
        conn.execute(update(models.report_table).where(models.report_table.c.ID == other_id).values(category="other"))
    database.rebuild_effective(engine)
    ids = {"traffic": traffic_id, "parking": parking_id, "safety": safety_id, "living": living_id, "other": other_id}
    return engine, seeded["api_key"], ids


class StatsFrameTaxonomyTest(unittest.TestCase):
    """`_load_stats_frames`/`get_stats_overview` 가 taxonomy 전환에 맞게 분류를 나누거나 접는지."""

    def setUp(self):
        logger.LoggerFactory.create_logger(mode="crawl")
        self.engine, self.api_key, self.ids = _seed_five_category_engine(self)

    def test_load_stats_frames_taxonomy_5_returns_5_ordered_categories(self):
        available_years, frames = stats._load_stats_frames(self.engine, None, "canonical", taxonomy=5)
        self.assertEqual(list(frames.keys()), list(report_category.CATEGORIES))
        self.assertIn(self.ids["safety"], set(frames["safety"]["ID"].astype(str)))
        self.assertIn(self.ids["living"], set(frames["living"]["ID"].astype(str)))
        self.assertIn(self.ids["other"], set(frames["other"]["ID"].astype(str)))
        self.assertNotIn(self.ids["safety"], set(frames["other"]["ID"].astype(str)))

    def test_load_stats_frames_taxonomy_3_folds_safety_and_living_into_other(self):
        _, frames = stats._load_stats_frames(self.engine, None, "canonical", taxonomy=3)
        self.assertEqual(list(frames.keys()), list(report_category.LEGACY_CATEGORIES))
        other_ids = set(frames["other"]["ID"].astype(str))
        for key in ("safety", "living", "other"):
            self.assertIn(self.ids[key], other_ids)

    def test_overview_taxonomy_5_splits_what_taxonomy_3_folds_into_other(self):
        """5분류 safety+living+other 합계 = 3분류(구앱 호환, 기본값) other 합계. traffic·parking·all 은 그대로."""
        five = stats.get_stats_overview(self.engine, taxonomy=5)
        three = stats.get_stats_overview(self.engine, taxonomy=3)
        self.assertEqual(set(five) & set(report_category.CATEGORIES), set(report_category.CATEGORIES))
        self.assertEqual(set(three) & set(report_category.CATEGORIES), set(report_category.LEGACY_CATEGORIES))
        self.assertNotIn("safety", three)
        self.assertNotIn("living", three)
        self.assertEqual(
            five["safety"]["total"] + five["living"]["total"] + five["other"]["total"],
            three["other"]["total"],
        )
        self.assertGreaterEqual(five["living"]["total"], 1)
        self.assertGreaterEqual(five["safety"]["total"], 1)
        self.assertEqual(five["traffic"]["total"], three["traffic"]["total"])
        self.assertEqual(five["parking"]["total"], three["parking"]["total"])
        self.assertEqual(five["all"]["total"], three["all"]["total"])

    def test_default_taxonomy_matches_byte_for_byte_old_shape(self):
        """taxonomy 를 안 주면(기존 호출자) 예전과 같은 3분류 — 구서버 호환의 핵심 불변."""
        default = stats.get_stats_overview(self.engine)
        three = stats.get_stats_overview(self.engine, taxonomy=3)
        self.assertEqual(default, three)
        default_agency = stats.get_agency_stats(self.engine)
        three_agency = stats.get_agency_stats(self.engine, taxonomy=3)
        self.assertEqual(default_agency, three_agency)


class ApiStatsTaxonomyHeaderTest(unittest.TestCase):
    """`/api/v1/stats`, `/api/v1/stats/overview`: `X-SR-Taxonomy: 5` 없는 구앱은 3분류, 있으면 5분류."""

    def setUp(self):
        logger.LoggerFactory.create_logger(mode="crawl")
        self.engine, self.api_key, self.ids = _seed_five_category_engine(self)
        patch = mock.patch.object(api_route, "engine", self.engine)
        patch.start()
        self.addCleanup(patch.stop)
        app = FastAPI()
        app.include_router(api_route.router)
        app.middleware("http")(taxonomy_compat_middleware)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def _get(self, path, *, five=False):
        headers = {"X-API-Key": self.api_key}
        if five:
            headers["X-SR-Taxonomy"] = "5"
        return self.client.get(path, headers=headers)

    def test_stats_overview_old_client_sees_3_categories(self):
        response = self._get("/api/v1/stats/overview")
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(set(data) & set(report_category.CATEGORIES), set(report_category.LEGACY_CATEGORIES))
        self.assertNotIn("safety", data)
        self.assertNotIn("living", data)

    def test_stats_overview_five_aware_client_sees_5_categories(self):
        old = self._get("/api/v1/stats/overview").json()["data"]
        five = self._get("/api/v1/stats/overview", five=True).json()["data"]
        self.assertEqual(set(five) & set(report_category.CATEGORIES), set(report_category.CATEGORIES))
        self.assertEqual(
            five["safety"]["total"] + five["living"]["total"] + five["other"]["total"],
            old["other"]["total"],
        )

    def test_stats_old_client_sees_3_categories_new_client_sees_5(self):
        old = self._get("/api/v1/stats").json()["data"]
        five = self._get("/api/v1/stats", five=True).json()["data"]
        self.assertEqual(set(old) & set(report_category.CATEGORIES), set(report_category.LEGACY_CATEGORIES))
        self.assertEqual(set(five) & set(report_category.CATEGORIES), set(report_category.CATEGORIES))
        # 공통 분류(traffic·parking)는 두 응답이 같다.
        for category in ("traffic", "parking"):
            self.assertEqual(old[category]["total_fine_amount"], five[category]["total_fine_amount"])


class MapCategoryBreakdownTaxonomyTest(unittest.TestCase):
    """지도 클러스터의 `category_breakdown` 라벨: taxonomy=5 는 CATEGORY_LABELS(5, '기타'),
    taxonomy=3(기본, 구앱 호환)은 예전 라벨 그대로('기타위반')."""

    def setUp(self):
        logger.LoggerFactory.create_logger(mode="crawl")
        self.engine, self.api_key, self.ids = _seed_five_category_engine(self)
        coords = {
            self.ids["traffic"]: (37.561, 126.825),
            self.ids["parking"]: (37.560, 126.826),
            self.ids["safety"]: (37.559, 126.827),
            self.ids["living"]: (37.558, 126.828),
            self.ids["other"]: (37.557, 126.829),
        }
        with self.engine.begin() as conn:
            for rid, (lat, lng) in coords.items():
                conn.execute(update(models.report_table).where(models.report_table.c.ID == rid).values(위도=lat, 경도=lng))
        database.rebuild_effective(self.engine)

    def _labels(self, *, taxonomy):
        from services.report_stats_service import get_report_map_stats
        payload = get_report_map_stats(self.engine, category="all", mode="canonical", taxonomy=taxonomy, max_points=1)
        self.assertEqual(len(payload["points"]), 1, "5개 좌표가 한 점에 묶여야 라벨을 한 번에 본다")
        return {item["label"] for item in payload["points"][0]["category_breakdown"]}

    def test_taxonomy_5_uses_the_five_category_labels(self):
        labels = self._labels(taxonomy=5)
        self.assertEqual(labels, {report_category.CATEGORY_LABELS[c] for c in report_category.CATEGORIES})
        self.assertIn("기타", labels)
        self.assertNotIn("기타위반", labels)

    def test_taxonomy_3_keeps_the_old_label_text(self):
        """구앱 지도 화면이 '기타위반' 문자열을 그대로 찾는다 — 라벨 문구를 바꾸면 안 된다."""
        labels = self._labels(taxonomy=3)
        self.assertEqual(labels, {"교통위반", "주정차위반", "기타위반"})

    def test_default_taxonomy_keeps_the_old_label_text(self):
        from services.report_stats_service import get_report_map_stats
        payload = get_report_map_stats(self.engine, category="all", mode="canonical", max_points=1)
        labels = {item["label"] for item in payload["points"][0]["category_breakdown"]}
        self.assertEqual(labels, {"교통위반", "주정차위반", "기타위반"})


class DataSafetyLivingRouteTest(unittest.TestCase):
    """`/data/safety`, `/data/living` 는 그 분류만, `/data/other` 는 기타만(제목 "기타 조회")."""

    def setUp(self):
        logger.LoggerFactory.create_logger(mode="crawl")
        self.engine, self.api_key, self.ids = _seed_five_category_engine(self)

    @staticmethod
    def _request():
        from starlette.requests import Request
        return Request({"type": "http", "method": "GET", "path": "/data/safety", "query_string": b"",
                        "headers": [], "session": {}})

    # view_safety/view_living/view_other 의 나머지 매개변수는 FastAPI `Query(None)` 기본값이라, 라우터를 거치지 않고
    # 함수를 직접 부를 때는 명시적으로 None/False 를 넘겨야 한다(기본값 그대로면 Query 객체가 그대로 들어간다).
    _NO_FILTERS = dict(status=None, fine=None, agency=None, person=None, agencyExact=False,
                       law=None, rating=None, ratingCause=None, dedupe=None)

    def test_safety_route_lists_only_safety_records(self):
        from web.routers import data as web_data
        with mock.patch.object(web_data, "engine", self.engine), \
                mock.patch.object(web_data.templates, "TemplateResponse", side_effect=lambda req, name, ctx: ctx):
            context = web_data.view_safety(self._request(), **self._NO_FILTERS)
        self.assertEqual(context["title"], "안전신고 조회")
        self.assertEqual(context["table_id"], "safetyTable")
        ids = {r["ID"] for r in context["records"]}
        self.assertIn(self.ids["safety"], ids)
        self.assertNotIn(self.ids["living"], ids)
        self.assertNotIn(self.ids["other"], ids)
        self.assertTrue(context["records"], "안전신고 fixture 행이 있어야 한다")
        self.assertTrue(all(r["category"] == "safety" for r in context["records"]))

    def test_living_route_lists_only_living_records(self):
        from web.routers import data as web_data
        with mock.patch.object(web_data, "engine", self.engine), \
                mock.patch.object(web_data.templates, "TemplateResponse", side_effect=lambda req, name, ctx: ctx):
            context = web_data.view_living(self._request(), **self._NO_FILTERS)
        self.assertEqual(context["title"], "생활불편 조회")
        self.assertEqual(context["table_id"], "livingTable")
        ids = {r["ID"] for r in context["records"]}
        self.assertIn(self.ids["living"], ids)
        self.assertNotIn(self.ids["safety"], ids)
        self.assertNotIn(self.ids["other"], ids)

    def test_other_route_lists_only_true_other_records_with_the_new_title(self):
        from web.routers import data as web_data
        with mock.patch.object(web_data, "engine", self.engine), \
                mock.patch.object(web_data.templates, "TemplateResponse", side_effect=lambda req, name, ctx: ctx):
            context = web_data.view_other(self._request(), **self._NO_FILTERS)
        self.assertEqual(context["title"], "기타 조회")
        ids = {r["ID"] for r in context["records"]}
        self.assertIn(self.ids["other"], ids)
        self.assertNotIn(self.ids["safety"], ids)
        self.assertNotIn(self.ids["living"], ids)


if __name__ == "__main__":
    unittest.main()
