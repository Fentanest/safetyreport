"""Astra 교차 확인의 기관 충돌·옛 기관·담당자 확장 재현. 합성 DB만 쓴다."""
import html
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from starlette.middleware.sessions import SessionMiddleware

from core.database import models
from core.utils.logger import LoggerFactory
from scripts.dev.fixture_server import seed_engine
from services import agency_registry, report_query_service as query, report_stats_service as stats
from services.client_report_search import compile_predicate, validate_filter, IDENTITY_SEARCH_CAPABILITY

ROOT = Path(__file__).resolve().parents[1]


class StatsDrillIdentityTest(unittest.TestCase):
    def setUp(self):
        LoggerFactory.create_logger(mode='crawl')
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.engine = create_engine(f'sqlite:///{directory.name}/identity.db')
        self.addCleanup(self.engine.dispose)
        self.seed = seed_engine(self.engine)
        self.old_code = next(iter(agency_registry.snapshot().multi))
        rows = [self.row('target', '0000', '9999991', '합성기관', '김민수')]
        rows += [self.row(f'other-{i}', f'{i+1:04}', '9999992', '합성기관', '김민수(교통과)') for i in range(200)]
        rows += [self.row('old', '0300', self.old_code, '옛기관', '이담당')]
        with self.engine.begin() as conn:
            conn.execute(models.report_effective_table.delete())
            conn.execute(models.report_effective_table.insert(), rows)

    @staticmethod
    def row(rid, number, code, agency, manager):
        return {'ID': rid, '신고번호': f'SYN-{number}', 'category': 'traffic',
                '처리기관코드': code, '처리기관': agency, '담당자': manager,
                '신고일': '2026-10-01', '답변일': '2026-10-02',
                '처리상태': '수용', '상태': '답변완료', '위반법규': '합성 법규'}

    def test_identity_before_count_and_limit_and_stats_population(self):
        for mode in ('raw', 'canonical'):
            tables = stats.get_agency_stats(self.engine, {'year': '2026', 'law': '합성 법규'}, mode=mode)['traffic']
            for kind in ('by_agency', 'by_person'):
                for row in tables[kind]:
                    filters = {'agencyKey': row['agency_key'], 'agency': '반드시 무시할 표시명',
                               'statuses': ['수용'], 'law': '합성 법규', 'lawExact': True,
                               'responseDateStart': '2026-01-01', 'responseDateEnd': '2026-12-31'}
                    if kind == 'by_person':
                        filters.update(manager=row['person'], managerExact=True)
                    page = query.get_report_page(self.engine, 'traffic', search_filter=filters, mode=mode)
                    self.assertEqual(page['total'], row['total'])
                    self.assertEqual(page['count'], row['total'])
                    self.assertIsNone(page['next_offset'])
                    self.assertEqual(query.get_report_page(self.engine, None, offset=200, search_filter=filters, mode=mode)['data'], [])
            page = query.get_report_page(self.engine, None, search_filter={'agencyKey': 'src:9999991:합성기관'})
            self.assertEqual((page['total'], [r['ID'] for r in page['data']]), (1, ['target']))
        # 원래 이름 검색은 201건·2페이지. 정확 판정은 이것을 페이지 뒤에 거르지 않는다.
        broad = query.get_report_page(self.engine, None, search_filter={'agency': '합성기관'})
        self.assertEqual((broad['total'], broad['count'], broad['next_offset']), (201, 200, 200))
        self.assertNotIn('target', [r['ID'] for r in broad['data']])
        for f, expected in (({'manager': '김민수'}, 201), ({'manager': ' 김민수 ', 'managerExact': True}, 1)):
            self.assertEqual(query.get_report_page(self.engine, None, search_filter=f)['total'], expected)
        display, key = agency_registry.resolve_stats_agency(self.old_code, '옛기관')
        self.assertEqual(display, '(구)옛기관')
        old = query.get_report_page(self.engine, None, search_filter={'agencyKey': key, 'agency': display})
        self.assertEqual((old['total'], old['data'][0]['ID']), (1, 'old'))

    def test_types_normalization_and_literal_manager(self):
        self.assertEqual(validate_filter({'agencyKey': 'src:-:기관', 'managerExact': True}), {'agencyKey': 'src:-:기관', 'managerExact': True})
        for invalid in ({'agencyKey': 1}, {'agencyKey': None}, {'managerExact': 'true'}, {'managerExact': 1}, {'unknown': ''}):
            with self.assertRaises(ValueError): validate_filter(invalid)
        # 통계 표가 담당자를 앞뒤 공백만 지우고 대소문자를 살려 묶으므로 정확 일치도 대소문자를 구분한다(Opus 검수 결정).
        for value, term, expected in [(' 김민수 ', '김민수', 1), ('김민수(교통과)', '김민수', 0),
                                      (' A,B&C ', 'A,B&C', 1), (' A,B&C ', 'a,b&c', 0), ('A', 'A,B&C', 0),
                                      (' ÉCOLE ', 'ÉCOLE', 1), (' ÉCOLE ', 'école', 0), ('Lee', 'LEE', 0),
                                      ('Straße', 'STRASSE', 0), (None, '', 1)]:
            cols, predicate = compile_predicate({'manager': term, 'managerExact': True})
            self.assertEqual(cols, ['담당자'])
            self.assertEqual(predicate(value), expected)
            from services.report_filter_spec import ReportFilterSpec
            web = ReportFilterSpec.from_filters({'person': term, 'personExact': True})
            self.assertEqual(web.matches_row({'담당자': value}), bool(expected))

    def test_web_render_and_identity_filters_without_http_transport(self):
        from starlette.requests import Request
        from web.routers import api_route, stats as stats_route, data as data_route
        import subprocess
        app = FastAPI()
        app.mount('/static', StaticFiles(directory=ROOT / 'web/static'), name='static')
        request = Request({'type': 'http', 'method': 'GET', 'path': '/stats/content',
                           'query_string': b'', 'headers': [], 'scheme': 'http',
                           'server': ('fixture', 80), 'app': app, 'router': app.router, 'session': {}})
        self.assertIn(IDENTITY_SEARCH_CAPABILITY, api_route.get_app_config('fixture')['data']['capabilities'])
        with patch.object(stats_route, 'engine', self.engine), \
             patch.object(stats_route.sunwi_service, 'get_dashboard_payload', return_value={}):
            response = stats_route.view_stats(request, year='2026', law='합성 법규', dedupe='raw')
        links = [html.unescape(link) for link in re.findall(r'data-href="([^"]+)"', response.body.decode())]
        links = [link for link in links if 'person=' in link]
        self.assertTrue(links)
        for link in links:
            parsed = urlsplit(link)
            params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            self.assertEqual(params['personExact'], 'true')
            req = Request({'type': 'http', 'query_string': parsed.query.encode(), 'headers': []})
            filters = data_route._identity_filters(req, data_route._build_filters(
                status=params.get('status'), agency=params.get('agency'), person=params.get('person'),
                agencyExact=True, law=params.get('law')))
            category = parsed.path.rsplit('/', 1)[1]
            records = (query.get_all_records(self.engine, filters, mode='raw') if category == 'all'
                       else query.get_category_records(self.engine, category, filters, mode='raw'))
            expected = 200 if params['person'] == '김민수(교통과)' else 1
            self.assertEqual(len(records), expected)
            self.assertEqual({r['담당자'] for r in records}, {params['person']})
        with self.engine.begin() as conn:
            conn.execute(models.report_effective_table.update().where(models.report_effective_table.c.ID == 'target').values(담당자=' 김민수 '))
        self.assertEqual(len(query.get_traffic_records(self.engine, {'person': '김민수', 'personExact': True})), 1)
        self.assertEqual(query.get_traffic_records(self.engine, {'person': '김민수'}), [])
        result = subprocess.run(['node', 'tests/js/stats_identity_check.js'], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_capability_and_web_person_links_match_table_count(self):
        from web.routers import api_route, stats as stats_route, data as data_route
        app = FastAPI()
        app.include_router(api_route.router)
        app.include_router(stats_route.router)
        app.include_router(data_route.router)
        app.add_middleware(SessionMiddleware, secret_key='synthetic-identity')
        app.mount('/static', StaticFiles(directory=ROOT / 'web/static'), name='static')
        with patch.object(api_route, 'engine', self.engine), patch.object(stats_route, 'engine', self.engine), \
             patch.object(data_route, 'engine', self.engine), \
             patch.object(stats_route.sunwi_service, 'get_dashboard_payload', return_value={}), TestClient(app) as client:
            config = client.get('/api/v1/app/config', headers={'X-API-Key': self.seed['api_key']}).json()['data']
            self.assertIn(IDENTITY_SEARCH_CAPABILITY, config['capabilities'])
            self.assertEqual(config['report_search_version'], 1)
            response = client.get('/stats/content?year=2026&law=합성%20법규&dedupe=raw')
            self.assertEqual(response.status_code, 200)
            links = [html.unescape(link) for link in re.findall(r'data-href="([^"]+)"', response.text)]
            person_links = [link for link in links if 'person=' in link]
            self.assertTrue(person_links)
            for link in person_links:
                params = parse_qs(urlsplit(link).query)
                self.assertEqual(params['personExact'], ['true'])
                page = client.get(link)
                self.assertEqual(page.status_code, 200)
                records = page.context['records']
                expected = 200 if params['person'] == ['김민수(교통과)'] else 1
                self.assertEqual(len(records), expected)
                self.assertEqual({r['담당자'] for r in records}, {params['person'][0]})
            # 새 정확 조건만 정규화. 기존 person 링크는 원문 완전 일치를 보존한다.
            with self.engine.begin() as conn:
                conn.execute(models.report_effective_table.update().where(models.report_effective_table.c.ID == 'target').values(담당자=' 김민수 '))
            self.assertEqual(len(query.get_traffic_records(self.engine, {'person': '김민수', 'personExact': True})), 1)
            self.assertEqual(query.get_traffic_records(self.engine, {'person': '김민수'}), [])
