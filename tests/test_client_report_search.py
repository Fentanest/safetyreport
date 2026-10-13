import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from sqlalchemy import create_engine
from core.database import database, models
from services.report_query_service import get_report_page
from services.client_report_search import validate_filter

VECTORS = json.loads((Path(__file__).resolve().parents[1] / 'contracts/client-search-vectors.json').read_text())


class ClientSearchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.engine = create_engine('sqlite:///' + str(Path(self.tmp.name) / 'test.db'))
        self.addCleanup(self.engine.dispose)
        models.metadata.create_all(self.engine)
        with self.engine.begin() as conn:
            for sql in database._index_statements():
                conn.exec_driver_sql(sql)
            conn.execute(models.report_effective_table.insert(), VECTORS['rows'])
        self.settings = patch('settings.settings.exclude_withdraw', False)
        self.settings.start()
        self.addCleanup(self.settings.stop)

    def test_vectors_total_order_and_pages(self):
        for case in VECTORS['cases']:
            for category in (None, 'traffic', 'parking', 'safety', 'living', 'other'):
                expected = [rid for rid in case['ids'] if category is None or
                            next(r for r in VECTORS['rows'] if r['ID'] == rid)['category'] == category]
                ids = []
                for offset in range(0, len(expected) + 1, 3):
                    page = get_report_page(self.engine, category, offset=offset, limit=3,
                                           search_filter=case['filter'])
                    self.assertEqual(page['total'], len(expected), (case, category))
                    ids += [r['ID'] for r in page['data']]
                    for row in page['data']:
                        self.assertEqual(row['첨부사진'], 'synthetic.png')
                        self.assertEqual(row['보완_요청_내용'], '합성 보완 요청')
                self.assertEqual(ids, expected, (case, category))

    def test_beyond_200_dedupe_and_mutation_no_stale_count(self):
        table = models.report_effective_table
        with self.engine.begin() as conn:
            conn.execute(table.delete())
            conn.execute(table.insert(), [dict(VECTORS['rows'][0], ID=str(i), 신고번호=f'SYN-{1000-i:04}',
                                              신고명='hit' if i >= 250 else 'miss') for i in range(850)])
            conn.execute(models.duplicate_group_table.insert().values(group_id='g', fingerprint='synthetic', match_type='field', status='confirmed_duplicate', representative_id='251'))
            conn.execute(models.duplicate_member_table.insert(), [
                {'group_id':'g','report_id':'250','report_number':'SYN-0750','category':'traffic','is_representative':0},
                {'group_id':'g','report_id':'251','report_number':'SYN-0749','category':'traffic','is_representative':1}])
        for mode, total in [('raw', 600), ('canonical', 599)]:
            old = []
            for offset in range(0, 850, 200):
                old += [r for r in get_report_page(self.engine, 'traffic', offset=offset, mode=mode)['data'] if r['신고명'] == 'hit']
            for offset in (0, 200, 400, 800):
                page = get_report_page(self.engine, 'traffic', offset=offset, mode=mode, search_filter={'name':'hit'})
                self.assertEqual(page['total'], total)
                self.assertEqual(page['data'], old[offset:offset+200])
        with self.engine.begin() as conn:
            conn.execute(table.update().where(table.c.ID == '849').values(신고명='miss'))
        self.assertEqual(get_report_page(self.engine, None, search_filter={'name':'hit'})['total'], 598)
        with self.engine.begin() as conn:
            conn.execute(models.duplicate_group_table.update().values(status='not_duplicate'))
        self.assertEqual(get_report_page(self.engine, None, search_filter={'name':'hit'})['total'], 599)

    def test_registry_police_and_settings_are_read_each_time(self):
        with patch('services.client_report_search.resolve_display_agency', return_value=('현행 경찰서','resolved')):
            self.assertEqual(get_report_page(self.engine, None, search_filter={'agency':'현행','onlyPolice':True})['total'], 32)
        self.assertEqual(get_report_page(self.engine, None, search_filter={'agency':'현행'})['total'], 0)
        with patch('settings.settings.exclude_withdraw', True):
            self.assertEqual(get_report_page(self.engine, None, search_filter={})['total'], 24)

    def test_index_avoids_temp_sort(self):
        with self.engine.connect() as conn:
            for where in ('', "WHERE category='traffic'"):
                plan = str(conn.exec_driver_sql(f'SELECT * FROM pragma_index_list("mysafety_report_effective")').all())
                self.assertIn('ix_effective_number_id', plan)
                plan = str(conn.exec_driver_sql(f'EXPLAIN QUERY PLAN SELECT ID FROM mysafety_report_effective {where} ORDER BY 신고번호 DESC, ID DESC LIMIT 200').all())
                self.assertNotIn('TEMP B-TREE', plan)

    def test_http_auth_version_filter_echo_and_validation(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from web.routers import api_route
        from core.utils import logger
        logger.LoggerFactory.create_logger(mode='crawl')
        key = database.create_api_key(self.engine, 'synthetic-search')
        app = FastAPI()
        app.include_router(api_route.router)
        with patch.object(api_route, 'engine', self.engine), TestClient(app) as client:
            path = '/api/v1/reports/all/search'
            self.assertEqual(client.get(path, params={'search_version':1}).status_code, 401)
            headers = {'X-API-Key':key, 'X-SR-Taxonomy':'5'}
            filters = {'name':'école'}
            response = client.get(path, headers=headers, params={
                'search_version':1, 'filter':json.dumps(filters), 'limit':3})
            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertEqual(body['search_version'], 1)
            self.assertEqual(body['filter'], filters)
            self.assertEqual(body['total'], 8)
            self.assertEqual(body['count'], 3)
            self.assertIn('신고내용', body['data'][0])
            for query in ({'search_version':2}, {'search_version':1, 'filter':'{"bogus":1}'},
                          {'search_version':1, 'filter':'bad'}, {'search_version':1,'dedupe':'unknown'}):
                self.assertEqual(client.get(path, headers=headers, params=query).status_code, 400)
            self.assertEqual(client.get(path, headers=headers, params={'search_version':1,'limit':201}).status_code,422)

    def test_invalid_filters_rejected(self):
        for value in ([], {'unknown':''}, {'ratings':'1'}, {'onlyPolice':1}):
            with self.assertRaises(ValueError):
                validate_filter(value)
