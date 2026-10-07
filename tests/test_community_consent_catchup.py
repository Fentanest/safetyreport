"""Consent catch-up: real loopback HTTP, encrypted disk state and restart recovery."""
import os
import time
from unittest import mock

from services import community_account_ops as ops, community_auth_service as cas
from services import community_cloud as cloud, community_gate as gate
from test_community_gate import GateTestBase, USER_A, USER_B, POLICY, HASH


class ConsentCatchupTests(GateTestBase):
    def setUp(self):
        super().setUp()
        self.wall = time.time()
        patch = mock.patch.object(cloud, '_now', lambda: self.wall)
        patch.start()
        self.addCleanup(patch.stop)

    def account_row(self, user=USER_A):
        key = cloud._account_key(self.cfg.supabase_url, user['id'])
        return cloud._read_state()['accounts'].get(key, {})

    def restart(self):
        self.service.shutdown(1.0)
        self.service = self.make_service(self.tmp)
        self.service.upload_allowed_provider = cas._gate_can_enter
        for patch in (mock.patch.object(cas, '_default', self.service),
                      mock.patch.object(gate, '_gate', gate._Gate(clock=self.clock))):
            patch.start()
            self.addCleanup(patch.stop)

    def test_existing_active_first_status_and_restart_do_not_schedule(self):
        self.assertTrue(self.open_gate()['can_enter'])
        self.assertIsNone(cloud.current_job())
        gate.refresh_now()  # Persist the writer binding created by the first status.
        before = self.account_row()
        self.assertEqual([event['state'] for event in before['history']], ['active'])
        self.restart()
        for _ in range(3):
            self.assertTrue(gate.refresh_now()['can_enter'])
        self.assertEqual(self.account_row(), before)
        self.assertFalse(self.account_row().get('jobs'))

    def test_first_active_without_grant_then_grant_fill_is_baseline(self):
        self.connect()
        self.account.grant(USER_A)
        grant = self.account.consents[USER_A['id']]['grant_id']
        self.account.consents[USER_A['id']]['grant_id'] = None
        gate.refresh_now()
        self.assertEqual(self.account_row()['consent']['state'], 'active')
        self.assertIsNone(cloud.current_job())
        self.restart()
        self.account.consents[USER_A['id']]['grant_id'] = grant
        gate.refresh_now()
        self.assertEqual(self.account_row()['consent']['grant_id'], grant)
        self.assertFalse(self.account_row().get('jobs'))
        self.assertEqual(len(self.account_row()['history']), 1)

    def test_explicit_first_consent_schedules_after_http_confirmation(self):
        self.connect()
        self.assertEqual(self.account_row(), {})
        self.assertTrue(ops.consent(POLICY, HASH)['result']['created'])
        job = cloud.current_job()
        self.assertIsNotNone(job)
        self.assertEqual(job[2]['phase'], 'reshare')
        self.assertEqual(job[1], self.account.consents[USER_A['id']]['grant_id'])
        self.assertEqual(self.account.count('consent'), 1)
        self.assertNotIn('catchup_requested', self.account_row())
        self.restart()
        gate.refresh_now()
        self.assertEqual(cloud.current_job(), job)

    def test_explicit_consent_status_outage_survives_encrypted_store_restart(self):
        self.connect()
        consent_handler = self.account._consent
        def consent_then_outage(uid, sid, body):
            result = consent_handler(uid, sid, body)
            self.account.fail.append((503, 'server_error'))
            return result
        with mock.patch.object(self.account, '_consent', side_effect=consent_then_outage):
            ops.consent(POLICY, HASH)
        row = self.account_row()
        self.assertTrue(row['catchup_requested'])
        self.assertFalse(row.get('jobs'))
        self.assertGreater(cloud.remaining(), 0)
        path = os.path.join(self.service.store.auth_dir, 'community_consent.enc')
        with open(path, 'rb') as stream:
            encrypted = stream.read()
        for value in (b'catchup_requested', b'active', USER_A['id'].encode()):
            self.assertNotIn(value, encrypted)
        self.restart()
        gate.refresh_now()
        self.assertEqual(self.account_row(), row)
        self.wall += 301
        self.assertTrue(gate.refresh_now()['can_enter'])
        job = cloud.current_job()
        self.assertIsNotNone(job)
        self.assertNotIn('catchup_requested', self.account_row())
        self.restart()
        gate.refresh_now()
        self.assertEqual(cloud.current_job(), job)
        self.assertEqual(len(self.account_row()['jobs']), 1)

    def test_failed_consent_post_does_not_leave_request(self):
        self.connect()
        with self.assertRaises(cas.CommunityAuthError):
            ops.consent(POLICY, 'not-the-current-policy-hash')
        self.assertNotIn('catchup_requested', self.account_row())
        self.account.grant(USER_A)
        gate.refresh_now()
        self.assertIsNone(cloud.current_job())

    def test_known_nonactive_to_active_schedules_once(self):
        self.connect()
        for prior_state in ('none', 'revoked', 'outdated'):
            with self.subTest(prior_state=prior_state):
                self.account.grant(USER_A, state=prior_state)
                gate.refresh_now()
                self.account.grant(USER_A)
                gate.refresh_now()
                job = cloud.current_job()
                self.assertIsNotNone(job)
                self.assertEqual(job[1], self.account.consents[USER_A['id']]['grant_id'])
                self.restart()
                gate.refresh_now()
                self.assertEqual(cloud.current_job(), job)

    def test_changed_nonnull_grant_schedules_once(self):
        self.open_gate()
        previous = self.account_row()['consent']['grant_id']
        self.account.grant(USER_A)
        gate.refresh_now()
        job = cloud.current_job()
        self.assertIsNotNone(job)
        self.assertNotEqual(job[1], previous)
        self.restart()
        gate.refresh_now()
        self.assertEqual(cloud.current_job(), job)
        self.assertEqual(len(self.account_row()['jobs']), 1)

    def test_existing_pending_running_completed_jobs_are_preserved(self):
        self.open_gate()
        ops.consent(POLICY, HASH)
        key, grant, _ = cloud.current_job()
        for state in ('pending', 'running', 'succeeded'):
            with self.subTest(state=state):
                cloud.update_job(key, grant, state=state, phase='crawl', run_id='fixture-existing-run')
                before = self.account_row()['jobs']
                self.restart()
                gate.refresh_now()
                ops.consent(POLICY, HASH)
                self.assertEqual(self.account_row()['jobs'], before)
                self.assertNotIn('catchup_requested', self.account_row())
                if state == 'succeeded':
                    self.assertIsNone(cloud.current_job())

    def test_unknown_transport_result_does_not_change_baseline(self):
        self.open_gate()
        gate.refresh_now()
        before = self.account_row()
        self.account.fail.append((503, 'server_error'))
        gate.refresh_now()
        self.assertEqual(self.account_row(), before)
        self.wall += 301
        gate.refresh_now()
        self.assertEqual(self.account_row(), before)
        self.assertIsNone(cloud.current_job())

    def test_explicit_request_is_scoped_to_account(self):
        self.connect()
        cloud.consent_accepted(self.service)
        self.connect(USER_B)
        self.account.grant(USER_B)
        gate.refresh_now()
        self.assertTrue(self.account_row(USER_A)['catchup_requested'])
        self.assertFalse(self.account_row(USER_B).get('jobs'))
        self.assertNotIn('catchup_requested', self.account_row(USER_B))
