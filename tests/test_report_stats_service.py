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

    def test_fine_amount_unknown_is_distinct_from_zero(self):
        frame = pd.DataFrame({"범칙금_과태료": ["과태료: 40,000원", "과태료", "과태료 부과 예정", "경고", "", None]})
        self.assertEqual(report_stats_service._count_fine_amount_unknown(frame), 2)
        self.assertFalse(report_stats_service._is_fine_amount_unknown("경고"))
        self.assertFalse(report_stats_service._is_fine_amount_unknown("과태료: 40,000원"))
        # 점을 천 단위 구분자로 쓴 답변(모바일 extractFineAmount 와 같은 규칙)
        self.assertEqual(report_stats_service._extract_fine_amount("과태료: 40.000원"), 40000)
        self.assertFalse(report_stats_service._is_fine_amount_unknown("과태료: 40.000원"))

    def test_round_half_up_matches_mobile(self):
        # Python round() 는 23.25 → 23.2(짝수 쪽). 모바일 toStringAsFixed 와 같게 23.3.
        self.assertEqual(report_stats_service._round_half_up(23.25, 1), 23.3)
        self.assertEqual(report_stats_service._round_half_up(0.35, 1), 0.3)  # 0.35 는 이진수로 0.3499…
        self.assertEqual(report_stats_service._round_half_up(4.125, 2), 4.13)

    def test_law_filter_is_exact_match(self):
        frame = pd.DataFrame({"위반법규": ["도로교통법", "도로교통법 제32조", " 도로교통법 ", None, ""]})
        exact = report_stats_service._apply_stats_law_filter(frame, {"law": "도로교통법"})
        self.assertEqual(len(exact), 2)  # 앞뒤 공백만 무시, 제32조는 제외
        empty = report_stats_service._apply_stats_law_filter(frame, {"law": "__없음__"})
        self.assertEqual(len(empty), 2)

    def test_stats_tables_include_only_answered_rows(self):
        # 2026-09-28 사용자 결정(S-10 대체): 표는 답변 완료(수용·일부수용·불수용·기타·답변완료) 신고만.
        # 처리중·진행·보완요청·취하는 기관·담당자 값이 있어도 넣지 않는다. 같은 입력·기대값: 모바일 test/services/stats_tables_test.dart
        frame = pd.DataFrame([
            {"처리기관": "A구청", "담당자": "김", "처리상태": "수용", "범칙금_과태료": "과태료: 40,000원", "신고일": "2026-01-01", "답변일": "2026-01-11", "위반법규": "도로교통법"},
            {"처리기관": "A구청", "담당자": "김", "처리상태": "처리중", "범칙금_과태료": "", "신고일": "2026-01-05", "답변일": "2026-01-06", "위반법규": ""},
            {"처리기관": " A구청 ", "담당자": "", "처리상태": "진행", "범칙금_과태료": None, "신고일": "2026-01-07", "답변일": None, "위반법규": None},
            {"처리기관": "A구청", "담당자": "미지정", "처리상태": "답변완료", "범칙금_과태료": "", "신고일": "2026-01-01", "답변일": "2026-01-05", "위반법규": ""},
            {"처리기관": "A구청", "담당자": "이", "처리상태": "취하", "범칙금_과태료": "", "신고일": "2026-01-01", "답변일": "2026-01-02", "위반법규": ""},
            {"처리기관": "", "담당자": "", "처리상태": "처리중", "범칙금_과태료": "", "신고일": "2026-01-08", "답변일": "", "위반법규": ""},
            {"처리기관": None, "담당자": None, "처리상태": None, "범칙금_과태료": None, "신고일": None, "답변일": None, "위반법규": None},
            {"처리기관": "B경찰서", "담당자": "박", "처리상태": "보완요청", "범칙금_과태료": "", "신고일": "2026-02-01", "답변일": "", "위반법규": ""},
        ])

        agency, person, law = report_stats_service._build_stats_tables(frame)
        by_agency = {row["agency"]: row for row in agency}
        by_person = {(row["agency"], row["person"]): row for row in person}

        # B경찰서는 보완요청 1건뿐이라 표에 없다. 기관이 빈 신고도 없다.
        self.assertEqual(set(by_agency), {"A구청"})
        a = by_agency["A구청"]
        self.assertEqual(a["total"], 2)  # 수용 + 답변완료(담당자 미지정)
        self.assertEqual(a["fines"], 1)
        self.assertEqual(a["in_progress"], 0)
        self.assertEqual(a["unconfirmed"], 1)
        self.assertEqual(a["avg_days"], 7.0)  # 10일, 4일
        self.assertEqual(a["avg_days_count"], 2)

        # 담당자표: 기관·담당자가 있고('미지정' 제외) 답변 완료 — 취하 '이' 는 빠진다
        self.assertEqual(set(by_person), {("A구청", "김")})
        self.assertEqual(by_person[("A구청", "김")]["total"], 1)
        self.assertEqual(by_person[("A구청", "김")]["avg_days"], 10.0)

        self.assertEqual([row["law"] for row in law], ["도로교통법"])

    def test_partial_accept_on_fine_menus_is_fine_unknown(self):
        # 2026-09-28 사용자 결정: 주정차·버스전용차로·쓰레기 메뉴의 일부수용은 보통 과태료가 아니다 → 과태료 미확인(disposition_unknown).
        frame = pd.DataFrame([
            {"category": "parking", "처리상태": "일부수용", "범칙금_과태료": "", "entry_value": "불법주정차신고-기타"},
            {"category": "traffic", "처리상태": "일부수용", "범칙금_과태료": "", "entry_value": "자동차·교통위반-버스전용차로 위반"},
            {"category": "other", "처리상태": "일부수용", "범칙금_과태료": "", "entry_value": "안전신고-쓰레기, 폐기물"},
            {"category": "other", "처리상태": "일부수용", "범칙금_과태료": "", "entry_value": "안전신고-생활안전"},
            {"category": "parking", "처리상태": "수용", "범칙금_과태료": "과태료", "entry_value": "불법주정차신고-기타"},
        ])
        counts = report_stats_service._stats_row_disposition_counts(frame)
        self.assertEqual(counts["disposition_unknown"], 3)
        self.assertEqual(counts["no_penalty"], 1)  # 생활안전 일부수용
        self.assertEqual(counts["fines"], 1)
        self.assertEqual(counts["unclassified"], 0)

    def test_overview_avg_days_uses_completed_rows_only(self):
        frame = pd.DataFrame([
            {"처리상태": "수용", "신고일": "2026-01-01", "답변일": "2026-01-11"},
            {"처리상태": "처리중", "신고일": "2026-01-01", "답변일": "2026-01-02"},  # 이송 답변일
            {"처리상태": "취하", "신고일": "2026-01-01", "답변일": "2026-01-03"},
        ])
        summary = report_stats_service._summarize_overview_frame(frame)
        self.assertEqual(summary["avg_days_count"], 1)
        self.assertEqual(summary["avg_days"], 10.0)
        self.assertEqual(summary["monthly_answered"], [{"month": "2026-01", "count": 3}])


if __name__ == "__main__":
    unittest.main()
