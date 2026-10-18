"""전체 탭의 합·행 분류·API 추가 호환·웹 렌더 계약. 합성 자료만 사용한다."""
import json
from collections import defaultdict
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

import settings.settings as settings
from core.utils import logger
from scripts.dev.fixture_server import seed_engine
from services import report_category, report_stats_service as stats
from web.routers import api_route, stats as stats_route

ROOT = Path(__file__).resolve().parents[1]
VECTOR = json.loads((ROOT / 'contracts/stats-all-vectors.json').read_text())
COUNTS = ('total', 'fines', 'warnings', 'rejects', 'unconfirmed', 'in_progress',
          'disposition_unknown', 'no_penalty', 'unclassified', 'total_fine_amount',
          'fine_amount_unknown', 'estimated_fine_amount', 'estimated_fine_count',
          'avg_days_count', 'rating_count')
# by_law 는 분류별 합이 아니다: 전체 탭은 모든 분류 행을 한 범위로 상위 조문에 묶는다(법규 필터·overview.all 과 같은 소속).
TABLES = ('by_agency', 'by_person', 'police_by_agency', 'police_by_person',
          'other_by_agency', 'other_by_person')


def assert_sums(test, records, categories):
    for table in TABLES:
        keys = ('agency_key', 'person') if 'person' in table else ('agency_key',)
        expected = defaultdict(lambda: {field: 0 for field in COUNTS})
        for cat in categories:
            for row in records[cat][table]:
                for field in COUNTS:
                    expected[tuple(row[k] for k in keys)][field] += row[field]
        actual = {tuple(row[k] for k in keys): {field: row[field] for field in COUNTS}
                  for row in records['all'][table]}
        test.assertEqual(len(actual), len(records['all'][table]), table)
        test.assertEqual(actual, dict(expected), table)
    for field in ('total_fine_amount', 'estimated_fine_amount', 'estimated_fine_count'):
        test.assertEqual(records['all'].get(field, 0), sum(records[c].get(field, 0) for c in categories), field)
    test.assertEqual(records['all']['available_laws'], sorted({law for c in categories for law in records[c]['available_laws']}))
    test.assertEqual(records['all'].get('has_empty_law', False), any(records[c].get('has_empty_law', False) for c in categories))


class AllVectorTests(unittest.TestCase):
    def test_shared_vectors_keep_category_metrics_and_regroup(self):
        frame = pd.DataFrame(VECTOR['rows'])
        expected = VECTOR['expected']
        for excluded in (False, True):
            with patch.object(settings._instance, 'exclude_withdraw', excluded):
                frames = {c: stats._prepare_metrics(frame[frame.category == c]) for c in report_category.CATEGORIES}
                records = stats._compute_agency_stats(['2026'], frames)
                overview = stats._compute_stats_overview(['2026'], frames)
                assert_sums(self, records, report_category.CATEGORIES)
                self.assertEqual(overview['all']['total'], expected['total_without_withdraw' if excluded else 'total_with_withdraw'])
                # 안전신고에는 제32조제1항만 있다. 분류별 표의 제32조(교통 2·주정차 1)에는 빠지지만 전체 표 제32조에는 들어간다.
                laws = {r['law']: r['total'] for r in records['all']['by_law']}
                self.assertEqual(laws['도로교통법 제32조'], 4)
                self.assertEqual(laws['도로교통법 제32조제1항'], 2)
                self.assertNotIn('도로교통법 제32조', {r['law'] for r in records['safety']['by_law']})
                for law, total in laws.items():
                    filtered = stats._compute_agency_stats(['2026'], frames, {'law': law})
                    self.assertEqual({r['law']: r['total'] for r in filtered['all']['by_law']}[law], total, law)
                self.assertEqual(sum(r['total'] for r in records['all']['by_agency']), expected['table_total'])
                self.assertEqual(sum(r['total'] for r in records['all']['by_person']), expected['person_total'])
                self.assertEqual(records['all']['total_fine_amount'], expected['confirmed_amount'])
                self.assertEqual(records['all']['estimated_fine_amount'], expected['estimated_amount'])
                self.assertEqual(records['all']['estimated_fine_count'], expected['estimated_count'])
                shared = next(r for r in records['all']['by_agency'] if r['agency'] == '합성 공통기관')
                self.assertEqual(shared['total'], expected['shared_agency_total'])
                self.assertEqual(shared['avg_days'], expected['shared_agency_avg_days'])
                self.assertEqual(shared['avg_rating'], expected['shared_agency_avg_rating'])
                # parking 에만 있는 법규가 전체 선택지에도 있으며, 필터는 모든 분류와 같다.
                from services.law_values import read_laws
                law = read_laws(VECTOR['rows'][1]['위반법규'])[0]
                self.assertNotIn(law, records['traffic']['available_laws'])
                self.assertIn(law, records['all']['available_laws'])
                for selected in (law, '도로교통법 제32조', '__없음__', '없는 법규'):
                    filtered = stats._compute_agency_stats(['2026'], frames, {'law': selected})
                    assert_sums(self, filtered, report_category.CATEGORIES)
                    self.assertEqual(filtered['all']['available_laws'], records['all']['available_laws'])


class AllFixtureTests(unittest.TestCase):
    def setUp(self):
        logger.LoggerFactory.create_logger(mode='crawl')
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.engine = create_engine(f'sqlite:///{self.directory.name}/data.db')
        self.addCleanup(self.engine.dispose)
        self.seeded = seed_engine(self.engine)

    def test_raw_canonical_taxonomy_filters_and_cached_page(self):
        totals = {}
        for taxonomy, cats in ((3, report_category.LEGACY_CATEGORIES), (5, report_category.CATEGORIES)):
            for mode in ('raw', 'canonical'):
                for excluded in (False, True):
                    with patch.object(settings._instance, 'exclude_withdraw', excluded):
                        for filters in ({}, {'law': '__없음__'}, {'agency': '강서', 'excludePolice': True}, {'year': '1900'}):
                            with self.subTest(taxonomy=taxonomy, mode=mode, excluded=excluded, filters=filters):
                                records, overview = stats.get_stats_page(self.engine, filters, mode, taxonomy)
                                assert_sums(self, records, cats)
                                self.assertEqual(overview['all']['total'], sum(overview[c]['total'] for c in cats))
                                self.assertEqual(records, stats.get_agency_stats(self.engine, filters, mode, taxonomy))
                                json.dumps(records, allow_nan=False)
                                if not filters and not excluded:
                                    totals[mode] = overview['all']['total']
        self.assertEqual(totals, {'raw': 24, 'canonical': 23})

    def test_api_adds_all_and_preserves_every_existing_category(self):
        app = FastAPI()
        app.include_router(api_route.router)
        with patch.object(api_route, 'engine', self.engine), TestClient(app) as client:
            for taxonomy, cats in ((3, report_category.LEGACY_CATEGORIES), (5, report_category.CATEGORIES)):
                response = client.get('/api/v1/stats?dedupe=raw', headers={
                    'X-API-Key': self.seeded['api_key'], 'X-SR-Taxonomy': str(taxonomy)})
                self.assertEqual(response.status_code, 200)
                payload = response.json()['data']
                self.assertEqual(set(payload), {*cats, 'all', 'available_years', 'traffic_total_fine', 'dedupe_mode'})
                _, frames = stats._load_stats_frames(self.engine, mode='raw', taxonomy=taxonomy)
                for cat in cats:
                    # 같은 분류만 넣은 기존 계산은 전체 추가로 바뀌지 않는다.
                    single = stats._compute_agency_stats([], {cat: stats._prepare_metrics(frames[cat])})
                    self.assertEqual(payload[cat], single[cat])
                assert_sums(self, payload, cats)

    def test_web_shell_and_content_have_all_button_panes_and_links(self):
        app = FastAPI()
        app.include_router(stats_route.router)
        from starlette.middleware.sessions import SessionMiddleware
        app.add_middleware(SessionMiddleware, secret_key='synthetic-stats-all')
        # base 템플릿의 정적 파일 경로만 제공하며 서버/외부 서비스는 실행하지 않는다.
        from fastapi.staticfiles import StaticFiles
        app.mount('/static', StaticFiles(directory=ROOT / 'web/static'), name='static')
        with patch.object(stats_route, 'engine', self.engine), \
             patch.object(stats_route.sunwi_service, 'get_dashboard_payload', return_value={}), TestClient(app) as client:
            for path in ('/stats', '/stats/content'):
                response = client.get(path)
                self.assertEqual(response.status_code, 200)
                html = response.text
                self.assertLess(html.index('data-cat="all"'), html.index('data-cat="traffic"'))
                for kind in ('agency', 'person', 'police-agency', 'police-person', 'other-agency', 'other-person'):
                    self.assertIn(f'id="all-{kind}"', html)
                self.assertIn('id="statsLawSidebar"', html)
                if path.endswith('content'):
                    self.assertIn('data-href="/data/all?', html)

    def test_browser_defaults_and_saved_categories(self):
        import subprocess
        result = subprocess.run(['node', str(ROOT / 'tests/js/stats_all_check.js')], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
