import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock
from services import community_reset as reset,community_account_ops as ops,community_gate as gate,community_cloud as cloud
from services.community_store import CommunityStore
from services.community_auth_service import CommunityAuthError

class ResetRetryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=CommunityStore.open(self.tmp.name);self.addCleanup(self.store.close)
        self.current={'user_id':'synthetic-user','kakao_id':'910001'};self.calls=[]
        self.service=SimpleNamespace(store=SimpleNamespace(load=lambda:{'current':self.current},save_writer=lambda w:self.calls.append('writer_clear')),disconnect=self.disconnect)
        self.store.set_context(contributor_fingerprint=gate.account_fingerprint(self.current['user_id']),connection_id='synthetic',writer_epoch=1,dataset_key='d'*64,consent_grant_id='g',policy_version='p',consent_text_sha256='h',source_app='safetyreport',source_mode='server')
        self.store.set_meta('raw_backfill',json.dumps({'state':'done'}))
        self.patches=[mock.patch.object(ops.cas,'get_service',return_value=self.service),mock.patch.object(CommunityStore,'open',return_value=self.store),mock.patch.object(cloud,'deny',side_effect=lambda *a,**k:self.calls.append('deny')),mock.patch.object(cloud,'observe'),mock.patch.object(cloud,'reset_evidence',side_effect=lambda *a,**k:self.calls.append('evidence')),mock.patch.object(gate,'invalidate',side_effect=self.invalidate),mock.patch.object(gate,'clear_after_soft_reset',side_effect=lambda:self.calls.append('cache_clear')),mock.patch.object(reset,'stop_work',side_effect=lambda:self.calls.append('stop'))]
        for p in self.patches:p.start();self.addCleanup(p.stop)
    def invalidate(self,reason):self.calls.append('lock');self.store.deactivate_context(reason)
    def disconnect(self):
        self.assertIsNone(self.store.active_context());self.assertIsNone(reset.pending(self.store))
        self.calls.append('logout');self.current=None
    def test_failure_keeps_session_and_persistent_retry_success_clears_only_active_state(self):
        reset.begin('g')
        self.assertEqual(self.calls[:3],['deny','lock','stop'])
        def unavailable(fn):self.calls.append('central');raise CommunityAuthError('auth_unavailable')
        with mock.patch.object(ops,'account_call',side_effect=unavailable):self.assertFalse(reset.attempt(force=True))
        item=reset.pending(self.store);self.assertEqual(item['attempts'],1);self.assertEqual(self.current['kakao_id'],'910001');self.assertNotIn('logout',self.calls)
        self.assertIsNone(self.store.active_context());self.assertIsNotNone(self.store.meta('raw_backfill'))
        def confirmed(fn):self.calls.append('central');return {'revoked':True}
        with mock.patch.object(ops,'account_call',side_effect=confirmed):self.assertTrue(reset.attempt(force=True))
        self.assertIsNone(self.current);self.assertIsNone(self.store.meta('raw_backfill'));self.assertIsNone(reset.pending(self.store))
        self.assertGreater(self.calls.index('logout'),len(self.calls)-1-self.calls[::-1].index('central'))
        self.assertLess(self.calls.index('evidence'),self.calls.index('logout'))  # G1 근거는 로그아웃 전에 지워 재로그인이 다시 리셋되지 않게 한다
    def test_revoke_completed_after_fresh_login_keeps_that_session_and_goes_to_consent(self):
        self.current['connected_at']='2026-10-21T01:00:00Z'
        reset.begin('g')
        with mock.patch.object(ops,'account_call',side_effect=CommunityAuthError('auth_unavailable')):self.assertFalse(reset.attempt(force=True))
        self.current=None  # 토큰이 사라짐
        self.current={'user_id':'synthetic-user','kakao_id':'910001','connected_at':'2026-10-21T02:00:00Z'}  # 철회 대기 중 새 카카오 로그인
        with mock.patch.object(ops,'account_call',side_effect=lambda fn:{'revoked':True}):self.assertTrue(reset.attempt(force=True))
        self.assertNotIn('logout',self.calls);self.assertEqual(self.current['connected_at'],'2026-10-21T02:00:00Z')
        self.assertIsNone(reset.pending(self.store));self.assertIsNone(self.store.meta('raw_backfill'));self.assertIsNone(self.store.active_context())
        for step in ('evidence','writer_clear','cache_clear'):self.assertIn(step,self.calls)
    def test_revoke_completed_with_original_session_still_disconnects(self):
        self.current['connected_at']='2026-10-21T01:00:00Z'
        reset.begin('g')
        with mock.patch.object(ops,'account_call',side_effect=lambda fn:{'revoked':True}):self.assertTrue(reset.attempt(force=True))
        self.assertIn('logout',self.calls);self.assertIsNone(self.current)
    def test_wrong_account_cannot_retry_another_accounts_grant(self):
        reset.begin('g');self.current={'user_id':'another-user','kakao_id':'other'}
        with mock.patch.object(ops,'account_call',side_effect=AssertionError('cross-account revoke')):
            self.assertFalse(reset.attempt(force=True))
        self.assertIsNotNone(reset.pending(self.store));self.assertNotIn('logout',self.calls)
    def test_stale_grant_is_reloaded_on_the_next_retry(self):
        reset.begin('old')
        with mock.patch.object(ops,'account_call',side_effect=CommunityAuthError('stale_grant')):self.assertFalse(reset.attempt(force=True))
        self.assertIsNone(reset.pending(self.store)['grant_id'])
        with mock.patch.object(ops,'account_call',side_effect=[{'consent':{'state':'active','grant_id':'fresh'}},{'revoked':True}]):
            self.assertTrue(reset.attempt(force=True))

    def test_late_confirmation_does_not_reset_another_session(self):
        reset.begin('g')
        def switched(fn):
            self.current={'user_id':'different-user','kakao_id':'other'}
            return {'revoked':True}
        with mock.patch.object(ops,'account_call',side_effect=switched):
            self.assertFalse(reset.attempt(force=True))
        self.assertEqual(self.current['user_id'],'different-user')
        self.assertNotIn('logout',self.calls)
        self.assertIsNotNone(reset.pending(self.store))
