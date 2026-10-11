"""모바일 API 분류 호환(스키마 6, core/utils/taxonomy_compat.py).

서버는 5분류(traffic·parking·safety·living·other)를 쓰지만 이 기능을 모르는 구앱·크롬 확장은 3분류만 안다.
`X-SR-Taxonomy: 5` 헤더(또는 `?taxonomy=5`)를 보내지 않은 요청의 `/api/v1/*` JSON 응답은 safety·living 값을
other 로 접어 보낸다. `/reports/other` 처럼 분류로 고르는 요청은 구앱 묶음(safety·living·other)을 센다.
편집기(`/editor/{category}/{id}`)는 ID 로 찾으므로 'other' 를 그 묶음까지 넓혀도 안전하다(구앱 호환).

tests/test_api_input_boundary.py 와 같은 패턴: api_route.router 만 올린 가벼운 앱(+ 이 미들웨어)으로
community_gate 등 전체 앱의 다른 관문 없이 분류 호환만 본다.
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
from web.routers import api_route


class TaxonomyCompatTests(unittest.TestCase):
    def setUp(self):
        logger.LoggerFactory.create_logger(mode="crawl")
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.engine = create_engine(f"sqlite:///{Path(self.directory.name) / 'data.db'}")
        self.addCleanup(self.engine.dispose)
        seeded = fixture_server.seed_engine(self.engine)
        self.api_key = seeded["api_key"]
        patch = mock.patch.object(api_route, "engine", self.engine)
        patch.start()
        self.addCleanup(patch.stop)

        with self.engine.connect() as conn:
            safety_ids = [r[0] for r in conn.execute(
                select(models.report_table.c.ID).where(models.report_table.c.category == "safety")
                .order_by(models.report_table.c.ID))]
        self.assertGreaterEqual(len(safety_ids), 3, "fixture 의 '기타' 신고는 entry_value 때문에 전부 safety 로 분류된다")
        self.safety_id = safety_ids[0]
        # fixture 는 safety 만 만든다. living·other 도 쓰려면 직접 분류를 바꿔 둔다(실제 신고 내용은 안 바뀜).
        self.living_id, self.other_id = safety_ids[1], safety_ids[2]
        with self.engine.begin() as conn:
            conn.execute(update(models.report_table).where(models.report_table.c.ID == self.living_id).values(category="living"))
            conn.execute(update(models.report_table).where(models.report_table.c.ID == self.other_id).values(category="other"))
        database.rebuild_effective(self.engine)

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

    def test_old_client_gets_other_for_safety_living_and_other_on_the_other_route(self):
        """`/reports/other` 는 구앱에게 예전 '기타' 묶음(safety·living·other) 전부를 묶어 보낸다."""
        response = self._get("/api/v1/reports/other")
        self.assertEqual(response.status_code, 200)
        rows = {r["ID"]: r["category"] for r in response.json()["data"]}
        for record_id in (self.safety_id, self.living_id, self.other_id):
            with self.subTest(record_id=record_id):
                self.assertIn(record_id, rows)
                self.assertEqual(rows[record_id], "other")

    def test_five_aware_client_on_the_other_route_excludes_safety_and_living(self):
        """5분류를 아는 클라이언트는 `other` 요청에 진짜 other 만 받는다(safety·living 은 빠진다)."""
        response = self._get("/api/v1/reports/other", five=True)
        self.assertEqual(response.status_code, 200)
        rows = {r["ID"]: r["category"] for r in response.json()["data"]}
        self.assertNotIn(self.safety_id, rows)
        self.assertNotIn(self.living_id, rows)
        self.assertIn(self.other_id, rows)
        self.assertEqual(rows[self.other_id], "other")

    def test_safety_route_works_for_everyone_but_folds_category_for_old_clients(self):
        """`/reports/safety` 는 누구나 쓸 수 있지만, 응답의 category 값은 구앱에게 other 로 접힌다."""
        old = self._get("/api/v1/reports/safety")
        self.assertEqual(old.status_code, 200)
        old_rows = {r["ID"]: r["category"] for r in old.json()["data"]}
        self.assertIn(self.safety_id, old_rows)
        self.assertEqual(old_rows[self.safety_id], "other", "구앱은 safety 를 모르니 기타로 본다")

        new = self._get("/api/v1/reports/safety", five=True)
        self.assertEqual(new.status_code, 200)
        new_rows = {r["ID"]: r["category"] for r in new.json()["data"]}
        self.assertIn(self.safety_id, new_rows)
        self.assertEqual(new_rows[self.safety_id], "safety", "5분류 헤더를 보내면 접지 않는다")

    def test_editor_other_route_finds_a_safety_record(self):
        """편집기는 ID 로 찾으므로 'other' 요청이 safety 신고를 찾아낸다(구앱 호환, 헤더 없어도 동작)."""
        response = self._get(f"/api/v1/editor/other/{self.safety_id}")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["data"]["record"]["ID"], self.safety_id)
        self.assertEqual(body["data"]["record"]["category"], "other", "구앱 응답이라 category 도 접힌다")

        # 5분류 헤더가 있으면 같은 레코드를 찾되 실제 분류 값을 그대로 보여준다.
        response_five = self._get(f"/api/v1/editor/other/{self.safety_id}", five=True)
        self.assertEqual(response_five.status_code, 200)
        self.assertEqual(response_five.json()["data"]["record"]["category"], "safety")


    def test_summary_watchlist_keeps_safety_for_five_aware_clients_and_folds_for_old_ones(self):
        """대시보드(/summary)는 실제 분류를 보내고 구앱에만 미들웨어가 접는다(Astra 2026-10-11 지적 6)."""
        with self.engine.begin() as conn:
            number = conn.execute(select(models.report_table.c["신고번호"]).where(
                models.report_table.c.ID == self.safety_id)).scalar_one()
            conn.execute(models.watchlist_table.insert().values(신고번호=number))
        database.rebuild_effective(self.engine)
        from services import report_cache
        report_cache.clear()

        def category_of(response):
            self.assertEqual(response.status_code, 200)
            items = response.json()["data"]["watchlist"]
            return next(item["category"] for item in items if item["신고번호"] == number)

        self.assertEqual(category_of(self._get("/api/v1/summary", five=True)), "safety")
        self.assertEqual(category_of(self._get("/api/v1/summary")), "other")


if __name__ == "__main__":
    unittest.main()
