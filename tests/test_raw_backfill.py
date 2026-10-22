"""Offline backfill state/storage and real save-path tests; no loopback or remote I/O."""
import copy
import json
import os
import tempfile
import unittest
from unittest import mock
from datetime import datetime
from sqlalchemy import create_engine,select
from services.community_raw_backfill import RawBackfill,BackfillPaused,BackfillCancelled
from services.community_store import CommunityStore
from services import community_capture as cap,community_raw_ingest as raw

class Backend:
    def __init__(self):
        self.identity=dict(local_dataset_id='local',contributor_fingerprint='f'*32,dataset_key='d'*64,consent_grant_id='grant',parser_target='pc-parser-13',connection_id='connection',writer_epoch=1)
        self.ready=True;self.list_ok=True;self.prepared={};self.receipts={};self.enqueued=[];self.calls=[];self.alive=True
    def scope(self):return dict(self.identity)
    def current(self,scope):return self.alive and scope==self.identity
    def optimization_ready(self):return self.ready
    def inventory(self):self.calls.append('list');return dict(list_ok=self.list_ok,ids=['A','B','C'],eligible_ids=['A','B','C'])
    def local_ids(self):return ['A','B']
    def eligible_ids(self):return ['A','B']
    def prepare(self,rid):
        self.calls.append(rid);out=self.prepared.get(rid,dict(eligible=True,filled=1,unfilled=0))
        if callable(out):return out()
        if isinstance(out,Exception):raise out
        return copy.deepcopy(out)
    def enqueue(self,rid,run):self.enqueued.append(rid);return 'event-'+rid
    def receipt(self,rid,event):return self.receipts.get(rid,'pending')
    def wake_upload(self):self.calls.append('upload')

class BackfillStateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=CommunityStore.open(self.temp.name);self.addCleanup(self.store.close)
        self.backend=Backend();self.clock=1000
    def load(self):return json.loads(self.store.meta('raw_backfill') or 'null')
    def save(self,value,scope):
        if not self.backend.current(scope):raise BackfillCancelled()
        self.store.set_meta('raw_backfill',json.dumps(value))
    def run_job(self,**kwargs):return RawBackfill(self.load,self.save,self.backend,clock=lambda:self.clock).run(**kwargs)
    def test_restart_cursor_unavailable_denominator_and_only_stored_receipts_complete(self):
        self.backend.prepared['B']=dict(eligible=True,raw_unavailable=True,unfilled=1)
        first=self.run_job(budget=1);self.assertEqual(first['cursor'],1);self.assertEqual(self.backend.enqueued,['A'])
        second=self.run_job();self.assertEqual(second['state'],'uploading');self.assertEqual(second['stats']['total'],3)
        self.assertEqual(second['stats']['available'],2);self.assertEqual(second['stats']['raw_unavailable'],1)
        self.assertEqual(self.backend.enqueued,['A','C'])
        self.run_job();self.assertEqual(self.backend.enqueued,['A','C']);self.assertEqual(self.load()['stats']['sent'],0)
        self.backend.receipts={'A':'stored','C':'stored'}
        done=self.run_job();self.assertEqual(done['state'],'done');self.assertEqual(done['stats']['sent'],2)
    def test_optimizer_and_partial_list_never_complete_or_advance(self):
        self.backend.ready=False
        self.assertEqual(self.run_job()['state'],'waiting_optimize');self.assertEqual(self.backend.calls,[])
        self.backend.ready=True;self.backend.list_ok=False
        paused=self.run_job();self.assertEqual(paused['state'],'paused');self.assertEqual(paused['reason'],'list_incomplete')
        self.assertEqual(paused['items'],{});self.assertEqual(self.backend.enqueued,[])
        self.backend.list_ok=True;self.run_job();self.assertEqual(self.backend.calls,['list'])
        self.clock+=301;self.run_job();self.assertEqual(self.backend.enqueued,['A','B','C'])
    def test_three_failures_hold_and_explicit_retry_resumes(self):
        self.backend.prepared['A']=ValueError('synthetic')
        for _ in range(3):self.run_job()
        held=self.load();self.assertEqual(held['holds']['A']['attempts'],3);self.assertNotEqual(held['state'],'done')
        self.backend.prepared['A']=dict(eligible=True,filled=0,unfilled=1)
        self.run_job(retry_holds=True);self.backend.receipts={x:'stored' for x in 'ABC'}
        self.assertEqual(self.run_job()['state'],'done')
    def test_auth_pause_does_not_consume_attempts(self):
        self.backend.prepared['A']=BackfillPaused('official_login_required')
        state=self.run_job();self.assertEqual(state['state'],'paused');self.assertEqual(state['items']['A']['attempts'],0)
        self.assertFalse(state['holds']);self.assertEqual(state['cursor'],0)
    def test_consecutive_failure_pause_and_cancelled_scope_cannot_publish(self):
        self.backend.prepared={x:ValueError('synthetic') for x in 'ABC'}
        for _ in range(4):self.run_job()
        self.assertEqual(self.load()['reason'],'consecutive_failures')
        self.clock+=301
        def cancelled():self.backend.alive=False;return dict(eligible=True)
        self.backend.prepared['B']=cancelled
        before=self.store.meta('raw_backfill');self.assertIsNone(self.run_job());self.assertEqual(self.store.meta('raw_backfill'),before)
    def test_shared_mobile_trace_fixture(self):
        from pathlib import Path
        fixture=json.loads((Path(__file__).parent/'fixtures/raw_backfill_cases.json').read_text())
        for case in fixture['cases']:
            with self.subTest(case=case['name']):
                self.backend=Backend();self.clock=1000
                with self.store.transaction() as tx:tx.execute("DELETE FROM meta WHERE key='raw_backfill'")
                options=dict(case['initial'])
                for action in case['actions']:
                    options.update({k:v for k,v in action.items() if k in ('optimized','list_ok','fail','auth','unavailable','receipts','dataset')})
                    self.backend.ready=options.get('optimized',True);self.backend.list_ok=options.get('list_ok',True)
                    self.backend.prepared={}
                    for rid in options.get('fail',[]):self.backend.prepared[rid]=RuntimeError('synthetic_retry')
                    for rid in options.get('auth',[]):self.backend.prepared[rid]=BackfillPaused('official_login_required')
                    for rid in options.get('unavailable',[]):self.backend.prepared[rid]=dict(eligible=True,raw_unavailable=True,filled=0,unfilled=1)
                    self.backend.receipts=options.get('receipts',{})
                    if 'dataset' in options:self.backend.identity['local_dataset_id']=options['dataset']
                    self.clock+=action.get('advance',0)
                    result=self.run_job(budget=action.get('budget',20),retry_holds=action.get('retry_holds',False))
                    for key,value in action['expect'].items():
                        actual=result['stats'][key] if key in ('total','available','raw_unavailable','sent','filled','unfilled','holds') else result[key]
                        self.assertEqual(actual,value,(case['name'],key))

    def test_only_local_dataset_change_restarts_the_run(self):
        # 의도한 정책 변경(검수 결정): run identity 에서 parser_target·writer_epoch·connection·grant 를 뺐다.
        first=self.run_job(budget=1)
        for key,value in [('consent_grant_id','newgrant'),('writer_epoch',2),('parser_target','pc-parser-14'),('connection_id','connection-2')]:
            self.backend.identity[key]=value
            same=self.run_job(budget=1);self.assertEqual(same['run_id'],first['run_id'],key);self.assertEqual(same[key],value)
        self.backend.identity['local_dataset_id']='restored'
        other=self.run_job(budget=1);self.assertNotEqual(other['run_id'],first['run_id'])

class BackfillRuntimeTests(unittest.TestCase):
    def setUp(self):
        from core.database import database
        from core.utils import logger
        log=mock.patch.object(logger.LoggerFactory,"logbot",mock.Mock());log.start();self.addCleanup(log.stop)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.engine=create_engine('sqlite:///'+os.path.join(self.temp.name,'personal.db'))
        self.addCleanup(self.engine.dispose);database.upgrade_schema(self.engine,maintenance=False)
        self.store=CommunityStore.open(self.temp.name);self.addCleanup(self.store.close)
        self.ctx=dict(contributor_fingerprint='f'*32,connection_id='22222222-2222-4222-8222-222222222222',writer_epoch=1,dataset_key='d'*64,
            consent_grant_id='33333333-3333-4333-8333-333333333333',policy_version='draft',consent_text_sha256='h'*64,source_app='safetyreport',source_mode='server')
        self.store.set_context(**self.ctx);self.store.set_meta('ingest_status',json.dumps(dict(consent=dict(state='active',grant_id=self.ctx['consent_grant_id'],policy_version='draft'),ingest=dict(contracts=[1,2],raw_policy_version='draft'))))
        from services.community_raw_backfill_runtime import RuntimeBackend
        import threading
        with mock.patch('core.database.engine.get_engine',return_value=self.engine):self.backend=RuntimeBackend(self.store,threading.Event())
        self.backend.scope();self.backend.pause=lambda:None;self.backend.connect=lambda:object()
    def insert(self,**kw):
        from core.database import models
        row=dict(ID='SYNTHETIC01',category='traffic',처리상태='수용',신고명='합성',신고일=datetime.now().strftime('%Y-%m-%d'),신고번호='SPP-2610-8000001')
        row.update(kw)
        with self.engine.begin() as c:c.execute(models.report_table.insert().values(**row))
    def raw(self,corrupt=False):
        from core.database import models
        from core.utils import raw_detail
        body=raw_detail.canonical_bytes(dict(result=dict(C_NO='SYNTHETIC01',C_NOW=10,C_A_TITLE='합성',C_A_CONTENTS='비공개 합성',answers=[dict(C_MANAGER_TYPE_NM='수용')])))
        with self.engine.begin() as c:c.execute(models.report_detail_raw_table.insert().values(ID='SYNTHETIC01',raw_zlib=b'broken' if corrupt else raw_detail.compress(body),raw_sha256=raw_detail.sha256_hex(body),raw_size=len(body),fetched_at='2026-10-21T00:00:00Z',source='crawl'))
        return json.loads(body)
    def test_corrupt_raw_uses_existing_detail_save_path_and_v2_journal(self):
        self.insert();envelope=self.raw(corrupt=True)
        with mock.patch('services.db_optimize._fetch_envelope',return_value=(envelope,'ok','')),mock.patch('core.storage.reports_repo._prefetch_derived',return_value={}),mock.patch.object(cap,'_store',return_value=self.store),mock.patch.object(cap,'read_account_context',return_value=(dict(self.ctx,state='active'),'active')):
            outcome=self.backend.prepare('SYNTHETIC01')
            self.assertEqual(self.store.connect().execute('SELECT COUNT(*) FROM source_journal').fetchone()[0],0)  # 저장 경로는 일반 캡처를 만들지 않는다
            self.backend.enqueue('SYNTHETIC01','run-1')  # 이벤트는 enqueue 한 번만 만든다
        self.assertTrue(outcome['eligible']);self.assertIsNotNone(raw.load_current_snapshot('SYNTHETIC01',engine=self.engine))
        journal=self.store.connect().execute('SELECT raw_event_json FROM source_journal ORDER BY source_revision DESC LIMIT 1').fetchone()
        self.assertIsNotNone(journal['raw_event_json']);self.assertEqual(json.loads(journal['raw_event_json'])['raw']['source'],'raw_backfill')
    def test_404_is_unavailable_and_403_pauses_without_saving(self):
        self.insert()
        with mock.patch('services.db_optimize._fetch_envelope',return_value=(None,'permanent','HTTP 404')):
            self.assertTrue(self.backend.prepare('SYNTHETIC01')['raw_unavailable'])
        with mock.patch('services.db_optimize._fetch_envelope',return_value=(None,'auth','HTTP 403')):
            with self.assertRaises(BackfillPaused):self.backend.prepare('SYNTHETIC01')
        self.assertEqual(self.store.connect().execute('SELECT COUNT(*) FROM source_journal').fetchone()[0],0)
    def test_rating_and_photo_supplement_fill_and_expiry_are_recorded(self):
        from services.satisfaction_fetcher import SatisfactionLookupResult
        from core.database import models
        self.insert(만족도조사여부='참여 완료',별점=4,별점사유='',첨부사진='https://fixture.invalid/photo.jpg');self.raw()
        with mock.patch('services.satisfaction_fetcher.fetch_score_via_api',return_value=SatisfactionLookupResult(4,'합성 사유',True)),mock.patch('services.photo_capture_time.collect',return_value={'사진_첫촬영':'2026-10-20 12:00:00','사진_끝촬영':'2026-10-20 12:00:00','사진_촬영수':1}):
            result=self.backend.prepare('SYNTHETIC01')
        self.assertEqual(result['filled'],2);snapshot=raw.load_current_snapshot('SYNTHETIC01',engine=self.engine)
        self.assertEqual(snapshot['supplement']['rating_cause'],'합성 사유');self.assertEqual(snapshot['supplement']['photo_count'],1)
        with self.engine.begin() as c:c.execute(models.report_table.update().values(신고일='2000-01-01',사진_촬영수=None,사진_첫촬영=None,사진_끝촬영=None))
        with self.store.transaction() as tx:tx.execute('DELETE FROM raw_backfill_supplements')  # 조회한 신고는 다시 조회하지 않는다 — 만료 판정을 보려고 표시를 지운다
        with mock.patch('services.photo_capture_time.collect',side_effect=AssertionError('expired fetch')):
            self.assertEqual(self.backend.prepare('SYNTHETIC01')['photo_unavailable'],'expired')
        # 2-6: 보관 기간이 지나 시도하지 않은 사진은 success 가 아니라 expired 로 남긴다
        row=self.store.connect().execute("SELECT status FROM raw_backfill_supplements WHERE report_id='SYNTHETIC01' AND kind='photo'").fetchone()
        self.assertEqual(row['status'],'expired')
