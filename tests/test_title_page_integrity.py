import unittest
from unittest import mock
from core.crawler import crawltitle_api as titles


def row(identifier):
    return {'C_NO': identifier, 'STTEMNT_NO': 'SPP-' + str(identifier),
            'C_A_TITLE': 'fixture', 'C_NOW': 0, 'STSFDG_SCORE': 0}


class TitlePageIntegrityTests(unittest.TestCase):
    def crawl(self, pages, page_range=None):
        progress = {}
        with mock.patch.object(titles.direct_login, 'make_authorized_session', return_value=(object(), None)), \
             mock.patch.object(titles, '_fetch_api_page', side_effect=lambda *a: (pages.pop(0), a[0])) as fetch, \
             mock.patch.object(titles.logger.LoggerFactory, 'logbot', mock.Mock()), \
             mock.patch.object(titles, 'sleep'):
            frames, last = titles.crawl_titles(progress=progress, page_range=page_range)
        return frames, progress, fetch.call_count

    def test_first_page_is_reused_and_unique_full_coverage_succeeds(self):
        frames, progress, calls = self.crawl([
            {'totalCnt': 201, 'result': [row(i) for i in range(200)]},
            {'totalCnt': 201, 'result': [row(200)]}])
        self.assertTrue(progress['list_ok'])
        self.assertEqual(sum(len(frame) for frame in frames), 201)
        self.assertEqual(calls, 2)

    def test_short_last_page_duplicate_id_changed_total_and_missing_id_fail_coverage(self):
        first = {'totalCnt': 201, 'result': [row(i) for i in range(200)]}
        for last in ({'totalCnt': 201, 'result': []}, {'totalCnt': 201, 'result': [row(0)]},
                     {'totalCnt': 202, 'result': [row(200)]},
                     {'totalCnt': 201, 'result': [{'C_NO': None}]}):
            with self.subTest(last=last):
                frames, progress, calls = self.crawl([first, last])
                self.assertFalse(progress['list_ok'])
                self.assertIn(2, progress['pages_failed'])
                self.assertGreaterEqual(sum(len(frame) for frame in frames), 200)

    def test_zero_is_valid_but_missing_or_invalid_count_is_not_an_empty_success(self):
        self.assertTrue(self.crawl([{'totalCnt': 0, 'result': []}])[1]['list_ok'])
        for payload in ({'result': []}, {'totalCnt': -1, 'result': []},
                        {'totalCnt': 0, 'result': [row(1)]}, []):
            self.assertFalse(self.crawl([payload])[1]['list_ok'])
