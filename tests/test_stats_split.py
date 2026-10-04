"""EO R-05: 통계 서비스 분리(services/stats/*) 뒤의 계약.

- 공개 진입점 services.report_stats_service 가 예전 이름(내부 도우미 포함)을 그대로 내보낸다.
- 파생 지표·집계 함수는 입력 프레임을 바꾸지 않는다(호출자가 복사해야 하는 암묵 계약을 없앴다).
- 층 방향: reads·metrics·common 은 응답 조립 모듈(dashboard·agency·overview·map)을 부르지 않는다.
분리 전후 전체 JSON 응답 동일성은 분리 때 128 조합으로 확인했다(docs/reviews/2026-10-05-eo-refactor.md).
"""

import ast
import json
import os
import pathlib
import tempfile
import unittest

os.environ.setdefault("SAFETYREPORT_DATA_DIR", tempfile.mkdtemp(prefix="sr-r05-"))

import pandas as pd

from services import report_stats_service as facade

ROOT = pathlib.Path(__file__).resolve().parents[1]
ROWS = [row for case in json.loads((ROOT / "contracts" / "stats-overview-vectors.json").read_text(encoding="utf-8"))["cases"]
        for row in case["rows"]]

PURE = ("_build_stats_tables", "_summarize_overview_frame", "_stats_row_disposition_counts", "_estimated_fine_totals",
        "_build_status_breakdown", "_build_disposition_breakdown", "_build_agency_breakdown", "_overview_disposition",
        "_overview_fine_amount", "_overview_report_types", "_overview_violation_laws", "_calc_avg_days_with_count",
        "_calc_avg_rating", "_prepare_metrics", "_apply_registry_agency_display", "_exclude_withdraw_rows")


class FacadeTest(unittest.TestCase):
    def test_public_and_helper_names_are_still_exported(self):
        for name in ("get_dashboard_stats", "get_agency_stats", "get_stats_page", "get_stats_overview", "get_report_map_stats",
                     "get_report_map_missing_summary", "get_report_map_missing_groups", "get_last_sync_label",
                     "_apply_stats_row_filters", "_apply_stats_law_filter", "_load_stats_frames", "_build_stats_query",
                     *PURE):
            self.assertTrue(callable(getattr(facade, name)), name)


class PurityTest(unittest.TestCase):
    def test_reducers_do_not_change_their_input(self):
        frame = pd.DataFrame(ROWS)
        for name in PURE:
            with self.subTest(name=name):
                before = frame.copy(deep=True)
                getattr(facade, name)(frame)
                self.assertEqual(list(frame.columns), list(before.columns))
                pd.testing.assert_frame_equal(frame, before)

    def test_row_filters_return_new_frames(self):
        frame = pd.DataFrame(ROWS)
        before = frame.copy(deep=True)
        facade._apply_stats_row_filters(frame, {"reportName": "신호", "excludePolice": True})
        facade._apply_stats_law_filter(frame, {"law": "__없음__"})
        pd.testing.assert_frame_equal(frame, before)


class LayerTest(unittest.TestCase):
    def test_lower_layers_do_not_import_response_assembly(self):
        assembly = {"services.stats.dashboard", "services.stats.agency", "services.stats.overview", "services.stats.map"}
        for module in ("common", "reads", "metrics"):
            tree = ast.parse((ROOT / "services" / "stats" / f"{module}.py").read_text(encoding="utf-8"))
            imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
            self.assertFalse(imported & assembly, module)


if __name__ == "__main__":
    unittest.main()
