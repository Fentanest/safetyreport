import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from sqlalchemy import create_engine
from core.database import database, models
from services.report_query_service import get_report_page, get_category_records
from services.report_stats_service import get_agency_stats, get_stats_overview
from services.law_values import read_laws
from services.parser import _violation_law

MULTI='도로교통법 제32조\n도로교통법 제33조\n도로교통법 제34조'
class LawSearchIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.engine=create_engine('sqlite:///'+str(Path(self.tmp.name)/'test.db'));self.addCleanup(self.engine.dispose)
        models.metadata.create_all(self.engine)
        self.rows=[]
        parsed = _violation_law('도로교통법 제32조, 제33조, 제34조 위반. 신고된 차량에 대해 과태료부과 처분을 하였습니다.')
        self.assertEqual(parsed, MULTI)
        for i,law in enumerate([parsed,'도로교통법 제17조제4항\n자동차관리법 제10조','도로교통법 제17조','도로교통법 제170조','도로교통법 제17조의2'],1):
            self.rows.append({'ID':str(i),'신고번호':f'SYN-{i:04}','category':'traffic','신고명':'합성 법규 검색','신고일':'2026-10-01','답변일':'2026-10-02','처리상태':'수용','상태':'답변완료','범칙금_과태료':'과태료: 40,000원','위반법규':law,'처리기관':'합성 기관','담당자':'합성 담당','entry_value':'자동차·교통위반-기타 자동차 안전기준 위반','ssnpc_cd':'TV','ssnpc_type':'09'})
        with self.engine.begin() as c:c.execute(models.report_effective_table.insert(),self.rows)
        p=patch('settings.settings.exclude_withdraw',False);p.start();self.addCleanup(p.stop)

    def test_web_and_api_query_paths_and_pages(self):
        for q,expected in [('도로교통법 제33조',{'1'}),('제34조',{'1'}),('도로교통법 제17조',{'2','3'}),('도로교통법 제170조',{'4'}),('도로교통법 제17조의2',{'5'}),('도로교통법 제32조&도로교통법 제34조',{'1'}),('도로교통법 제33조,자동차관리법 제10조',{'1','2'})]:
            with self.subTest(q=q):
                self.assertEqual({r['ID'] for r in get_category_records(self.engine,'traffic',{'law':q,'lawExact':True},mode='raw')},expected)
                first=get_report_page(self.engine,'traffic',limit=1,mode='raw',search_filter={'law':q})
                self.assertEqual(first['total'],len(expected))
                ids=[]
                for offset in range(len(expected)):ids.extend(r['ID'] for r in get_report_page(self.engine,'traffic',offset=offset,limit=1,mode='raw',search_filter={'law':q})['data'])
                self.assertEqual(set(ids),expected);self.assertEqual(len(ids),len(set(ids)))

    def test_stats_options_hierarchy_and_unique_money(self):
        stats=get_agency_stats(self.engine,mode='raw',taxonomy=5)['traffic']
        self.assertEqual(stats['total_fine_amount'],200000)
        self.assertTrue(all('\n' not in law for law in stats['available_laws']))
        self.assertIn('도로교통법 제33조',stats['available_laws'])
        bylaw={r['law']:r for r in stats['by_law']}
        self.assertEqual(bylaw['도로교통법 제17조']['total'],2)
        self.assertEqual(bylaw['도로교통법 제33조']['total'],1)
        filtered=get_agency_stats(self.engine,{'law':'도로교통법 제17조'},mode='raw',taxonomy=5)['traffic']
        self.assertEqual(filtered['total_fine_amount'],80000)
        self.assertEqual(filtered['available_laws'],stats['available_laws'])

    def test_http_authenticated_search(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from web.routers import api_route
        from core.utils import logger
        logger.LoggerFactory.create_logger(mode='crawl')
        key=database.create_api_key(self.engine,'synthetic-law-search');app=FastAPI();app.include_router(api_route.router)
        with patch.object(api_route,'engine',self.engine),TestClient(app) as client:
            path='/api/v1/reports/all/search';params={'search_version':1,'filter':json.dumps({'law':'도로교통법 제34조'})}
            self.assertEqual(client.get(path,params=params).status_code,401)
            response=client.get(path,headers={'X-API-Key':key,'X-SR-Taxonomy':'5'},params=params)
            self.assertEqual(response.status_code,200);data=response.json()
            self.assertEqual(data['total'],1);self.assertEqual(data['data'][0]['ID'],'1')
            self.assertEqual(read_laws(data['data'][0]['위반법규']),tuple(MULTI.splitlines()))

    def test_updates_do_not_leave_stale_search_matches(self):
        self.assertEqual(get_report_page(self.engine,'traffic',search_filter={'law':'제34조'})['total'],1)
        with self.engine.begin() as c:c.execute(models.report_effective_table.update().where(models.report_effective_table.c.ID=='1').values(위반법규='도로교통법 제23조'))
        self.assertEqual(get_report_page(self.engine,'traffic',search_filter={'law':'제34조'})['total'],0)

    def test_browser_form_crlf_does_not_create_law_override(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from web.routers import db_editor_route
        from sqlalchemy import select
        with self.engine.begin() as c:
            c.execute(models.report_table.insert(), self.rows)
        app=FastAPI();app.include_router(db_editor_route.router)
        with patch.object(db_editor_route,'engine',self.engine),TestClient(app) as client:
            response=client.post('/db-editor/traffic/1',data={'위반법규':MULTI.replace('\n','\r\n'),'담당자':'합성 변경'},follow_redirects=False)
            self.assertEqual(response.status_code,303)
        with self.engine.connect() as c:
            laws=c.execute(select(models.report_override_table).where(models.report_override_table.c.column_name=='위반법규')).all()
            self.assertEqual(laws,[])
            self.assertEqual(c.execute(select(models.report_effective_table.c['위반법규']).where(models.report_effective_table.c.ID=='1')).scalar_one(),MULTI)
