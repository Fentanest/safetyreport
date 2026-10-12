"""감시목록(watchlist.html)·별점(rating.html)·중복 묶음(duplicate_groups.html)·대시보드(index.html) 화면에도
금액 미확인 과태료 추정 보조 표기를 반영했는지 확인한다(2026-10-12, /data/* 화면·상세 모달에 붙인
380534b 의 뒤이은 확장, docs/design/statistics-spec.md §4-1·§4-2, PROJECT_RULES §3-2).

- 판정은 services/fine_estimate.estimate_if_unknown 을 그대로 쓴다(재구현 금지). web/routers/filters.py 의
  with_fine_estimates() 하나를 web/routers/data.py·watchlist_route.py·rating_route.py·duplicate_route.py·
  dashboard.py 가 모두 같은 함수 객체로 공유하는지(identity) 를 본다 — 화면마다 다시 구현하지 않았는지.
- 확정 금액이 있는 행(90000011/90000001)·범칙금(90000002)·경고(90000004)·규칙이 못 알아보는 '과태료' 문구
  (90000202)·금액 문구 자체가 없는 행은 추정이 없어야 한다(표시 없음). 금액 미확인 bus_lane(90000005)·
  parking(90000101/90000103) 은 /data/* 화면과 같은 금액으로 추정되어야 한다.
- 대시보드(dashboard.py)는 모바일 API(web/routers/api_route.py)와 data_service.get_dashboard_stats() 를
  공유한다 — 그 서비스 함수 자체의 반환값에는 추정 필드가 생기지 않고(계약 불변), 웹 라우터에서만 붙는지를
  확인한다. entry_value 를 못 채우면 세부 규칙이 틀어지므로(일반 '과태료' 로만 떨어짐) 정확한 금액까지 본다.
- CSV 내보내기가 있는 화면은 없다(watchlist/rating/duplicate_groups/대시보드 템플릿에 CSV 버튼/로직이
  없음을 grep 으로 확인) — 이번 확장의 시험 범위에는 내보내기 열 추가 대상이 없다.
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sqlalchemy import create_engine

from core.utils import logger
from scripts.dev import fixture_server
from services import data_service, fine_estimate
from web.routers import filters as web_filters


def _seeded_engine(test_case):
    directory = tempfile.TemporaryDirectory()
    test_case.addCleanup(directory.cleanup)
    engine = create_engine(f"sqlite:///{Path(directory.name) / 'data.db'}")
    test_case.addCleanup(engine.dispose)
    fixture_server.seed_engine(engine)
    return engine


def _request(path="/"):
    from starlette.requests import Request
    return Request({"type": "http", "method": "GET", "path": path, "query_string": b"",
                     "headers": [], "session": {}})


class SharedHelperIdentityTest(unittest.TestCase):
    """재구현 금지: 네 라우터 모두 web/routers/filters.with_fine_estimates 를 그대로 쓴다(같은 함수 객체)."""

    def test_data_py_delegates_to_shared_helper(self):
        from web.routers import data as web_data
        self.assertIs(web_data._with_fine_estimates, web_filters.with_fine_estimates)

    def test_with_fine_estimates_reuses_judgement_function(self):
        with mock.patch.object(web_filters.fine_estimate, "estimate_if_unknown",
                                wraps=fine_estimate.estimate_if_unknown) as spy:
            records = [{"범칙금_과태료": "과태료", "category": "traffic", "entry_value": "자동차·교통위반-신호위반",
                        "신고명": "버스전용차로 위반", "위반법규": "도로교통법 제15조", "차량번호": "14가1004",
                        "사진_첫촬영": None, "사진_끝촬영": None, "발생시각": None}]
            web_filters.with_fine_estimates(records)
        self.assertGreater(spy.call_count, 0)
        self.assertEqual(records[0]["fine_estimate_amount"], 50000)
        record_arg = spy.call_args_list[0].args[1]
        self.assertEqual(set(record_arg), set(fine_estimate.FINE_ESTIMATE_FIELDS))


class WatchlistScreenTest(unittest.TestCase):
    """watchlist_route.view_watchlist — watchlist.html 표의 과태료/범칙금 칸 전용 표시값."""

    def setUp(self):
        logger.LoggerFactory.create_logger(mode="crawl")
        self.engine = _seeded_engine(self)
        rnums = data_service.resolve_to_report_numbers(
            self.engine, ["90000005", "90000011", "90000002", "90000004", "90000003"])
        data_service.update_watchlist_status(self.engine, rnums, "Y")

    def _records(self):
        from web.routers import watchlist_route as wr
        with mock.patch.object(wr, "engine", self.engine), \
                mock.patch.object(wr.templates, "TemplateResponse", side_effect=lambda req, name, ctx: ctx):
            ctx = wr.view_watchlist(_request("/watchlist/"))
        return {r["ID"]: r for r in ctx["records"]}

    def test_unknown_fine_bus_lane_gets_estimate(self):
        by_id = self._records()
        row = by_id["90000005"]
        self.assertEqual(row["범칙금_과태료"], "과태료")
        self.assertEqual(row["fine_estimate_amount"], 50000)
        self.assertIn("승용등", row["fine_estimate_basis"])

    def test_confirmed_amount_row_has_no_estimate(self):
        by_id = self._records()
        row = by_id["90000011"]
        self.assertEqual(row["범칙금_과태료"], "과태료 40,000원")
        self.assertIsNone(row["fine_estimate_amount"])

    def test_penalty_row_has_no_estimate(self):
        by_id = self._records()
        self.assertIsNone(by_id["90000002"]["fine_estimate_amount"])

    def test_warning_row_has_no_estimate(self):
        by_id = self._records()
        self.assertIsNone(by_id["90000004"]["fine_estimate_amount"])

    def test_not_applicable_row_has_no_estimate(self):
        by_id = self._records()
        row = by_id["90000003"]
        self.assertEqual(row["범칙금_과태료"], "")
        self.assertIsNone(row["fine_estimate_amount"])


class RatingScreenTest(unittest.TestCase):
    """rating_route.view_rating_page — rating.html 표(만족도조사 미참여 대상)."""

    def setUp(self):
        logger.LoggerFactory.create_logger(mode="crawl")
        self.engine = _seeded_engine(self)

    def _records(self):
        from web.routers import rating_route as rr
        with mock.patch.object(rr, "engine", self.engine), \
                mock.patch.object(rr.templates, "TemplateResponse", side_effect=lambda req, name, ctx: ctx):
            ctx = rr.view_rating_page(_request("/rating/"))
        return {r["ID"]: r for r in ctx["records"]}

    def test_unknown_fine_bus_lane_gets_estimate(self):
        """90000005 는 별점 미참여 대상에도 있다(fixture)."""
        by_id = self._records()
        row = by_id["90000005"]
        self.assertEqual(row["범칙금_과태료"], "과태료")
        self.assertEqual(row["fine_estimate_amount"], 50000)

    def test_confirmed_amount_row_has_no_estimate(self):
        by_id = self._records()
        row = by_id["90000011"]
        self.assertEqual(row["범칙금_과태료"], "과태료 40,000원")
        self.assertIsNone(row["fine_estimate_amount"])

    def test_unmatched_rule_row_has_no_estimate(self):
        """90000202: '과태료'(미확인)지만 규칙이 못 알아본다 — 추정을 억지로 만들지 않는다."""
        by_id = self._records()
        row = by_id["90000202"]
        self.assertEqual(row["범칙금_과태료"], "과태료")
        self.assertIsNone(row["fine_estimate_amount"])

    def test_empty_fine_text_row_has_no_estimate(self):
        by_id = self._records()
        self.assertIsNone(by_id["90000003"]["fine_estimate_amount"])


class DuplicateGroupsScreenTest(unittest.TestCase):
    """duplicate_route.view_duplicate_groups — duplicate_groups.html 의 대표건·멤버 표."""

    def setUp(self):
        logger.LoggerFactory.create_logger(mode="crawl")
        self.engine = _seeded_engine(self)

    def _groups(self):
        from web.routers import duplicate_route as dr
        with mock.patch.object(dr, "engine", self.engine), \
                mock.patch.object(dr.templates, "TemplateResponse", side_effect=lambda req, name, ctx: ctx):
            ctx = dr.view_duplicate_groups(_request("/duplicates/manage"))
        return ctx["groups"]

    def test_members_and_representative_carry_fine_estimate_fields(self):
        groups = self._groups()
        self.assertTrue(groups, "fixture 중복군이 있어야 한다")
        group = groups[0]
        for member in group["members"]:
            self.assertIn("fine_estimate_amount", member)
            self.assertIn("fine_estimate_basis", member)

    def test_confirmed_amount_representative_has_no_estimate(self):
        """fixture 중복군 대표건(90000011)은 확정 금액이 있어 추정이 없어야 한다."""
        groups = self._groups()
        representative = groups[0]["representative"]
        self.assertEqual(representative.get("범칙금_과태료"), "과태료 40,000원")
        self.assertIsNone(representative.get("fine_estimate_amount"))

    def test_representative_shares_object_with_matching_member(self):
        """representative 와 멤버 목록의 같은 신고는 같은 객체 참조다 — 멤버에 붙인 추정이 대표건에도 그대로 보인다."""
        groups = self._groups()
        group = groups[0]
        representative = group["representative"]
        matching = next(m for m in group["members"] if m.get("is_representative"))
        self.assertIs(representative, matching)


class DashboardScreenTest(unittest.TestCase):
    """dashboard.dashboard — index.html 의 감시 목록·최근 답변 표. 모바일 API 계약 불변도 함께 본다."""

    def setUp(self):
        logger.LoggerFactory.create_logger(mode="crawl")
        self.engine = _seeded_engine(self)
        rnums = data_service.resolve_to_report_numbers(
            self.engine, ["90000005", "90000011", "90000002", "90000004", "90000003"])
        data_service.update_watchlist_status(self.engine, rnums, "Y")

    def _watchlist(self):
        from web.routers import dashboard as dash
        with mock.patch.object(dash, "engine", self.engine), \
                mock.patch.object(dash.templates, "TemplateResponse", side_effect=lambda req, name, ctx: ctx):
            ctx = dash.dashboard(_request("/"))
        return {r["ID"]: r for r in ctx["watchlist"]}

    def test_unknown_fine_bus_lane_gets_estimate_with_entry_value_backfill(self):
        """services/stats 의 _REPORT_FIELDS 화이트리스트엔 entry_value 가 없다 — 대시보드가 보충해야
        data.py(/data/traffic) 와 같은 50000원(승용등)이 나온다. 보충하지 않으면 세부 규칙이 틀어진다."""
        by_id = self._watchlist()
        row = by_id["90000005"]
        self.assertEqual(row["범칙금_과태료"], "과태료")
        self.assertEqual(row["fine_estimate_amount"], 50000)
        self.assertIn("승용등", row["fine_estimate_basis"])

    def test_confirmed_amount_row_has_no_estimate(self):
        by_id = self._watchlist()
        row = by_id["90000011"]
        self.assertEqual(row["범칙금_과태료"], "과태료 40,000원")
        self.assertIsNone(row["fine_estimate_amount"])

    def test_penalty_and_warning_rows_have_no_estimate(self):
        by_id = self._watchlist()
        self.assertIsNone(by_id["90000002"]["fine_estimate_amount"])
        self.assertIsNone(by_id["90000004"]["fine_estimate_amount"])

    def test_dashboard_stats_service_itself_unaffected(self):
        """모바일 API(web/routers/api_route.py)가 쓰는 data_service.get_dashboard_stats() 자체는
        추정 필드를 붙이지 않는다 — 붙이는 책임은 web/routers/dashboard.py 뿐(계약 불변)."""
        stats = data_service.get_dashboard_stats(self.engine, mode="raw")
        for record in stats.get("watchlist", []):
            self.assertNotIn("fine_estimate_amount", record)
            self.assertNotIn("fine_estimate_basis", record)
            self.assertNotIn("entry_value", record)


if __name__ == "__main__":
    unittest.main()
