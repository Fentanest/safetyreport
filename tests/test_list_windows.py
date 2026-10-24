"""공식 목록 API 제한(2026-10-24 실측): 조회 기간 최대 1년, 한 번에 최대 50건.

가짜 공식 서버가 같은 제한(초과 시 400 request_limited)을 강제하고, 실제 crawl_titles 가
연도 구간을 이어 붙여 예전 한 번 조회와 같은 결과(누락·중복 없음, 최신순)를 내는지 본다. 합성 데이터만 쓴다.
"""
import datetime
import unittest
from unittest import mock

from core.crawler import crawltitle_api as titles


class FakeSite:
    def __init__(self, rows):
        self.rows = rows  # [(C_NO, 'YYYY-MM-DD HH:MM')]
        self.requests = []

    def get(self, session, url, params=None, timeout=None):
        self.requests.append(dict(params))
        frm, to = params["C_FRM_DATE"], params["C_TO_DATE"]
        a, b = int(params["startRowNum"]), int(params["endRowNum"])
        if (datetime.date.fromisoformat(to) - datetime.date.fromisoformat(frm)).days > 365:
            return {"error": "HTTP 400"}, session
        if b - a + 1 > 50:
            return {"error": "HTTP 400"}, session
        in_range = sorted((r for r in self.rows if frm <= r[1][:10] <= to),
                          key=lambda r: (r[1], r[0]), reverse=True)
        page = in_range[a - 1:b]
        return {"totalCnt": len(in_range),
                "result": [{"C_NO": cid, "C_DATE": date, "STTEMNT_NO": "SPP-" + cid,
                            "C_A_TITLE": "fixture", "C_NOW": 0, "STSFDG_SCORE": 0}
                           for cid, date in page]}, session


def synthetic_rows():
    today = datetime.date.today()
    rows, n = [], 0
    for y in range(today.year, today.year - 5, -1):
        for md in ("01-01", "03-15", "06-30", "12-31"):
            if datetime.date.fromisoformat(f"{y}-{md}") > today:
                continue
            for k in range(27):
                rows.append((f"R{n:04d}", f"{y}-{md} {k % 24:02d}:00"))
                n += 1
    rows.append(("R9999", today.isoformat()))
    return rows


class ListWindowTests(unittest.TestCase):
    def setUp(self):
        titles._listing_state.direct = None
        titles._listing_state.browser = None

    def test_windows_newest_first_within_one_year_and_contiguous(self):
        for today in (datetime.date(2026, 10, 24), datetime.date(2024, 12, 31),
                      datetime.date(2028, 2, 29), datetime.date(2026, 1, 1)):
            windows = titles._list_windows(today)
            self.assertEqual(windows[0][1], today.isoformat())
            self.assertEqual(windows[-1][0], "2014-01-01")
            for i, (frm, to) in enumerate(windows):
                days = (datetime.date.fromisoformat(to) - datetime.date.fromisoformat(frm)).days
                self.assertTrue(0 <= days <= 365)
                if i:
                    gap = datetime.date.fromisoformat(windows[i - 1][0]) - datetime.date.fromisoformat(to)
                    self.assertEqual(gap.days, 1)

    def crawl(self, site):
        progress = {}
        with mock.patch.object(titles.direct_login, "make_authorized_session", return_value=(object(), None)), \
             mock.patch.object(titles, "get_authorized_json", side_effect=site.get), \
             mock.patch.object(titles.logger.LoggerFactory, "logbot", mock.Mock()), \
             mock.patch.object(titles, "sleep"):
            frames, _ = titles.crawl_titles(progress=progress)
        return frames, progress

    def test_real_crawl_titles_collects_every_report_once_in_newest_first_order(self):
        rows = synthetic_rows()
        site = FakeSite(rows)
        frames, progress = self.crawl(site)
        self.assertTrue(progress["list_ok"], progress)
        self.assertEqual(progress["total"], len(rows))
        ids = [str(v) for frame in frames for v in frame.iloc[:, 0].tolist()]
        expected = [cid for cid, _ in sorted(rows, key=lambda r: (r[1], r[0]), reverse=True)]
        self.assertEqual(ids, expected)
        # 제한을 넘은 요청은 가짜 사이트가 HTTP 400 으로 돌려 위 단언이 실패한다 — 모든 요청이 제한 안이었다.
        self.assertTrue(all(int(p["endRowNum"]) - int(p["startRowNum"]) < 50 for p in site.requests))

    def test_report_added_while_paging_fails_coverage_instead_of_skipping(self):
        today = datetime.date.today().isoformat()
        rows = [(f"A{i:03d}", today) for i in range(400)]
        site = FakeSite(rows)
        original = site.get
        calls = {"n": 0}

        def get(session, url, params=None, timeout=None):
            calls["n"] += 1
            # 구간 건수 13번 + 1페이지 4번 뒤, 2페이지(201~400행)를 받는 중에 올해 신고가 하나 늘었다
            if calls["n"] == 20:
                rows.append(("A999", today))
            return original(session, url, params=params, timeout=timeout)

        site.get = get
        _, progress = self.crawl(site)
        self.assertFalse(progress["list_ok"])

    def test_official_rejection_is_reported_as_first_page_failure(self):
        with mock.patch.object(titles, "get_authorized_json", return_value=({"error": "HTTP 400"}, None)), \
             mock.patch.object(titles.direct_login, "make_authorized_session", return_value=(object(), None)), \
             mock.patch.object(titles.logger.LoggerFactory, "logbot", mock.Mock()), \
             mock.patch.object(titles, "sleep"):
            progress = {}
            frames, last = titles.crawl_titles(progress=progress)
        self.assertEqual((frames, last), ([], 0))
        self.assertFalse(progress["list_ok"])
        self.assertEqual(progress["first_error"], "HTTP 400")

    def test_report_number_years(self):
        self.assertEqual(titles.report_number_years(["SPP-2610-0123456"]), {2026})
        self.assertEqual(titles.report_number_years(["2406-1", "spp-2512-7"]), {2024, 2025, 2026})
        self.assertEqual(titles.report_number_years(["SPP-2501-7"]), {2025, 2024})
        for bad in (["SPP-2613-1"], ["SPP-2610-1", "partial"], ["SPP-2610"], []):
            self.assertIsNone(titles.report_number_years(bad), bad)

    def test_year_scoped_crawl_asks_only_that_year(self):
        today = datetime.date.today()
        last = today.year - 1
        rows = [("Y1", f"{last}-05-01 10:00"), ("Y2", f"{last}-12-31 09:00"), ("N1", today.isoformat())]
        site = FakeSite(rows)
        progress = {}
        with mock.patch.object(titles.direct_login, "make_authorized_session", return_value=(object(), None)), \
             mock.patch.object(titles, "get_authorized_json", side_effect=site.get), \
             mock.patch.object(titles.logger.LoggerFactory, "logbot", mock.Mock()), \
             mock.patch.object(titles, "sleep"):
            frames, _ = titles.crawl_titles(progress=progress, years={last})
        self.assertTrue(progress["list_ok"])
        self.assertEqual(progress["total"], 2)
        self.assertEqual([str(v) for f in frames for v in f.iloc[:, 0].tolist()], ["Y2", "Y1"])
        self.assertEqual({p["C_FRM_DATE"] for p in site.requests}, {f"{last}-01-01"})


if __name__ == "__main__":
    unittest.main()
