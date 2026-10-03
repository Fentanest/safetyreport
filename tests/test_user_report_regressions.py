"""User-reported v3 defects: deterministic, isolated and no remote writes."""
import datetime
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import pandas as pd
from sqlalchemy import create_engine, text
from services import report_cache, report_stats_service as stats


class CacheRegressions(unittest.TestCase):
    def test_simultaneous_fresh_checks_share_one_remote_verification(self):
        import threading
        from services.community_gate import _Gate
        gate=_Gate(clock=lambda:100.0)
        gate._verified_at=0.0; gate._invalidated=False
        barrier=threading.Barrier(2); calls=[]
        original=gate.refresh_now
        def verified():
            calls.append(1); gate._verified_at=100.0; gate._invalidated=False
        def synchronized(max_age=None):
            barrier.wait(timeout=3); return original(max_age=max_age)
        with mock.patch.object(gate,'evaluate',return_value={'state':'ok','can_enter':True}), mock.patch.object(gate,'_refresh_locked',side_effect=verified):
            with mock.patch.object(gate,'refresh_now',side_effect=synchronized):
                threads=[threading.Thread(target=gate.require_fresh) for _ in range(2)]
                for thread in threads: thread.start()
                for thread in threads: thread.join(5); self.assertFalse(thread.is_alive())
            self.assertEqual(len(calls),1)
            gate._invalidated=True; gate.require_fresh(); self.assertEqual(len(calls),2)

    def test_commit_calendar_and_dataset_invalidate_and_results_are_copies(self):
        with tempfile.TemporaryDirectory() as root:
            engine=create_engine('sqlite:///'+str(Path(root)/'personal.db'))
            with engine.begin() as c: c.execute(text('CREATE TABLE sample(n INTEGER)')); c.execute(text('INSERT INTO sample VALUES (1)'))
            calls=[]
            @report_cache.cached
            def read(engine):
                calls.append(1)
                with engine.connect() as c: return {'n':c.execute(text('SELECT n FROM sample')).scalar_one()}
            try:
                result=read(engine); result['n']=999
                self.assertEqual(read(engine),{'n':1}); self.assertEqual(len(calls),1)
                with engine.begin() as c: c.execute(text('UPDATE sample SET n=2'))
                self.assertEqual(read(engine),{'n':2}); self.assertEqual(len(calls),2)
                with mock.patch('services.report_cache.datetime.date') as date:
                    date.today.return_value=datetime.datetime(2027,1,1).date()
                    self.assertEqual(read(engine),{'n':2}); self.assertEqual(len(calls),3)
                sqlite3.connect(Path(root)/'community.db').close()
                read(engine); self.assertEqual(len(calls),4)
            finally: report_cache.clear(); engine.dispose()


class StatisticsRegressions(unittest.TestCase):
    def test_partial_fine_and_exact_law_combinations_are_independent(self):
        frame=pd.DataFrame([
            {'ID':'1','처리상태':'일부수용','범칙금_과태료':'과태료 40,000원','위반법규':'도로교통법 제5조','category':'traffic'},
            {'ID':'2','처리상태':'일부수용','범칙금_과태료':'미확인','위반법규':'도로교통법 제5조, 제6조','category':'traffic'},
            {'ID':'3','처리상태':'답변완료','범칙금_과태료':'','위반법규':'','category':'traffic'},
        ])
        result=stats._summarize_overview_frame(frame)
        self.assertEqual(result['result_distribution'],{'accept':0,'partial':2,'reject':0,'unknown':1})
        self.assertEqual(result['disposition']['fines'],1)
        self.assertEqual(sum(v['count'] for v in result['violation_laws']),3)
        for law in result['violation_laws']:
            self.assertEqual(len(stats._apply_stats_law_filter(frame,{'law':law['filter']})),law['count'])

    def test_spatial_budget_preserves_every_report_and_original_coordinates(self):
        frame=pd.DataFrame([{'ID':str(i),'위도':33+i/1000,'경도':126+i/1000,'주소키':str(i),
            '위반장소':str(i),'행정구역':'','처리상태':'일부수용','범칙금_과태료':'과태료','category':'traffic'} for i in range(3000)])
        original=frame.copy(deep=True)
        points=stats._aggregate_map_points(frame,max_points=1200,zoom=7)
        self.assertLessEqual(len(points),1200); self.assertEqual(sum(p['total'] for p in points),3000)
        pd.testing.assert_frame_equal(frame,original)


class RestoreImmutability(unittest.TestCase):
    def test_legacy_and_future_refusal_preserve_full_personal_and_rebuild_state(self):
        from test_community_rebuild import RebuildEnv
        from core.storage import exchange
        from services import community_rebuild as rebuild
        from services.community_store import CommunityStore
        env=RebuildEnv(self).install()
        rebuild.start('fixture')
        def dump(path):
            connection=sqlite3.connect(path)
            try: return tuple(connection.iterdump())
            finally: connection.close()
        personal=dump(env.personal_db); community=dump(str(Path(env.tmp)/'community.db'))
        for version,code in [(4,'DB_LEGACY_UNSUPPORTED'),(6,'DB_SCHEMA_UNSUPPORTED')]:
            upload=Path(env.tmp)/f'v{version}.db'
            connection=sqlite3.connect(upload)
            connection.execute('CREATE TABLE mysafety(ID TEXT)'); connection.execute(f'PRAGMA user_version={version}')
            connection.commit(); connection.close()
            source=upload.read_bytes()
            with self.assertRaises(exchange.RestoreRefused) as error: exchange.restore(str(upload),'server')
            self.assertEqual(error.exception.code,code)
            self.assertEqual(dump(env.personal_db),personal); self.assertEqual(dump(str(Path(env.tmp)/'community.db')),community)
            self.assertEqual(upload.read_bytes(),source)


class RebuildCommitRegressions(unittest.TestCase):
    def test_pending_items_cannot_commit_and_failure_does_not_claim_complete(self):
        from test_community_rebuild import RebuildEnv
        from services import community_rebuild as rebuild
        from services.community_store import CommunityStore
        env=RebuildEnv(self).install(); job=rebuild.start('fixture'); run_id=job['run_id']
        with CommunityStore.open().transaction() as tx:
            tx.execute("UPDATE rebuild_jobs SET state='validating',list_complete=1 WHERE run_id=?",(run_id,))
            tx.execute("INSERT INTO rebuild_items(run_id,source_report_id,state) VALUES(?,?,'pending')",(run_id,'p1'))
        with self.assertRaises(RuntimeError): rebuild._commit(run_id,with_gaps=False)
        self.assertEqual(rebuild._get_job(run_id)['state'],'failed'); self.assertTrue(rebuild.required())


class PagedRecords(unittest.TestCase):
    def test_pages_equal_full_raw_population(self):
        from test_community_rebuild import RebuildEnv
        from scripts.dev import fixture_server
        from services.report_query_service import get_report_page
        env=RebuildEnv(self,existing_reports=False).install(); fixture_server.seed_engine(env.engine)
        for mode in ('raw','canonical'):
            full=__import__('services.data_service',fromlist=['get_traffic_records']).get_traffic_records(env.engine,mode=mode,exact_values=True)
            self.assertGreater(len(full),0)
            rows=[]; offset=0
            while True:
                page=get_report_page(env.engine,'traffic',offset=offset,limit=3,mode=mode)
                self.assertEqual(page['total'],len(full)); self.assertLessEqual(page['count'],3)
                rows.extend(page['data'])
                if page['next_offset'] is None: break
                offset=page['next_offset']
            self.assertEqual({r['ID'] for r in rows},{r['ID'] for r in full})
            self.assertEqual({r['ID']:r for r in rows},{r['ID']:r for r in full})

    def test_paging_uses_one_projection_snapshot_without_post_page_requery(self):
        from test_community_rebuild import RebuildEnv
        from scripts.dev import fixture_server
        from services.report_query_service import get_report_page
        from services import duplicate_group_service as duplicates
        env=RebuildEnv(self,existing_reports=False).install(); fixture_server.seed_engine(env.engine)
        with mock.patch.object(duplicates,'build_projection_map',wraps=duplicates.build_projection_map) as projection:
            page=get_report_page(env.engine,'traffic',offset=0,limit=1,mode='canonical')
            self.assertEqual(page['count'],1); self.assertEqual(page['next_offset'],1)
            self.assertEqual(projection.call_count,1)


class PublicBuildDefaults(unittest.TestCase):
    def test_real_dev_build_uses_verified_public_defaults(self):
        import json
        from scripts.build.build_exe import write_community_public
        from services.community_auth_service import validate_publishable_key, normalize_supabase_url
        with tempfile.TemporaryDirectory() as root, mock.patch.dict('os.environ',{},clear=True):
            target=write_community_public(out=str(Path(root)/'community_public.json'))
            cfg=json.loads(Path(target).read_text())
            self.assertTrue(normalize_supabase_url(cfg['supabase_url']))
            self.assertTrue(validate_publishable_key(cfg['publishable_key']))
            self.assertNotIn('sb_secret_',cfg['publishable_key'])


if __name__=='__main__': unittest.main()
