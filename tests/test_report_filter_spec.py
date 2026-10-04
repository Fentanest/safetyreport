"""EO R-02: 신고 필터 사양(contracts/report-filter-vectors.json, 모바일과 바이트 동일).

- 순수 판정·pandas 최종 판정이 벡터와 같다.
- SQL 후보 축소는 최종 결과의 상위 집합이고, SQL → pandas → 법규 단계를 거친 통계 경로가 벡터와 같다.
- 웹 목록 JS 가 같은 행을 고른다.
"""

import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

os.environ.setdefault("SAFETYREPORT_DATA_DIR", tempfile.mkdtemp(prefix="sr-r02-"))

import pandas as pd
from sqlalchemy import Column, MetaData, String, Table, create_engine, select

from services import report_policy
from services import report_stats_service as stats
from services.report_filter_spec import ReportFilterSpec, parse_groups, range_key

ROOT = pathlib.Path(__file__).resolve().parents[1]
VECTORS = json.loads((ROOT / "contracts" / "report-filter-vectors.json").read_text(encoding="utf-8"))
ROWS = VECTORS["rows"]
STATS_CASES = [c for c in VECTORS["cases"] if "stats" in c["targets"]]
COLUMNS = ["ID", "신고명", "위반장소", "처리기관", "처리기관코드", "신고일", "발생일자", "답변일", "발생시각", "위반법규", "처리상태"]


def _frame():
    return pd.DataFrame([{**row, "ID": str(i)} for i, row in enumerate(ROWS)])


class PureAndPandasTest(unittest.TestCase):
    def test_pure_matches_vectors(self):
        for case in VECTORS["cases"]:
            spec = ReportFilterSpec.from_filters(case["filters"])
            statuses = case["filters"].get("statuses") or []
            got = [i for i, row in enumerate(ROWS)
                   if spec.matches_row(row) and spec.matches_law(row)
                   and (not statuses or report_policy.display_status(row["처리상태"]) in statuses)]
            self.assertEqual(got, case["expected"], case["name"])

    def test_pandas_matches_vectors(self):
        for case in STATS_CASES:
            spec = ReportFilterSpec.from_filters(case["filters"])
            frame = spec.apply_law(spec.apply_rows(_frame()))
            self.assertEqual(sorted(int(i) for i in frame["ID"]), case["expected"], case["name"])
            via_service = stats._apply_stats_law_filter(stats._apply_stats_row_filters(_frame(), case["filters"]),
                                                        case["filters"])
            self.assertEqual(sorted(int(i) for i in via_service["ID"]), case["expected"], case["name"])

    def test_parse_and_range_helpers(self):
        self.assertEqual(parse_groups(" A & b , ,c "), (("a", "b"), ("c",)))
        self.assertEqual(parse_groups(" , & "), ())
        self.assertEqual(range_key("2024-02-29 10:00", False), "2024-02-29")
        self.assertIsNone(range_key("2023-02-29", False))
        self.assertIsNone(range_key("2026-01-01X", False))
        self.assertEqual(range_key("08:05:30", True), "08:05")
        self.assertIsNone(range_key("24:00", True))


class SqlCandidatesTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        meta = MetaData()
        self.table = Table("t", meta, *[Column(name, String) for name in COLUMNS])
        meta.create_all(self.engine)
        with self.engine.begin() as conn:
            for i, row in enumerate(ROWS):
                conn.execute(self.table.insert().values(ID=str(i), **row))

    def _candidates(self, filters):
        query = stats._build_stats_query(self.table, filters, column_names=COLUMNS)
        with self.engine.connect() as conn:
            return pd.read_sql_query(query, conn)

    def test_sql_is_superset_and_stats_path_is_exact(self):
        for case in STATS_CASES:
            with self.subTest(case=case["name"]):
                frame = self._candidates(case["filters"])
                ids = {int(i) for i in frame["ID"]}
                self.assertTrue(set(case["expected"]) <= ids, f"SQL 이 후보를 놓쳤다: {set(case['expected']) - ids}")
                final = stats._apply_stats_law_filter(stats._apply_stats_row_filters(frame, case["filters"]),
                                                      case["filters"])
                self.assertEqual(sorted(int(i) for i in final["ID"]), case["expected"])

    def test_police_candidates_keep_rows_whose_registry_name_may_change(self):
        with self.engine.begin() as conn:
            conn.execute(self.table.insert().values(ID="coded", 처리기관="OO서", 처리기관코드="1234567"))
        for filters in ({"onlyPolice": True}, {"excludePolice": True}):
            ids = set(self._candidates(filters)["ID"])
            self.assertIn("coded", ids, filters)

    def test_non_ascii_cased_term_is_not_pushed_to_sql(self):
        spec = ReportFilterSpec.from_filters({"reportName": "Ä"})
        self.assertEqual(spec.sql_candidates(self.table), [])
        spec = ReportFilterSpec.from_filters({"reportName": "abc"})
        self.assertEqual(len(spec.sql_candidates(self.table)), 1)


class BrowserFilterTest(unittest.TestCase):
    def test_data_table_cell_renderers(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is unavailable")
        result = subprocess.run([node, str(ROOT / "tests/js/data_table_cells_check.js")], cwd=ROOT,
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_js_matches_vectors(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is unavailable")
        result = subprocess.run([node, str(ROOT / "tests/js/report_filter_check.js")], cwd=ROOT,
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
