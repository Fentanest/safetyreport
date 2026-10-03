"""통계 표·목록·지도는 기관 집계 키와 완료 모집단을 공유한다."""
import os
import tempfile
import unittest
from sqlalchemy import create_engine
from scripts.dev.fixture_server import seed_engine
from scripts.dev.review_regression_fixture import seed_collision_rows
from services import report_query_service as query, report_stats_service as stats


class StatsIdentityScope(unittest.TestCase):
    def setUp(self):
        from core.utils.logger import LoggerFactory
        LoggerFactory.create_logger(mode='crawl')
        fd, self.path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        self.addCleanup(os.remove,self.path)
        self.engine = create_engine(f'sqlite:///{self.path}')
        self.addCleanup(self.engine.dispose)
        seed_engine(self.engine)
        seed_collision_rows(self.engine)

    def test_same_name_agencies_and_people_keep_separate_identity(self):
        for mode in ('raw','canonical'):
            tables = stats.get_agency_stats(self.engine,{},mode=mode)['traffic']
            for kind in ('by_agency','by_person'):
                rows = [r for r in tables[kind] if r['agency']=='검수 동일명 기관']
                self.assertEqual(len(rows),2)
                self.assertEqual(len({r['agency_key'] for r in rows}),2)
                for row in rows:
                    filters={'agencyKey':row['agency_key'],'agency':row['agency'],'status':'완료','law':'검수 법규','lawExact':True}
                    if kind=='by_person': filters['person']=row['person']
                    records = query.get_traffic_records(self.engine,filters,mode=mode)
                    self.assertEqual(len(records),row['total'])
                    self.assertEqual(len(records),1)
                    map_data=stats.get_report_map_stats(self.engine,category='traffic',mode=mode,
                        filters={'targetAgencyKey':row['agency_key'],'targetAgency':row['agency'],
                                 'targetPerson':row.get('person',''),'completedOnly':True,'law':'검수 법규'})
                    self.assertEqual(map_data['meta']['total_reports'],row['total'])
                    self.assertEqual(records[0]['ID'],'991001' if row['fines'] else '991002')
            # 기존 이름 필터 계약은 여전히 두 기관 전체를 반환한다.
            self.assertEqual(len(query.get_traffic_records(self.engine,{'agency':'검수 동일명 기관','agencyExact':True},mode=mode)),2)

    def test_completed_drilldown_does_not_shrink_summary_population(self):
        tables=stats.get_agency_stats(self.engine,{},mode='canonical')['traffic']
        row=next(r for r in tables['by_agency'] if r['agency']=='서울특별시 강서경찰서 교통과')
        records=query.get_traffic_records(self.engine,{'agencyKey':row['agency_key'],'status':'완료'},mode='canonical')
        self.assertEqual(len(records),3)
        filtered=stats.get_report_map_stats(self.engine,category='traffic',filters={'targetAgencyKey':row['agency_key'],'completedOnly':True})
        self.assertEqual(filtered['meta']['total_reports'],3)
        unfiltered=stats.get_report_map_stats(self.engine,category='traffic',filters={'targetAgencyKey':row['agency_key']})
        self.assertEqual(unfiltered['meta']['total_reports'],5)


if __name__=='__main__': unittest.main()
