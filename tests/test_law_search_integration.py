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
                # 웹 목록 검색칸(lawExact 없음)은 검색 의미: 쉼표 OR·& AND·법령명 없는 조 번호.
                self.assertEqual({r['ID'] for r in get_category_records(self.engine,'traffic',{'law':q},mode='raw')},expected)
                first=get_report_page(self.engine,'traffic',limit=1,mode='raw',search_filter={'law':q})
                self.assertEqual(first['total'],len(expected))
                ids=[]
                for offset in range(len(expected)):ids.extend(r['ID'] for r in get_report_page(self.engine,'traffic',offset=offset,limit=1,mode='raw',search_filter={'law':q})['data'])
                self.assertEqual(set(ids),expected);self.assertEqual(len(ids),len(set(ids)))

    def test_lawexact_is_stored_key_membership(self):
        # 2026-10-16: lawExact=true(통계 막대·상세 링크)는 저장 키 소속 판정(matches_law_keys): 키 그대로, 하위 항·호 포함,
        # 쉼표·& 분해와 법령명 없는 조 번호 검색은 하지 않는다. 같은 키의 웹 목록·모바일 검색(lawExact)·통계 필터가 같은 행을 본다.
        for q,expected in [('도로교통법 제17조',{'2','3'}),('도로교통법 제17조제4항',{'2'}),('도로교통법 제34조',{'1'}),
                           ('제34조',set()),('도로교통법 제33조,자동차관리법 제10조',set()),('도로교통법 제32조\n도로교통법 제34조',{'1'})]:
            with self.subTest(q=q):
                self.assertEqual({r['ID'] for r in get_category_records(self.engine,'traffic',{'law':q,'lawExact':True},mode='raw')},expected)
                page=get_report_page(self.engine,'traffic',limit=200,mode='raw',search_filter={'law':q,'lawExact':True})
                self.assertEqual({r['ID'] for r in page['data']},expected);self.assertEqual(page['total'],len(expected))

    def test_overview_bar_counts_equal_law_filter_and_list(self):
        # /stats/overview·/api/v1/stats/overview 의 위반법규 막대 건수 == 그 filter 의 통계 필터 total == 목록(lawExact) 행 수.
        extra=[('6','도로교통법'),('7','도로교통법 제5조, 제6조'),('8','도로교통법 제32조의21항'),('9','주차장법 제33조')]
        rows=[dict(self.rows[0],ID=i,신고번호=f'SYN-{int(i):04}',위반법규=law) for i,law in extra]
        with self.engine.begin() as c:c.execute(models.report_effective_table.insert(),rows)
        bars=get_stats_overview(self.engine,mode='raw',taxonomy=5)['traffic']['violation_laws']
        self.assertIn('도로교통법 제5조, 제6조',[b['name'] for b in bars])
        for bar in bars:
            with self.subTest(bar=bar['name']):
                filtered=get_stats_overview(self.engine,{'law':bar['filter']},mode='raw',taxonomy=5)['traffic']
                self.assertEqual(filtered['total'],bar['count'])
                listed=get_category_records(self.engine,'traffic',{'law':bar['filter'],'lawExact':True},mode='raw')
                self.assertEqual(len(listed),bar['count'])

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
