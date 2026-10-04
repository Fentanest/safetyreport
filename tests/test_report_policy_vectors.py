"""EO R-01: 상태·처분 정책 벡터(contracts/report-policy-vectors.json, 모바일과 바이트 동일).

같은 입력으로 순수 판정·pandas 어댑터·SQL 어댑터·브라우저 JS·통계 서비스가 같은 결과를 내는지 본다.
"""

import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

os.environ.setdefault("SAFETYREPORT_DATA_DIR", tempfile.mkdtemp(prefix="sr-r01-"))

import pandas as pd
from sqlalchemy import Column, MetaData, String, Table, create_engine, select

from services import rating_eligibility, report_policy as policy
from services import report_stats_service as stats

ROOT = pathlib.Path(__file__).resolve().parents[1]
VECTORS = json.loads((ROOT / "contracts" / "report-policy-vectors.json").read_text(encoding="utf-8"))


def _frame(rows):
    return pd.DataFrame(rows, columns=["처리상태", "범칙금_과태료", "category", "entry_value"])


class PolicySetsTest(unittest.TestCase):
    def test_sets_and_trim_chars_match_contract(self):
        sets = VECTORS["sets"]
        self.assertEqual(sets["completed"], list(policy.COMPLETED_ORDER))
        self.assertEqual(sets["processing"], list(policy.PROCESSING_ORDER))
        self.assertEqual(sets["reject"], list(policy.REJECT_ORDER))
        self.assertEqual(set(policy.COMPLETED_ORDER), policy.COMPLETED_STATUSES)
        self.assertEqual(set(policy.PROCESSING_ORDER), policy.PROCESSING_STATUSES)
        self.assertEqual(set(policy.REJECT_ORDER), policy.REJECT_STATUSES)
        self.assertEqual(VECTORS["trim_chars"], policy.TRIM_CHARS)


class PureAndPandasTest(unittest.TestCase):
    def test_status_cases(self):
        for case in VECTORS["status_cases"]:
            status = case["status"]
            with self.subTest(status=status):
                self.assertEqual(policy.display_status(status), case["display"])
                self.assertEqual(policy.breakdown_status(status), case["breakdown"])
                self.assertEqual(policy.badge_key(status), case["badge"])
                self.assertEqual(policy.is_completed(status), case["completed"])
                self.assertEqual(policy.is_processing(status), case["processing"])
                self.assertEqual(policy.is_reject(status), case["reject"])
                self.assertEqual(policy.is_withdrawn(status), case["withdrawn"])
                self.assertEqual(rating_eligibility.canonical_status(status), case["display"])
        frame = pd.DataFrame({"처리상태": [c["status"] for c in VECTORS["status_cases"]]})
        self.assertEqual(policy.display_status_series(frame).tolist(), [c["display"] for c in VECTORS["status_cases"]])
        self.assertEqual(policy.breakdown_status_series(frame).tolist(), [c["breakdown"] for c in VECTORS["status_cases"]])

    def test_disposition_cases_pure_and_pandas(self):
        cases = VECTORS["disposition_cases"]
        frame = _frame([c["row"] for c in cases])
        table = policy.table_disposition_masks(frame)
        dashboard = policy.dashboard_disposition_masks(frame)
        traffic = policy.traffic_dashboard_masks(frame)
        eligible = policy.penalty_eligible_mask(frame)
        menu = policy.partial_unknown_menu_mask(frame)
        for i, case in enumerate(cases):
            row = case["row"]
            with self.subTest(row=row):
                self.assertEqual(policy.table_disposition(row), case["table"])
                self.assertEqual(policy.dashboard_disposition(row), case["dashboard"])
                self.assertEqual(policy.traffic_dashboard(row), case["traffic_dashboard"])
                self.assertEqual(policy.penalty_eligible(row["category"], row["entry_value"]), case["penalty_eligible"])
                self.assertEqual(policy.partial_unknown_menu(row["category"], row["entry_value"]), case["partial_unknown_menu"])
                self.assertEqual({k: bool(v.iloc[i]) for k, v in table.items()}, case["table"])
                self.assertEqual({k: bool(v.iloc[i]) for k, v in dashboard.items()}, case["dashboard"])
                self.assertEqual({k: bool(v.iloc[i]) for k, v in traffic.items()}, case["traffic_dashboard"])
                self.assertEqual(bool(eligible.iloc[i]), case["penalty_eligible"])
                self.assertEqual(bool(menu.iloc[i]), case["partial_unknown_menu"])

    def test_table_disposition_partitions_every_row(self):
        for case in VECTORS["disposition_cases"]:
            table = case["table"]
            decided = table["fines"] or table["warnings"] or table["rejects"]
            self.assertEqual(int(decided) + int(table["in_progress"]) + int(table["unconfirmed"]), 1, case["row"])
            if table["unconfirmed"]:
                parts = [table["disposition_unknown"], table["no_penalty"], table["unclassified"]]
                self.assertEqual(sum(parts), 1, case["row"])

    def test_filter_cases_pure(self):
        rows = VECTORS["filter_rows"]
        for case in VECTORS["filter_cases"]:
            with self.subTest(case=case["name"]):
                if case["kind"] == "status":
                    got = [policy.list_status_filter(case["name"], r["처리상태"]) for r in rows]
                else:
                    got = [policy.list_fine_filter(case["name"], r["범칙금_과태료"], r["처리상태"]) for r in rows]
                self.assertEqual(got, case["matches"])


class SqlAdapterTest(unittest.TestCase):
    """SQLite trim 에 같은 문자 집합을 넘겨 순수 판정과 같은 행을 고르는지."""

    def setUp(self):
        self.engine = create_engine("sqlite://")
        meta = MetaData()
        self.table = Table("t", meta, Column("idx", String), Column("처리상태", String), Column("범칙금_과태료", String))
        meta.create_all(self.engine)
        with self.engine.begin() as conn:
            for i, row in enumerate(VECTORS["filter_rows"]):
                conn.execute(self.table.insert().values(idx=str(i), 처리상태=row["처리상태"], 범칙금_과태료=row["범칙금_과태료"]))

    def _matches(self, clause):
        with self.engine.connect() as conn:
            hits = {row[0] for row in conn.execute(select(self.table.c.idx).where(clause))}
        return [str(i) in hits for i in range(len(VECTORS["filter_rows"]))]

    def test_list_filters(self):
        for case in VECTORS["filter_cases"]:
            with self.subTest(case=case["name"]):
                if case["kind"] == "status":
                    clause = policy.sql_list_status_filter(self.table.c["처리상태"], case["name"])
                else:
                    clause = policy.sql_list_fine_filter(self.table.c["범칙금_과태료"], self.table.c["처리상태"], case["name"])
                self.assertEqual(self._matches(clause), case["matches"])

    def test_not_withdrawn_keeps_null_and_trims(self):
        expected = [not policy.is_withdrawn(r["처리상태"]) for r in VECTORS["filter_rows"]]
        self.assertEqual(self._matches(policy.sql_not_withdrawn(self.table.c["처리상태"])), expected)


class StatsServiceUsesPolicyTest(unittest.TestCase):
    def test_table_and_dashboard_counts_follow_vectors(self):
        cases = VECTORS["disposition_cases"]
        frame = _frame([c["row"] for c in cases])
        counts = stats._stats_row_disposition_counts(frame)
        for key in policy.TABLE_DISPOSITION_KEYS:
            self.assertEqual(counts[key], sum(c["table"][key] for c in cases), key)
        prepared = stats._prepare_metrics(frame.assign(신고일="", 답변일="", 별점=None))
        self.assertEqual(stats._stats_row_disposition_counts(prepared), counts)
        dashboard = stats._disposition_counts(frame)
        for key in policy.DASHBOARD_DISPOSITION_KEYS:
            self.assertEqual(dashboard[key], sum(c["dashboard"][key] for c in cases), key)

    def test_status_breakdown_uses_breakdown_status(self):
        frame = pd.DataFrame({"처리상태": [c["status"] for c in VECTORS["status_cases"]]})
        items = {item["label"]: item["count"] for item in stats._build_status_breakdown(frame)}
        labels = [c["breakdown"] for c in VECTORS["status_cases"]]
        for label in ("수용", "일부수용", "불수용", "기타", "답변완료", "보완요청", "처리중", "취하", "이송"):
            self.assertEqual(items.get(label, 0), labels.count(label), label)


class BrowserPolicyTest(unittest.TestCase):
    def test_js_matches_vectors(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is unavailable")
        result = subprocess.run([node, str(ROOT / "tests/js/report_policy_check.js")], cwd=ROOT,
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
