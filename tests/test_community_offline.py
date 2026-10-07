"""Emergency local-use / cloud-send separation, using loopback fake Supabase only."""
import json
import os
import threading
import time
import unittest
from unittest import mock

from services import community_cloud as cloud, community_gate as gate, community_auth_service as cas
from services import community_capture as capture, community_upload_status as upload_status
from services import community_uploader as real_uploader
from test_community_gate import GateTestBase, USER_A, USER_B
from test_community_uploader import INPUT, ack, ok_resp


class OfflinePolicyTests(GateTestBase):
    def setUp(self):
        super().setUp()
        self.wall = time.time()
        patch = mock.patch.object(cloud, '_now', lambda: self.wall)
        patch.start(); self.addCleanup(patch.stop)

    def test_shared_deadline_survives_restart_and_other_callers(self):
        self.open_gate()
        calls = []
        def unavailable():
            calls.append(1)
            return 429, b'', {'Retry-After': '600'}
        cloud.run(self.cfg.supabase_url, unavailable)
        for _ in range(20):
            with self.assertRaises(cloud.CloudDeferred):
                cloud.run(self.cfg.supabase_url, lambda: self.fail('network before deadline'))
            gate.refresh_now()
        self.assertEqual(len(calls), 1)
        self.assertEqual(round(cloud.remaining()), 600)
        with mock.patch.object(gate, '_gate', gate._Gate(clock=self.clock)):
            value = gate.refresh_now()
            self.assertTrue(value['can_local'])
            self.assertFalse(value['can_enter'])
            self.assertEqual(round(cloud.remaining()), 600)
        self.wall += 599
        with self.assertRaises(cloud.CloudDeferred):
            cloud.run(self.cfg.supabase_url, unavailable)
        self.wall += 1
        cloud.run(self.cfg.supabase_url, lambda: (200, b'{}', {}))
        self.assertEqual(cloud.remaining(), 0)

    def test_timeout_503_and_http_date_have_minimum_five_minutes(self):
        self.open_gate()
        for response in ((503, b'bad gateway', {}), (429, b'', {})):
            cloud.run(self.cfg.supabase_url, lambda: response)
            self.assertGreaterEqual(cloud.remaining(), 300)
            self.wall += 301
        with self.assertRaises(cloud.CloudDeferred):
            cloud.run(self.cfg.supabase_url, lambda: (_ for _ in ()).throw(TimeoutError()))
        self.assertEqual(cloud.remaining(), 300)
        self.assertEqual(cloud.retry_after({'Date':'Wed, 07 Oct 2026 00:00:00 GMT', 'Retry-After':'Wed, 07 Oct 2026 00:10:00 GMT'}, {'error':{'retryAfterSeconds':5}}), 600)

    def test_concurrent_failures_coalesce_at_http_boundary(self):
        self.open_gate()
        entered = threading.Event(); release = threading.Event(); calls=[]
        def send():
            calls.append(1); entered.set(); release.wait(2)
            return 503, b'', {}
        def worker():
            try: cloud.run(self.cfg.supabase_url, send)
            except cloud.CloudDeferred: pass
        workers = [threading.Thread(target=worker) for _ in range(8)]
        for worker_thread in workers: worker_thread.start()
        self.assertTrue(entered.wait(2)); release.set()
        for worker_thread in workers: worker_thread.join(3)
        self.assertEqual(len(calls), 1)

    def test_revoked_and_no_proof_allow_owned_local_but_suspension_does_not(self):
        self.open_gate()
        self.account.consents[USER_A['id']]['state']='revoked'
        result=gate.refresh_now()
        self.assertTrue(result['can_local']); self.assertFalse(result['can_enter'])
        self.clock.now += 10000
        self.account.fail=[(503,'server_error')]
        gate.refresh_now()
        with mock.patch.object(gate, '_gate', gate._Gate(clock=self.clock)):
            self.assertTrue(gate.refresh_now()['can_local'])
        self.wall += 301
        self.account.contributor[USER_A['id']]='suspended'
        self.assertFalse(gate.refresh_now()['can_local'])
        self.account.fail=[(503,'server_error')]
        self.clock.now += 10000
        with mock.patch.object(gate, '_gate', gate._Gate(clock=self.clock)):
            self.assertFalse(gate.refresh_now()['can_local'])

    def test_new_no_login_and_foreign_owner_stay_blocked(self):
        self.assertFalse(gate.evaluate()['can_local'])
        self.open_gate()
        from services import account_data
        with mock.patch.object(account_data,'db_owner',return_value='foreign-fixture-owner'):
            self.account.fail=[(503,'server_error')]
            self.assertFalse(gate.refresh_now()['can_local'])

    def test_consent_history_encrypted_deduplicated_and_unknown_not_recorded(self):
        self.open_gate()
        gate.refresh_now()
        before = cloud._read_state()['accounts']
        cloud.observe(self.service, kind='unknown', source='timeout')
        self.assertEqual(before, cloud._read_state()['accounts'])
        row = next(iter(before.values()))
        self.assertEqual([e['state'] for e in row['history']], ['active'])
        path=os.path.join(self.service.store.auth_dir,'community_consent.enc')
        with open(path,'rb') as stream: raw=stream.read()
        self.assertNotIn(b'active',raw)
        self.assertNotIn(USER_A['id'].encode(),raw)
        with open(os.path.join(self.service.store.auth_dir,'community_cloud.db'),'rb') as stream:
            self.assertNotIn(USER_A['id'].encode(),stream.read())

    def test_offline_capture_recovery_ack_has_no_loss_or_duplicate(self):
        self.open_gate()
        first=capture.capture(INPUT,source_report_id='offline-fixture-1',trigger='test',data_dir=self.data)
        self.account.fail=[(503,'server_error')]
        self.assertTrue(gate.refresh_now()['can_local'])
        second=capture.capture(INPUT,source_report_id='offline-fixture-2',trigger='test',data_dir=self.data)
        self.assertEqual(self.store.connect().execute('SELECT COUNT(*) FROM outbox').fetchone()[0],2)
        self.assertIsNone(capture.capture(INPUT,source_report_id='offline-fixture-2',trigger='test',data_dir=self.data).event_id)
        self.wall+=301
        self.assertTrue(gate.refresh_now()['can_enter'])
        sent=[]
        def send(envelope, **kwargs):
            ids=[x['event_id'] for x in envelope['events']]; sent.extend(ids)
            return ok_resp([ack(x) for x in ids])
        with mock.patch('services.community_ingest_client.post_envelope',side_effect=send), mock.patch.object(real_uploader,'_sleep'):
            real_uploader.request_upload('recovery',self.data)
            real_uploader.request_upload('recovery',self.data)
        self.assertCountEqual(sent,[first.event_id,second.event_id])
        self.assertEqual(self.store.connect().execute('SELECT COUNT(*) FROM outbox').fetchone()[0],0)

    def test_reconsent_reshare_retains_provenance_and_deduplicates(self):
        self.open_gate()
        self.account.consents[USER_A['id']]['state']='revoked'
        gate.refresh_now()
        row=capture.capture(INPUT,source_report_id='revoked-fixture',trigger='test',data_dir=self.data)
        original=dict(self.store.connect().execute('SELECT * FROM source_journal WHERE event_id=?',(row.event_id,)).fetchone())
        self.assertEqual(original['blocked_reason'],'consent:revoked')
        self.account.grant(USER_A)
        gate.refresh_now()
        grant=self.store.active_context()['consent_grant_id']
        result=upload_status.request_reshare(self.data,consent_grant=grant,send=False)
        self.assertEqual(result['reshared'],1)
        self.assertEqual(upload_status.request_reshare(self.data,consent_grant=grant,send=False)['count'],0)
        self.assertEqual(original,dict(self.store.connect().execute('SELECT * FROM source_journal WHERE event_id=?',(row.event_id,)).fetchone()))

    def test_local_revoke_pending_survives_network_failure(self):
        self.open_gate()
        cloud.deny('consent_revoked',sticky=True)
        cloud.observe(self.service,kind='revoked',source='local_revoke_pending')
        gate.invalidate('consent_revoked')
        self.assertTrue(gate.refresh_now()['can_local'])
        self.assertFalse(gate.evaluate()['can_enter'])
        with mock.patch.object(gate, '_gate', gate._Gate(clock=self.clock)):
            self.account.fail=[(503,'server_error')]
            self.assertTrue(gate.refresh_now()['can_local'])
            self.assertFalse(gate.evaluate()['can_enter'])

    def test_grant_job_is_durable_once_and_full_not_reset(self):
        self.open_gate()
        from services import community_consent_jobs as jobs, crawl_run_state
        key,grant,job=cloud.current_job()
        launched=[]
        def launch(**kwargs):
            self.assertEqual(kwargs['crawl_mode'],'full')
            self.assertTrue(kwargs['force_full'])
            run_id=crawl_run_state.create(); kwargs['on_run_created'](run_id,None)
            crawl_run_state.write(run_id,'succeeded'); launched.append(run_id)
        with mock.patch('services.crawl_manager.crawl_manager.is_crawling',return_value=False), mock.patch('services.crawl_control.start_crawl',side_effect=launch), mock.patch.object(upload_status,'request_reshare',return_value={'result':'queued'}):
            jobs.tick(); jobs.tick(); gate.refresh_now(); jobs.tick()
        self.assertEqual(len(launched),1)
        self.assertIsNone(cloud.current_job())

    def test_restored_writer_uploads_waiting_rows_after_completed_grant_job(self):
        self.open_gate()
        key, grant, _ = cloud.current_job()
        cloud.update_job(key, grant, state='succeeded')
        self.store.deactivate_context('cloud_unavailable')
        cloud.run(self.cfg.supabase_url, lambda: (503, b'', {}))
        saved = capture.capture(INPUT, source_report_id='restored-fixture', trigger='test', data_dir=self.data)
        self.assertEqual(self.store.connect().execute('SELECT blocked_reason FROM source_journal WHERE event_id=?', (saved.event_id,)).fetchone()[0], 'consent:unknown')
        self.wall += 301
        self.assertTrue(gate.refresh_now()['can_enter'])
        sent = []
        def send(envelope, **kwargs):
            self.assertEqual(envelope['trigger'], 'reshare')
            self.assertEqual(envelope['events'][0]['event_type'], 'reshare')
            sent.extend(envelope['events'])
            return ok_resp([ack(row['event_id']) for row in envelope['events']])
        with mock.patch('services.community_ingest_client.post_envelope', side_effect=send), mock.patch.object(real_uploader, '_sleep'):
            real_uploader.request_upload('realtime', self.data)
            real_uploader.request_upload('recovery', self.data)
        self.assertEqual(len(sent), 1)
        self.assertIsNone(cloud.current_job())

    def test_actual_clients_share_cooldown_before_any_network_call(self):
        self.open_gate()
        from services import community_ingest_client as ingest
        from services.community_account_client import CommunityAccountClient, AccountApiError
        from services.community_auth_client import CommunityAuthClient, AuthError
        config = mock.Mock(supabase_url=self.cfg.supabase_url, publishable_key='fixture', configured=True)
        with mock.patch.object(ingest, '_config', return_value=config), mock.patch.object(cas, 'get_access_token', return_value='fixture'), mock.patch.object(ingest, '_http_post', return_value=(429, b'{}', {'Retry-After':'600'})) as post:
            self.assertFalse(ingest.post_manifest(connection_id='fixture')[0])
            self.assertFalse(ingest.post_manifest(connection_id='fixture')[0])
            self.assertEqual(post.call_count, 1)
        account = CommunityAccountClient(self.cfg.supabase_url, 'fixture')
        auth = CommunityAuthClient(self.cfg.supabase_url, 'fixture')
        with mock.patch.object(account.http, 'post') as post:
            with self.assertRaises(AccountApiError): account.status('fixture')
            post.assert_not_called()
        with mock.patch.object(auth.http, 'get') as get:
            with self.assertRaises(AuthError): auth.get_user(access_token='fixture')
            get.assert_not_called()
        self.assertEqual(cloud.remaining(), 600)

    def test_explicit_deletion_never_becomes_automatic_reshare(self):
        self.open_gate()
        saved = capture.capture(INPUT, source_report_id='deleted-fixture', trigger='test', data_dir=self.data)
        with self.store.transaction() as tx:
            tx.execute("UPDATE source_journal SET blocked_reason='deleted_by_user' WHERE event_id=?", (saved.event_id,))
            tx.execute("UPDATE outbox SET state='blocked',last_error_code='deleted' WHERE event_id=?", (saved.event_id,))
        self.account.grant(USER_A)
        gate.refresh_now()
        grant = self.store.active_context()['consent_grant_id']
        self.assertEqual(upload_status.request_reshare(self.data, consent_grant=grant, send=False)['count'], 0)
        self.assertEqual(self.store.connect().execute('SELECT COUNT(*) FROM source_journal').fetchone()[0], 1)

    def test_no_consent_capture_keeps_owner_and_waits_for_real_grant(self):
        self.open_gate()
        self.account.consents.pop(USER_A['id'])
        self.assertTrue(gate.refresh_now()['can_local'])
        saved = capture.capture(INPUT, source_report_id='no-consent-fixture', trigger='test', data_dir=self.data)
        row = self.store.connect().execute('SELECT * FROM source_journal WHERE event_id=?', (saved.event_id,)).fetchone()
        self.assertEqual(row['blocked_reason'], 'consent:none')
        self.assertEqual(row['contributor_fingerprint'], gate.account_fingerprint(USER_A['id']))
        self.assertEqual(self.store.connect().execute('SELECT COUNT(*) FROM outbox').fetchone()[0], 0)
        self.account.grant(USER_A)
        gate.refresh_now()
        grant = self.store.active_context()['consent_grant_id']
        self.assertEqual(upload_status.request_reshare(self.data, consent_grant=grant, send=False)['reshared'], 1)

    def test_slow_cloud_request_does_not_block_local_evaluation(self):
        self.open_gate()
        entered = threading.Event(); release = threading.Event()
        def send():
            entered.set(); release.wait(3)
            return 200, b'{}', {}
        worker = threading.Thread(target=lambda: cloud.run(self.cfg.supabase_url, send))
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            start = time.monotonic()
            self.assertTrue(gate.evaluate()['can_local'])
            self.assertLess(time.monotonic() - start, 0.5)
        finally:
            release.set(); worker.join(3)

    def test_suspension_survives_local_revoke_outage_and_restart(self):
        self.open_gate()
        self.account.contributor[USER_A['id']] = 'suspended'
        self.assertFalse(gate.refresh_now()['can_local'])
        cloud.run(self.cfg.supabase_url, lambda: (503, b'', {}))
        from services import community_account_ops as ops
        with self.assertRaises(cas.CommunityAuthError): ops.consent_revoke()
        with mock.patch.object(gate, '_gate', gate._Gate(clock=self.clock)):
            self.assertFalse(gate.refresh_now()['can_local'])
            cloud.consent_accepted(self.service)
            self.assertFalse(gate.evaluate()['can_local'])
            self.wall += 301
            self.account.contributor[USER_A['id']] = 'none'
            self.assertFalse(gate.refresh_now()['can_local'])
            self.account.contributor[USER_A['id']] = 'active'
            self.assertTrue(gate.refresh_now()['can_local'])

    def test_reconsent_restored_db_recovers_prior_dataset_without_other_accounts_or_deleted(self):
        self.open_gate()
        self.account.consents[USER_A['id']]['state'] = 'revoked'
        gate.refresh_now()
        old_dataset = self.store.local_dataset_id()
        saved = capture.capture(INPUT, source_report_id='restore-reshare', trigger='test', data_dir=self.data)
        deleted = capture.capture(INPUT, source_report_id='restore-deleted', trigger='test', data_dir=self.data)
        foreign = capture.capture(INPUT, source_report_id='restore-foreign', trigger='test', data_dir=self.data)
        with self.store.transaction() as tx:
            tx.execute("UPDATE source_journal SET blocked_reason='deleted_by_user' WHERE event_id=?", (deleted.event_id,))
            tx.execute("UPDATE source_journal SET contributor_fingerprint=? WHERE event_id=?", (gate.account_fingerprint(USER_B['id']), foreign.event_id))
        new_dataset = self.store.rotate_dataset('same-account-db-restore-fixture')
        self.assertNotEqual(old_dataset, new_dataset)
        self.account.grant(USER_A)
        gate.refresh_now()
        grant = self.store.active_context()['consent_grant_id']
        self.assertEqual(upload_status.request_reshare(self.data, consent_grant=grant, send=False)['reshared'], 1)
        self.assertEqual(upload_status.request_reshare(self.data, consent_grant=grant, send=False)['count'], 0)
        rows = self.store.connect().execute("SELECT * FROM source_journal WHERE event_type='reshare'").fetchall()
        self.assertEqual([(row['source_report_id'], row['local_dataset_id']) for row in rows], [('restore-reshare', new_dataset)])
        original = self.store.connect().execute('SELECT * FROM source_journal WHERE event_id=?', (saved.event_id,)).fetchone()
        self.assertEqual((original['local_dataset_id'], original['blocked_reason']), (old_dataset, 'consent:revoked'))

    def test_transient_refresh_never_erases_local_session_even_with_auth_error_code(self):
        self.open_gate()
        from services.community_auth_client import AuthError
        original = self.service.store.load()['current']
        for status in (429, 503):
            client = mock.Mock()
            client.refresh.side_effect = AuthError('session_not_found', status)
            with mock.patch.object(self.service, '_client', return_value=client):
                with self.assertRaises(cas.CommunityAuthError) as caught:
                    self.service.get_access_token(rejected=original['access_token'])
            self.assertEqual(caught.exception.code, 'auth_unavailable')
            self.assertEqual(self.service.store.load()['current'], original)
        from services import community_upload_policy as policy
        from datetime import datetime, timezone
        for status in (429, 503):
            result = policy.interpret_response([], status, {}, '{"error":{"code":"consent_revoked","retryable":false}}', datetime.now(timezone.utc))
            self.assertIn(result.error_class, ('rate_limited', 'server_busy'))
