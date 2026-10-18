"""처리기관 현행 표시명을 화면마다 같게 보이는지 확인한다(2026-10-18).

같은 기관이 서로 다른 부서코드로 들어온다 — 서울강서경찰서는 1810043(본서)·1814605(경비교통과)·1810341(교통과).
registry 는 셋을 한 기관(inst 키 하나)으로 묶고, 유형별 목록(/data/traffic 등)은 이미 현행 표시명을 보였다.
대시보드·감시목록·별점·중복 차량·중복 그룹 모달·텔레그램 메시지는 원문을 그대로 보여 화면마다 기관명이 달랐다.

- 웹 화면은 라우터에서만 바꾼다. 모바일·확장 API 의 원문 `처리기관`은 그대로다(계약 불변).
- API 는 하위호환 추가 필드 `agency_name_current`·`agency_key` 만 붙인다(/vehicle·/address·/summary).
- 크롤 변경 payload 는 `처리기관코드`·`agency_name_current` 를 추가로 싣는다(앱·Kotlin 알림 표시용).
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sqlalchemy import create_engine, select, update

from core.database import models
from core.storage import reports_repo
from core.utils import logger
from scripts.dev import fixture_server
from services import agency_registry, data_service

RAW = {
    "1814605": "경찰청 서울특별시경찰청 서울강서경찰서 경비교통과",
    "1810341": "경찰청 서울특별시경찰청 서울강서경찰서 교통과",
    "1810043": "경찰청 서울특별시경찰청 서울강서경찰서",
}
CURRENT = "서울특별시경찰청 서울강서경찰서"


def _request(path="/"):
    from starlette.requests import Request
    return Request({"type": "http", "method": "GET", "path": path, "query_string": b"",
                    "headers": [], "session": {}})


class RecordHelperTest(unittest.TestCase):
    def test_three_gangseo_codes_show_one_current_name(self):
        records = [{"처리기관": name, "처리기관코드": code} for code, name in RAW.items()]
        agency_registry.display_agency_records(records)
        self.assertEqual({r["처리기관"] for r in records}, {CURRENT})

    def test_unresolved_and_missing_keep_source(self):
        records = [{"처리기관": "어딘가구청", "처리기관코드": "9999999"}, {"처리기관": "", "처리기관코드": ""}, {"ID": "1"}]
        agency_registry.display_agency_records(records)
        self.assertEqual(records[0]["처리기관"], "어딘가구청")
        self.assertEqual(records[1]["처리기관"], "")
        self.assertNotIn("처리기관", records[2])

    def test_api_fields_are_additive_and_share_one_key(self):
        records = [{"처리기관": name, "처리기관코드": code} for code, name in RAW.items()]
        agency_registry.with_current_agency_fields(records)
        self.assertEqual([r["처리기관"] for r in records], list(RAW.values()))  # 원문 그대로
        self.assertEqual({r["agency_name_current"] for r in records}, {CURRENT})
        self.assertEqual(len({r["agency_key"] for r in records}), 1)
        self.assertTrue(records[0]["agency_key"].startswith("inst:"))

    def test_api_fields_for_empty_agency(self):
        records = [{"처리기관": None, "처리기관코드": None}]
        agency_registry.with_current_agency_fields(records)
        self.assertEqual((records[0]["agency_name_current"], records[0]["agency_key"]), ("", ""))

    def test_telegram_message_uses_current_name(self):
        from core.utils.message_formatter import format_report_list

        text = format_report_list([{"처리기관": RAW["1814605"], "처리기관코드": "1814605"}], "검색")
        self.assertIn(f"처리기관: {CURRENT}\n", text)
        self.assertNotIn("경비교통과", text)


class ScreenSurfaceTest(unittest.TestCase):
    """fixture 의 모든 신고를 강서경찰서 세 코드로 돌려 넣고 화면마다 현행 표시명만 보이는지 본다."""

    def setUp(self):
        logger.LoggerFactory.create_logger(mode="crawl")
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.engine = create_engine(f"sqlite:///{Path(directory.name) / 'data.db'}")
        self.addCleanup(self.engine.dispose)
        fixture_server.seed_engine(self.engine)
        table = models.report_table
        with self.engine.begin() as conn:
            ids = [row.ID for row in conn.execute(select(table.c.ID))]
            codes = list(RAW)
            for index, record_id in enumerate(ids):
                code = codes[index % len(codes)]
                conn.execute(update(table).where(table.c.ID == record_id).values(처리기관=RAW[code], 처리기관코드=code))
            reports_repo.refresh_effective_rows(conn, ids)
        rnums = data_service.resolve_to_report_numbers(self.engine, ids[:6])
        data_service.update_watchlist_status(self.engine, rnums, "Y")

    def _ctx(self, module, call):
        with mock.patch.object(module, "engine", self.engine), \
                mock.patch.object(module.templates, "TemplateResponse", side_effect=lambda req, name, ctx: ctx):
            return call()

    def assertAllCurrent(self, records):
        self.assertTrue(records)
        self.assertEqual({r["처리기관"] for r in records}, {CURRENT})

    def test_duplicate_vehicle_page(self):
        from web.routers import data as web_data
        ctx = self._ctx(web_data, lambda: web_data.view_duplicates(_request("/data/duplicates"), dedupe="raw"))
        self.assertAllCurrent(ctx["records"])

    def test_watchlist_page(self):
        from web.routers import watchlist_route as wr
        self.assertAllCurrent(self._ctx(wr, lambda: wr.view_watchlist(_request("/watchlist/")))["records"])

    def test_rating_page(self):
        from web.routers import rating_route as rr
        self.assertAllCurrent(self._ctx(rr, lambda: rr.view_rating_page(_request("/rating/")))["records"])

    def test_duplicate_groups_modal(self):
        from web.routers import duplicate_route as dr
        groups = self._ctx(dr, lambda: dr.view_duplicate_groups(_request("/duplicates/manage")))["groups"]
        self.assertTrue(groups)
        for group in groups:
            self.assertAllCurrent(group["members"])
            self.assertEqual(group["representative"].get("처리기관", CURRENT), CURRENT)

    def test_dashboard_tables(self):
        from web.routers import dashboard as dash
        ctx = self._ctx(dash, lambda: dash.dashboard(_request("/")))
        self.assertFalse(ctx["load_error"])
        self.assertAllCurrent(ctx["watchlist"])
        if ctx["recent_answers"]:
            self.assertAllCurrent(ctx["recent_answers"])

    def test_api_keeps_source_and_adds_current_fields(self):
        from web.routers import api_route
        with mock.patch.object(api_route, "engine", self.engine):
            summary = api_route.get_summary(_request("/api/v1/summary"), dedupe="raw", _="key")["data"]
            vehicle = api_route.get_vehicle_reports("99가9999", dedupe="raw", _="key")["data"]
            duplicates = api_route.get_reports(_request("/api/v1/reports/duplicates"), "duplicates", dedupe="raw", _="key")["data"]
        self.assertTrue(vehicle)
        for rows in (summary["watchlist"], vehicle):
            self.assertTrue(set(r["처리기관"] for r in rows) <= set(RAW.values()))
            self.assertEqual({r["agency_name_current"] for r in rows}, {CURRENT})
            self.assertEqual(len({r["agency_key"] for r in rows}), 1)
        # 모바일 목록 API 는 원문만(추가 필드도 없음 — 앱이 registry 로 직접 푼다)
        self.assertTrue(duplicates)
        self.assertTrue(set(r["처리기관"] for r in duplicates) <= set(RAW.values()))
        self.assertNotIn("agency_name_current", duplicates[0])

    def test_crawl_change_payload_carries_code_and_current_name(self):
        import settings.settings as app_settings
        from services import crawl_state_store

        with self.engine.connect() as conn:
            record_id = conn.execute(select(models.report_effective_table.c.ID)
                                     .where(models.report_effective_table.c.처리기관코드 == "1814605")).scalars().first()
        crawl_state_store.save_crawl_changes(self.engine, [{"id": record_id, "change_type": "변경"}])
        self.addCleanup(crawl_state_store.clear_crawl_changes)
        payload = json.loads((Path(app_settings.datapath) / "crawl_changes.json").read_text(encoding="utf-8"))
        item = next(p for p in payload if p.get("ID") == record_id)
        self.assertEqual(item["처리기관"], RAW["1814605"])  # 원문 그대로
        self.assertEqual(item["처리기관코드"], "1814605")
        self.assertEqual(item["agency_name_current"], CURRENT)


if __name__ == "__main__":
    unittest.main()
