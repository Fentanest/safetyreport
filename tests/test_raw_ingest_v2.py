import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timezone
from types import SimpleNamespace
import gzip
from services import community_raw_ingest as raw
from services.community_gate_policy import gate_policy
from services import community_capture as cap, community_ingest_client as client, community_uploader as uploader, community_upload_policy as policy
from services.community_store import CommunityStore
ROOT=Path(__file__).resolve().parents[1]
class RawIngestTests(unittest.TestCase):
    def test_actual_offline_authorization_expires_and_respects_authoritative_consent(self):
        from services import community_cloud as cloud
        service=SimpleNamespace(config=lambda:SimpleNamespace(supabase_url='https://fixture.invalid'))
        current=dict(user_id='synthetic',kakao_id='910001')
        row=dict(consent=dict(state='active'),last_g1=dict(at=1000000,kakao_id='910001',dataset_key='dataset'))
        state={'accounts':{cloud._account_key(service.config().supabase_url,'synthetic'):row}}
        with mock.patch.object(cloud,'_read_state',return_value=state):
            for now,allowed in [(1000000,True),(1259200,True),(1259201,False),(999999,False)]:
                with self.subTest(now=now),mock.patch.object(cloud,'_now',return_value=now):
                    self.assertEqual(cloud.local_allowed(service,current,'910001','dataset'),allowed)
            for denied in ('none','outdated','revoked'):
                row['consent']['state']=denied
                with mock.patch.object(cloud,'_now',return_value=1000001):
                    self.assertFalse(cloud.local_allowed(service,current,'910001','dataset'))

    def test_soft_reset_cache_clear_keeps_change_listeners(self):
        from services.community_gate import _Gate
        gate=_Gate(); listener=lambda result:None
        gate.on_change(listener);gate._status={'consent':{'state':'active'}};gate._status_user='old';gate._verified_at=1;gate._owner='ok'
        gate.clear_after_soft_reset()
        self.assertIsNone(gate._status);self.assertIsNone(gate._status_user);self.assertIsNone(gate._verified_at)
        self.assertIsNone(gate._owner);self.assertIn(listener,gate._listeners);self.assertTrue(gate._invalidated)
    def test_transport_gzip_preserves_wire_and_checks_protocol(self):
        env=json.loads((ROOT/'contracts/community-ingest-v2/vectors/wire.json').read_text())['envelope']
        sent=[]
        def post(url,headers,body,timeout):
            sent.append((headers,body))
            eid=env['events'][0]['event_id']
            ack=dict(protocol=2,request_id='synthetic',results=[dict(event_id=eid,status='stored',durable=True,receipt_id=eid,projection_status='parse_pending')])
            return 200,json.dumps(ack).encode(),{}
        with (mock.patch.object(client,'_config',return_value=SimpleNamespace(supabase_url='https://fixture.invalid',publishable_key='synthetic')),
              mock.patch('services.community_cloud.run',side_effect=lambda base,fn:fn()), mock.patch.object(client,'_http_post',side_effect=post)):
            response=client.post_envelope(env,token='synthetic')
        self.assertTrue(response.ok)
        self.assertEqual(sent[0][0]['Content-Encoding'],'gzip')
        self.assertEqual(gzip.decompress(sent[0][1]),client.envelope_bytes(env))
    def test_shared_contract_bytes_and_hashes(self):
        v=json.loads((ROOT/'contracts/community-ingest-v2/vectors/wire.json').read_text())
        e=v['envelope']['events'][0]
        self.assertEqual(hashlib.sha256(e['raw']['body'].encode()).hexdigest(),e['raw']['sha256'])
        self.assertEqual(raw.canonical(e['supplement']),v['supplement_canonical'])
        self.assertEqual(raw.sha(e['supplement']),e['supplement_sha256'])
        self.assertEqual(raw.sha(e['client_observation']),e['client_observation_sha256'])
        self.assertEqual(hashlib.sha256(client.envelope_bytes(v['envelope'])).hexdigest(),v['request_utf8_sha256'])
        for line in (ROOT/'contracts/community-ingest-v2/MANIFEST.sha256').read_text().splitlines():
            digest,name=line.split('  ',1);self.assertEqual(hashlib.sha256((ROOT/'contracts/community-ingest-v2'/name).read_bytes()).hexdigest(),digest)
    def test_gate_contract_vectors(self):
        for c in json.loads((ROOT/'tests/fixtures/review_gate_cases.json').read_text())['cases']:
            with self.subTest(case=c['name']):self.assertEqual(gate_policy(c['input']),c['expected'])
    def test_v2_switch_requires_both_new_active_policy_and_capability(self):
        good=dict(consent=dict(state='active',policy_version='draft'),ingest=dict(raw_policy_version='draft',contracts=[1,2]))
        self.assertTrue(raw.v2_ready(good))
        for s in [dict(good,ingest={}),dict(good,consent=dict(state='outdated',policy_version='draft')),dict(good,consent=dict(state='active',policy_version='old'))]:self.assertFalse(raw.v2_ready(s))
    def test_capture_supplement_changes_and_immutable_upload_snapshot(self):
        with tempfile.TemporaryDirectory() as data:
            store=CommunityStore.open(data)
            ctx=dict(contributor_fingerprint='a'*32,connection_id='22222222-2222-4222-8222-222222222222',writer_epoch=1,dataset_key='b'*64,
                consent_grant_id='33333333-3333-4333-8333-333333333333',policy_version='draft',consent_text_sha256='c'*64,source_app='safetyreport',source_mode='server')
            store.set_context(**ctx);store.set_meta('ingest_status',json.dumps(dict(consent=dict(state='active',grant_id=ctx['consent_grant_id'],policy_version='draft'),ingest=dict(raw_policy_version='draft',contracts=[1,2]))))
            inp=dict(processing_status='수용',report_date='2026-10-20',geocode={})
            snapshot=raw.snapshot('{"result":{"C_NO":"SYNTHETIC01"}}','2026-10-21T00:00:00Z','crawl',{})
            with mock.patch('services.community_capture.read_account_context',return_value=(dict(ctx,state='active'),'active')):
                first=cap.capture(inp,source_report_id='SYNTHETIC01',trigger='raw_backfill',data_dir=data,raw_snapshot=snapshot)
                self.assertIsNone(cap.capture(inp,source_report_id='SYNTHETIC01',trigger='realtime',data_dir=data,raw_snapshot=snapshot).event_id)
                changed=raw.snapshot(snapshot['raw']['body'],snapshot['raw']['fetched_at'],'crawl',{'별점사유':'합성 비공개 사유'})
                second=cap.capture(inp,source_report_id='SYNTHETIC01',trigger='realtime',data_dir=data,raw_snapshot=changed)
                self.assertIsNotNone(second.event_id)
            row=dict(store.connect().execute('SELECT * FROM source_journal WHERE event_id=?',(first.event_id,)).fetchone())
            row['_raw_event']=row['raw_event_json'];event=uploader._event(row,row['payload_json'],ctx)
            self.assertIn('raw',event);self.assertNotIn('payload',event)
            self.assertEqual(event['supplement']['rating_cause'],None)
            self.assertEqual(uploader._envelope(ctx,'raw_backfill',[event])['protocol'],2)
            store.close();CommunityStore._forget(str(Path(data)/'community.db'))
    def test_stored_ack_and_upgrade_error_are_not_retry_loops(self):
        eid='11111111-1111-4111-8111-111111111111'
        ack=dict(protocol=2,request_id='synthetic',results=[dict(event_id=eid,status='stored',durable=True,receipt_id=eid,projection_status='parse_pending')])
        result=policy.interpret_ack([eid],ack,protocol=2)
        self.assertEqual(result.events[eid].outcome,'done')
        self.assertEqual(policy.interpret_ack([eid],ack).error_class,'invalid_ack')
        result=policy.interpret_response([eid],426,{},json.dumps(dict(error=dict(code='client_upgrade_required',retryable=False))),datetime.now(timezone.utc))
        self.assertEqual(result.error_class,'connection_rejected');self.assertIsNone(result.scope)
