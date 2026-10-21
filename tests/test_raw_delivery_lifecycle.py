import gzip
import json
import os
import tempfile
import unittest
import uuid
from types import SimpleNamespace
from unittest import mock
from services import community_capture as cap,community_ingest_client as client,community_uploader as up,community_notice as notices
from services.community_store import CommunityStore
from services.community_raw_ingest import snapshot

class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=CommunityStore.open(self.tmp.name);self.addCleanup(self.store.close)
        self.ctx=dict(contributor_fingerprint='f'*32,connection_id=str(uuid.uuid4()),writer_epoch=1,dataset_key='d'*64,consent_grant_id=str(uuid.uuid4()),policy_version='draft',consent_text_sha256='h'*64,source_app='safetyreport',source_mode='server')
        self.store.set_context(**self.ctx)
        self.store.set_meta('ingest_status',json.dumps(dict(consent=dict(state='active',grant_id=self.ctx['consent_grant_id'],policy_version='draft'),ingest=dict(contracts=[1,2],raw_policy_version='draft'))))
        self.input=dict(processing_status='수용',report_date='2026-10-20',geocode={})
        self.packet=snapshot('{"result":{"C_NO":"SYNTHETIC01"}}','2026-10-21T00:00:00Z','crawl',{})
        self.patches=[mock.patch.object(cap,'read_account_context',return_value=(dict(self.ctx,state='active'),'active')),mock.patch.object(cap,'_project_namespace',return_value='fixture'),mock.patch.object(up,'_project_ns',return_value='fixture'),mock.patch.object(up,'_gate_check',return_value={'can_enter':True}),mock.patch.object(up,'_sleep'),mock.patch('services.community_auth_service.get_access_token',return_value='synthetic-token'),mock.patch.object(client,'_config',return_value=SimpleNamespace(supabase_url='https://fixture.invalid',publishable_key='synthetic')),mock.patch('services.community_cloud.run',side_effect=lambda url,fn:fn())]
        for p in self.patches:p.start();self.addCleanup(p.stop)
    def capture(self,**kw):
        event=cap.capture(self.input,source_report_id='SYNTHETIC01',trigger=kw.pop('trigger','raw_backfill'),data_dir=self.tmp.name,raw_snapshot=self.packet,**kw)
        cap.mark_personal_save(event.event_id,True,self.tmp.name);return event
    def ack(self,body,status='stored',**extra):
        env=json.loads(gzip.decompress(body))
        self.assertEqual(env['protocol'],2)
        return 200,json.dumps(dict(protocol=2,request_id='synthetic-request',client_notice=extra.pop('notice',None),results=[dict(event_id=e['event_id'],status=status,durable=True,receipt_id=str(uuid.uuid4()),projection_status='parse_pending',**extra) for e in env['events']])).encode(),{}
    def test_v2_transport_is_revalidated_as_v2_and_receipt_is_durable(self):
        event=self.capture()
        with mock.patch.object(client,'_http_post',side_effect=lambda u,h,b,t:self.ack(b)):
            result=up.request_upload('raw_backfill',self.tmp.name)
        self.assertEqual(result['counts']['acked'],1)
        self.assertEqual(self.store.meta('raw_receipt:'+event.event_id),'stored')
        self.assertEqual(self.store.connect().execute('SELECT COUNT(*) FROM outbox').fetchone()[0],0)
    def test_duplicate_stored_receipt_and_notice_from_ack(self):
        event=self.capture();notice=dict(level='warning',message='합성 서버 안내',min_version=dict(pc='99.0.0',mobile=None),url=dict(pc=None,mobile=None),deadline=None)
        with mock.patch.object(client,'_http_post',side_effect=lambda u,h,b,t:self.ack(b,'duplicate',original_status='stored',notice=notice)):
            up.request_upload('raw_backfill',self.tmp.name)
        self.assertEqual(self.store.meta('raw_receipt:'+event.event_id),'stored')
        self.assertEqual(notices.read(self.store)['message'],'합성 서버 안내')
    def test_late_ack_after_reset_cannot_mark_receipt_or_reactivate_context(self):
        event=self.capture()
        def post(u,h,b,t):
            response=self.ack(b)
            with self.store.transaction() as tx:
                tx.execute("UPDATE outbox SET state='blocked',lease_owner=NULL,lease_until=NULL")
                tx.execute('DELETE FROM context')
            return response
        with mock.patch.object(client,'_http_post',side_effect=post):up.request_upload('raw_backfill',self.tmp.name)
        self.assertIsNone(self.store.active_context());self.assertIsNone(self.store.meta('raw_receipt:'+event.event_id))
        self.assertIsNone(self.store.connect().execute('SELECT ack_status FROM source_journal WHERE event_id=?',(event.event_id,)).fetchone()[0])
    def test_426_is_persisted_shown_as_generic_required_and_rows_stay_retryable(self):
        self.capture()
        with mock.patch.object(client,'_http_post',return_value=(426,b'<html>upgrade</html>',{})):
            up.request_upload('raw_backfill',self.tmp.name)
        shown=notices.read(self.store)
        self.assertEqual(shown['level'],'required');self.assertEqual(shown['message'],notices.GENERIC_MESSAGE)
        self.assertNotRegex(shown['message'],r'\d+\.\d+')  # 하드코딩 버전 문구 없음
        box=self.store.connect().execute('SELECT state,last_error_code FROM outbox').fetchone()
        self.assertEqual((box['state'],box['last_error_code']),('retry_wait','client_upgrade_required'))  # 영구 blocked 아님
        # status 가 정본: client_notice 가 null 이면 표시와 426 표시를 풀고 행을 다시 보낼 수 있게 한다
        notices.apply_status(self.store,{'client_notice':None})
        self.assertIsNone(notices.read(self.store))
        box=self.store.connect().execute('SELECT state,last_error_code FROM outbox').fetchone()
        self.assertEqual((box['state'],box['last_error_code']),('pending',None))
    def test_426_flag_expires_when_app_version_changes(self):
        self.capture()
        with mock.patch.object(notices,'app_version',return_value='3.5.1'):
            notices.mark_upgrade_required(self.store);self.assertIsNotNone(notices.read(self.store))
        with mock.patch.object(notices,'app_version',return_value='3.6.0'):
            self.assertIsNone(notices.read(self.store))
            self.assertIsNone(self.store.meta('client_upgrade_required'))
    def test_successful_ack_clears_upgrade_flag_and_status_without_key_clears_notice(self):
        event=self.capture();notices.mark_upgrade_required(self.store)
        with mock.patch.object(client,'_http_post',side_effect=lambda u,h,b,t:self.ack(b)):up.request_upload('raw_backfill',self.tmp.name)
        self.assertIsNone(self.store.meta('client_upgrade_required'))
        notice=dict(level='info',message='안내',min_version=dict(pc='99.0.0',mobile=None),url=dict(pc=None,mobile=None),deadline=None)
        notices.apply_status(self.store,{'client_notice':notice});self.assertEqual(notices.read(self.store)['level'],'info')
        notices.apply_status(self.store,{'consent':{}});self.assertIsNone(notices.read(self.store))  # 키가 없어도 null 과 같다
        notices.apply_status(self.store,{'client_notice':notice});notices.apply_status(self.store,{'client_notice':{'level':'bogus'}})
        self.assertEqual(notices.read(self.store)['level'],'info')  # 형식이 틀린 안내는 기존 상태를 지우지 않는다
    def test_notice_is_shown_only_when_app_is_older_than_min_version(self):
        notice=dict(level='warning',message='업데이트 안내',min_version=dict(pc='3.6.0',mobile='2.5.0'),url=dict(pc=None,mobile=None),deadline=None)
        notices.save(self.store,notice)
        for version,shown in [('3.5.1',True),('3.5.99',True),('3.6.0',False),('3.6.1',False),('3.10.0',False),('4.0',False),('3.6',False),('3.5',True)]:
            with self.subTest(version=version),mock.patch.object(notices,'app_version',return_value=version):
                self.assertEqual(notices.read(self.store) is not None,shown)
        with mock.patch.object(notices,'app_version',return_value='unknown'):self.assertIsNone(notices.read(self.store))
        notices.save(self.store,dict(notice,min_version=dict(pc=None,mobile='2.5.0')))
        with mock.patch.object(notices,'app_version',return_value='3.5.1'):self.assertIsNone(notices.read(self.store))  # PC 최소 버전이 없으면 보이지 않는다
        for level in ('info','warning','required'):
            notices.save(self.store,dict(notice,level=level))
            with mock.patch.object(notices,'app_version',return_value='3.5.1'):self.assertEqual(notices.read(self.store)['level'],level)
    def test_v2_off_falls_back_to_v1_events_and_pending_rows_are_sent_as_v1(self):
        pending=self.capture(trigger='realtime')
        self.assertIsNotNone(self.store.connect().execute('SELECT raw_event_json FROM source_journal WHERE event_id=?',(pending.event_id,)).fetchone()[0])
        status=dict(consent=dict(state='active',grant_id=self.ctx['consent_grant_id'],policy_version='draft'),ingest=dict(contracts=[1],raw_policy_version='draft'))
        self.store.set_meta('ingest_status',json.dumps(status))  # 다음 status 가 2 를 빼고 왔다
        seen=[]
        def v1(u,h,b,t):
            env=json.loads(b);seen.append(env);return 200,json.dumps(dict(protocol=1,request_id='synthetic-v1',results=[dict(event_id=e['event_id'],status='accepted',durable=True,receipt_id=str(uuid.uuid4()),projection_status='published') for e in env['events']])).encode(),{}
        with mock.patch.object(client,'_http_post',side_effect=v1):up.request_upload('recovery',self.tmp.name)
        self.assertEqual([e['protocol'] for e in seen],[1]);event=seen[0]['events'][0]
        self.assertIn('payload',event);self.assertNotIn('raw',event);self.assertEqual(event['event_id'],pending.event_id)
        self.assertEqual(self.store.connect().execute('SELECT COUNT(*) FROM outbox').fetchone()[0],0)
        # 새 수집도 v1 이벤트(원본 사본 없음)다
        again=self.capture(force=True,trigger='realtime')
        self.assertIsNone(self.store.connect().execute('SELECT raw_event_json FROM source_journal WHERE event_id=?',(again.event_id,)).fetchone()[0])
    def test_backfill_rows_wait_while_v2_is_off_and_resume_when_it_returns(self):
        event=self.capture(trigger='raw_backfill')
        status=dict(consent=dict(state='active',grant_id=self.ctx['consent_grant_id'],policy_version='draft'),ingest=dict(contracts=[1],raw_policy_version='draft'))
        self.store.set_meta('ingest_status',json.dumps(status))
        with mock.patch.object(client,'_http_post',side_effect=AssertionError('backfill must not be sent as v1')):up.request_upload('recovery',self.tmp.name)
        box=self.store.connect().execute('SELECT state,last_error_code FROM outbox WHERE event_id=?',(event.event_id,)).fetchone()
        self.assertEqual((box['state'],box['last_error_code']),('retry_wait','v2_unavailable'))
        status['ingest']['contracts']=[1,2];self.store.set_meta('ingest_status',json.dumps(status))
        with self.store.transaction() as tx:tx.execute('UPDATE outbox SET next_retry_at=NULL')
        with mock.patch.object(client,'_http_post',side_effect=lambda u,h,b,t:self.ack(b)):up.request_upload('recovery',self.tmp.name)
        self.assertEqual(self.store.meta('raw_receipt:'+event.event_id),'stored')
    def test_gzip_503_is_retryable_and_never_blocks_or_drops_rows(self):
        event=self.capture()
        body=json.dumps(dict(error=dict(code='service_unavailable',retryable=True))).encode()
        with mock.patch.object(client,'_http_post',return_value=(503,body,{'Retry-After':'1'})):result=up.request_upload('raw_backfill',self.tmp.name)
        box=self.store.connect().execute('SELECT state FROM outbox WHERE event_id=?',(event.event_id,)).fetchone()
        self.assertEqual(box['state'],'retry_wait');self.assertIsNone(self.store.meta('client_upgrade_required'))
    def test_acked_raw_copy_is_compacted_but_keeps_change_detection_hashes(self):
        event=self.capture()
        with mock.patch.object(client,'_http_post',side_effect=lambda u,h,b,t:self.ack(b)):up.request_upload('raw_backfill',self.tmp.name)
        stored=self.store.connect().execute('SELECT raw_event_json FROM source_journal WHERE event_id=?',(event.event_id,)).fetchone()[0]
        compact=json.loads(stored);self.assertTrue(compact['compact']);self.assertNotIn('body',compact['raw']);self.assertLess(len(stored),600)
        # 같은 내용을 다시 수집해도 새 이벤트를 만들지 않는다(비교 해시가 남아 있다)
        self.assertIsNone(cap.capture(self.input,source_report_id='SYNTHETIC01',trigger='realtime',data_dir=self.tmp.name,raw_snapshot=self.packet).event_id)
    def test_raw_required_rows_are_released_when_v2_turns_off(self):
        with mock.patch('services.community_raw_ingest.snapshot_for_adapter',return_value=None):
            event=cap.capture(self.input,source_report_id='MISSING',trigger='realtime',data_dir=self.tmp.name)
        cap.mark_personal_save(event.event_id,True,self.tmp.name)
        status=dict(consent=dict(state='active',grant_id=self.ctx['consent_grant_id'],policy_version='draft'),ingest=dict(contracts=[1],raw_policy_version='draft'))
        self.store.set_meta('ingest_status',json.dumps(status))
        sent=[]
        def v1(u,h,b,t):
            env=json.loads(b);sent.append(env);return 200,json.dumps(dict(protocol=1,request_id='synthetic-v1',results=[dict(event_id=e['event_id'],status='accepted',durable=True,receipt_id=str(uuid.uuid4()),projection_status='published') for e in env['events']])).encode(),{}
        with mock.patch.object(client,'_http_post',side_effect=v1):up.request_upload('recovery',self.tmp.name)
        self.assertEqual([e['event_id'] for env in sent for e in env['events']],[event.event_id])
    def test_raw_missing_is_blocked_and_never_becomes_a_v1_upload(self):
        with mock.patch('services.community_raw_ingest.snapshot_for_adapter',return_value=None):
            event=cap.capture(self.input,source_report_id='MISSING',trigger='realtime',data_dir=self.tmp.name)
        row=self.store.connect().execute('SELECT blocked_reason FROM source_journal WHERE event_id=?',(event.event_id,)).fetchone()
        self.assertEqual(row[0],'raw_required')
        with mock.patch.object(client,'_http_post',side_effect=AssertionError('v1 transmission')):up.request_upload('recovery',self.tmp.name)
