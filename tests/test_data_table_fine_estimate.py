"""신고 목록 화면(/data/<category>)·상세 모달의 추정 과태료 보조 표기 회귀 테스트
(docs/design/statistics-spec.md §4-1·§4-2, PROJECT_RULES §3-2).

- 추정은 DB 에 저장하지 않고 조회 때마다 계산한다. 판정은 services/fine_estimate.estimate_if_unknown 을
  그대로 쓴다(재구현 금지) — 이 테스트는 그 함수가 실제로 호출되는지(wraps) 와, 호출 결과가
  record['fine_estimate_amount']/['fine_estimate_basis'] 로 그대로 노출되는지를 함께 본다.
- 확정 금액이 있는 행(90000001)·범칙금(90000002)·경고(90000004)·규칙이 못 알아보는 '과태료' 문구
  (90000202, 안전신고 쓰레기 메뉴코드 불일치)는 추정이 없어야 한다(표시 없음).
- 모바일 API(exact_values=True, web/routers/api_route.py 경로)는 이 라우터를 거치지 않으므로
  fine_estimate_amount/basis 키가 전혀 없어야 한다(계약 불변).
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sqlalchemy import create_engine

from core.utils import logger
from scripts.dev import fixture_server
from services import data_service, fine_estimate


def _seeded_engine(test_case):
    directory = tempfile.TemporaryDirectory()
    test_case.addCleanup(directory.cleanup)
    engine = create_engine(f"sqlite:///{Path(directory.name) / 'data.db'}")
    test_case.addCleanup(engine.dispose)
    fixture_server.seed_engine(engine)
    return engine


class _RouteTestCase(unittest.TestCase):
    """web/routers/data.py 의 뷰 함수를 직접 호출(TestClient 없이) — tests/test_report_taxonomy_routes.py 와 같은 패턴."""

    _NO_FILTERS = dict(status=None, fine=None, agency=None, person=None, agencyExact=False,
                        law=None, rating=None, ratingCause=None, dedupe=None)

    @staticmethod
    def _request(path="/data/traffic"):
        from starlette.requests import Request
        return Request({"type": "http", "method": "GET", "path": path, "query_string": b"",
                         "headers": [], "session": {}})

    def setUp(self):
        logger.LoggerFactory.create_logger(mode="crawl")
        self.engine = _seeded_engine(self)

    def _records_by(self, view_name, **extra):
        from web.routers import data as web_data
        view = getattr(web_data, view_name)
        kwargs = dict(self._NO_FILTERS, **extra)
        with mock.patch.object(web_data, "engine", self.engine), \
                mock.patch.object(web_data.templates, "TemplateResponse", side_effect=lambda req, name, ctx: ctx):
            context = view(self._request(), **kwargs)
        by_id = {r["ID"]: r for r in context["records"]}
        return context, by_id


class TrafficFineEstimateTest(_RouteTestCase):
    def test_corrected_amount_is_display_only(self):
        from web.routers import data as web_data
        raw = {"범칙금_과태료": "과태료: 40,0000원", "category": "parking",
               "entry_value": "불법주정차신고", "차량번호": "12가3456"}
        row = web_data._with_fine_estimates([raw])[0]
        self.assertEqual(row["범칙금_과태료"], "과태료: 40,0000원")
        self.assertEqual(row["fine_corrected_text"], "과태료: 40,000원")
        self.assertIsNone(row["fine_estimate_amount"])

    def test_unknown_fine_bus_lane_gets_estimate(self):
        """90000005: 버스전용차로 위반 · '과태료'(금액 없음) → bus_lane 규칙, 승용등 5만원."""
        _, by_id = self._records_by("view_traffic")
        row = by_id["90000005"]
        self.assertEqual(row["범칙금_과태료"], "과태료", "확정 원문은 그대로 둔다")
        self.assertEqual(row["fine_estimate_amount"], 50000)
        self.assertIn("승용등", row["fine_estimate_basis"])
        self.assertIn("별표6 제3호", row["fine_estimate_basis"])

    def test_confirmed_amount_row_has_no_estimate(self):
        """90000001: '과태료 40,000원'(확정) → 추정 없음(합치지 않는다)."""
        _, by_id = self._records_by("view_traffic")
        row = by_id["90000001"]
        self.assertEqual(row["범칙금_과태료"], "과태료 40,000원")
        self.assertIsNone(row["fine_estimate_amount"])
        self.assertIsNone(row["fine_estimate_basis"])

    def test_penalty_fine_row_has_no_estimate(self):
        """90000002: '범칙금 60,000원'(과태료 아님) → 추정 없음."""
        _, by_id = self._records_by("view_traffic")
        row = by_id["90000002"]
        self.assertIsNone(row["fine_estimate_amount"])

    def test_warning_row_has_no_estimate(self):
        """90000004: '경고' → 추정 없음."""
        _, by_id = self._records_by("view_traffic")
        row = by_id["90000004"]
        self.assertIsNone(row["fine_estimate_amount"])

    def test_not_applicable_row_has_no_estimate(self):
        """90000003: 범칙금_과태료 값 없음(비대상) → 추정 없음."""
        _, by_id = self._records_by("view_traffic")
        row = by_id["90000003"]
        self.assertEqual(row["범칙금_과태료"], "")
        self.assertIsNone(row["fine_estimate_amount"])

    def test_estimate_reuses_shared_judgement_function(self):
        """재구현 금지: services/fine_estimate.estimate_if_unknown 을 실제로 호출해야 한다(wraps=실제 구현)."""
        from web.routers import data as web_data
        with mock.patch.object(web_data.fine_estimate, "estimate_if_unknown",
                                wraps=fine_estimate.estimate_if_unknown) as spy:
            _, by_id = self._records_by("view_traffic")
        self.assertGreater(spy.call_count, 0)
        self.assertEqual(by_id["90000005"]["fine_estimate_amount"], 50000)
        # 두 번째 인자(record)는 FINE_ESTIMATE_FIELDS 열만 담아 넘긴다(로컬 통계·공유 payload 와 같은 입력).
        for call in spy.call_args_list:
            record_arg = call.args[1]
            self.assertEqual(set(record_arg), set(fine_estimate.FINE_ESTIMATE_FIELDS))


class ParkingFineEstimateTest(_RouteTestCase):
    def test_unknown_fine_parking_gets_estimate(self):
        """90000101/90000103: 불법주정차신고 · '과태료'(금액 없음) → parking 규칙, 승용등 4만원."""
        _, by_id = self._records_by("view_parking")
        for report_id in ("90000101", "90000103"):
            with self.subTest(report_id=report_id):
                row = by_id[report_id]
                self.assertEqual(row["범칙금_과태료"], "과태료")
                self.assertEqual(row["fine_estimate_amount"], 40000)
                self.assertIn("주정차", row["fine_estimate_basis"])

    def test_rows_without_fine_text_have_no_estimate(self):
        _, by_id = self._records_by("view_parking")
        row = by_id["90000102"]
        self.assertIsNone(row["fine_estimate_amount"])


class SafetyFineEstimateTest(_RouteTestCase):
    def test_unmatched_rule_row_has_no_estimate_even_with_fine_text(self):
        """90000202: 범칙금_과태료='과태료'(미확인)이지만 entry_value('안전신고-생활안전')가 어떤 추정
        규칙에도 안 걸린다 — 과태료 문구가 있어도 규칙이 못 알아보면 추정을 억지로 만들지 않는다."""
        _, by_id = self._records_by("view_safety")
        row = by_id["90000202"]
        self.assertEqual(row["범칙금_과태료"], "과태료")
        self.assertIsNone(row["fine_estimate_amount"])
        self.assertIsNone(row["fine_estimate_basis"])


class DuplicatesFineEstimateTest(_RouteTestCase):
    def test_duplicate_view_also_carries_fine_estimate_fields(self):
        """'같은 기능의 화면'(중복 차량 보기)도 data_table.html 을 그대로 쓰므로 같은 필드가 붙는다."""
        from web.routers import data as web_data
        with mock.patch.object(web_data, "engine", self.engine), \
                mock.patch.object(web_data.templates, "TemplateResponse", side_effect=lambda req, name, ctx: ctx):
            context = web_data.view_duplicates(self._request("/data/duplicates"), dedupe=None)
        self.assertTrue(context["records"], "중복 fixture 행이 있어야 한다")
        for row in context["records"]:
            self.assertIn("fine_estimate_amount", row)
            self.assertIn("fine_estimate_basis", row)


class MobileApiContractUnaffectedTest(unittest.TestCase):
    """모바일 API 경로(exact_values=True)는 이 기능을 전혀 거치지 않는다 — 계약 불변 확인."""

    def setUp(self):
        logger.LoggerFactory.create_logger(mode="crawl")
        self.engine = _seeded_engine(self)

    def test_client_page_keeps_law_and_raw_typo_for_mobile_correction(self):
        from core.database import database, models
        from web.routers import api_route
        with self.engine.begin() as conn:
            conn.execute(models.report_table.update().where(models.report_table.c.ID == '90000001').values(
                신고명='끼어들기 금지 위반', 위반법규='도로교통법 제23조',
                차량번호='63너1234', 범칙금_과태료='과태료: 40,0000원'))
        database.rebuild_effective(self.engine)
        with mock.patch.object(api_route, 'engine', self.engine):
            response = api_route.get_reports_page(
                _RouteTestCase._request('/api/v1/reports/traffic/page'), 'traffic',
                offset=0, limit=100, dedupe='raw', _='fixture')
        row = next(row for row in response['data'] if row['ID'] == '90000001')
        self.assertEqual(row['위반법규'], '도로교통법 제23조')
        self.assertEqual(row['범칙금_과태료'], '과태료: 40,0000원')
        self.assertNotIn('fine_corrected_text', row)
        self.assertEqual(fine_estimate.corrected_fine_text(row['범칙금_과태료'], row), '과태료: 40,000원')

    def test_exact_values_records_have_no_fine_estimate_keys(self):
        records = data_service.get_traffic_records(self.engine, None, exact_values=True)
        self.assertTrue(records)
        for row in records:
            self.assertNotIn("fine_estimate_amount", row)
            self.assertNotIn("fine_estimate_basis", row)

    def test_web_default_path_without_route_helper_has_no_fine_estimate_keys(self):
        """data_service 자체(라우터 바깥)는 추정 필드를 붙이지 않는다 — 붙이는 책임은 web/routers/data.py 뿐."""
        records = data_service.get_traffic_records(self.engine, None)
        self.assertTrue(records)
        for row in records:
            self.assertNotIn("fine_estimate_amount", row)
            self.assertNotIn("fine_estimate_basis", row)


if __name__ == "__main__":
    unittest.main()
