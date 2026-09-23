import unittest

import pandas as pd

from services import report_stats_service


class ReportStatsServiceTest(unittest.TestCase):
    def test_disposition_breakdown_keeps_reject_bucket_and_splits_unconfirmed(self):
        frame = pd.DataFrame([
            {"처리상태": "수용", "범칙금_과태료": "과태료: 40,000원"},
            {"처리상태": "답변완료", "범칙금_과태료": "경고"},
            {"처리상태": "기타", "범칙금_과태료": ""},
            {"처리상태": "처리중", "범칙금_과태료": "미확인"},
        ])

        items = report_stats_service._build_disposition_breakdown(frame)
        counts = {item["label"]: item["count"] for item in items}

        self.assertEqual(counts["과태료"], 1)
        self.assertEqual(counts["경고/범칙금"], 1)
        self.assertEqual(counts["불수용/기타"], 1)
        self.assertEqual(counts["미확인"], 1)
        self.assertNotIn("기타/미확인", counts)


    def test_overview_summary_uses_valid_samples_and_splits_monthly_bases(self):
        frame = pd.DataFrame([
            {"처리상태": "수용", "신고일": "2026-01-30", "답변일": "2026-02-02 10:00:00"},
            {"처리상태": "일부수용", "신고일": "2026-02-10", "답변일": "2026-02-20"},
            {"처리상태": "처리중", "신고일": "2026-02-11", "답변일": ""},
            {"처리상태": "보완요청", "신고일": "2026-02-12", "답변일": None},
            {"처리상태": "기타", "신고일": "2026-03-05", "답변일": "2026-03-01"},
            {"처리상태": "취하", "신고일": "잘못된날짜", "답변일": "2026-03-09"},
            {"처리상태": None, "신고일": None, "답변일": None},
        ])

        summary = report_stats_service._summarize_overview_frame(frame)

        self.assertEqual(summary["total"], 7)
        self.assertEqual(summary["completed"], 3)
        self.assertEqual(summary["accept"], 1)
        self.assertEqual(summary["partial"], 1)
        self.assertEqual(summary["reject"], 1)
        self.assertEqual(summary["processing"], 1)
        self.assertEqual(summary["supplement"], 1)
        self.assertEqual(summary["withdraw"], 1)
        # 표본은 두 날짜가 모두 유효하고 차이 >= 0 인 행만: 3일, 10일
        self.assertEqual(summary["avg_days_count"], 2)
        self.assertEqual(summary["avg_days"], 6.5)
        self.assertEqual(summary["reversed_date_count"], 1)
        self.assertEqual(summary["undated_report_count"], 2)
        self.assertEqual(
            summary["monthly_reported"],
            [{"month": "2026-01", "count": 1}, {"month": "2026-02", "count": 3}, {"month": "2026-03", "count": 1}],
        )
        self.assertEqual(
            summary["monthly_answered"],
            [{"month": "2026-02", "count": 2}, {"month": "2026-03", "count": 2}],
        )

    def test_overview_summary_handles_empty_frame(self):
        summary = report_stats_service._summarize_overview_frame(pd.DataFrame())
        self.assertEqual(summary["total"], 0)
        self.assertIsNone(summary["avg_days"])
        self.assertEqual(summary["avg_days_count"], 0)
        self.assertEqual(summary["monthly_reported"], [])
        self.assertEqual(summary["monthly_answered"], [])

if __name__ == "__main__":
    unittest.main()
