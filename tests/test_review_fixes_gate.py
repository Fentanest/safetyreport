"""검수 결정 1·6·7·배포 위험: 철회 뒤 재로그인 무한 리셋, 철회 재시도 경로, G7 엄격 판정, 업그레이드 G1 시드.

실제 loopback 가짜 중앙을 쓰므로 소켓이 필요하다(test_community_gate 와 같은 기반).
"""
import json
import time
import unittest
from unittest import mock

import test_community_gate as G
from services import community_account_ops as ops, community_cloud as cloud, community_gate as gate, community_reset as reset
from test_community_auth import USER_A


def _quiet(cls):
    """기반 클래스의 시험을 다시 돌리지 않는다."""
    for name in dir(cls):
        if name.startswith('test') and name not in cls.__dict__:
            setattr(cls, name, None)
    return cls


class ReloginAfterRevokeTests(G.GateTestBase):
    def reset_spy(self):
        return mock.patch.object(ops, 'soft_reset', wraps=ops.soft_reset)

    def test_relogin_after_remote_revoke_reaches_consent_screen_through_regate_without_second_reset(self):
        self.open_gate()
        with self.reset_spy() as spy:
            self.account.consents[USER_A['id']]['state'] = 'revoked'
            result = gate.refresh_now()
            self.assertEqual(result['state'], 'kakao_required')
            self.assertEqual(spy.call_count, 1)
            self.assertIsNone(self.service.store.load().get('current'))
            # 같은 계정으로 다시 로그인: 실제 라우트가 부르는 regate('login') 와 이어지는 확인
            self.connect(USER_A)
            for _ in range(4):
                shown = ops.regate('login')
                self.assertEqual(shown['state'], 'consent_required', shown)
                self.assertIsNotNone(self.service.store.load().get('current'), '로그인이 다시 지워지면 안 된다')
                gate.refresh_now()
            self.assertEqual(spy.call_count, 1, '근거가 지워졌으므로 두 번째 리셋은 없다')
            # 재동의 → 입장, 이후 다시 철회하면 리셋은 다시 한 번만
            self.account.grant(USER_A)
            self.assertTrue(ops.regate('consent_saved')['can_enter'])
            self.account.consents[USER_A['id']]['state'] = 'revoked'
            gate.refresh_now()
            self.assertEqual(spy.call_count, 2)

    def test_account_deletion_g8_resets_once_then_login_shows_consent(self):
        self.open_gate()
        with self.reset_spy() as spy:
            self.account.consents.pop(USER_A['id'])
            self.account.contributor[USER_A['id']] = 'none'
            self.assertEqual(gate.refresh_now()['state'], 'kakao_required')
            self.assertEqual(spy.call_count, 1)
            self.connect(USER_A)
            self.assertEqual(ops.regate('login')['state'], 'consent_required')
            gate.refresh_now()
            self.assertEqual(spy.call_count, 1)

    def test_login_without_prior_g1_never_resets(self):
        self.connect(USER_A)
        self.account.grant(USER_A, state='revoked')
        with self.reset_spy() as spy:
            self.assertEqual(ops.regate('login')['state'], 'consent_required')
            self.assertEqual(spy.call_count, 0)

    def test_new_grant_after_account_removal_clears_the_sticky_denial(self):
        self.open_gate()
        self.account.consents.pop(USER_A['id'])
        self.account.contributor[USER_A['id']] = 'none'
        gate.refresh_now()
        self.account.contributor.pop(USER_A['id'])
        self.connect(USER_A)
        ops.regate('login')
        self.account.grant(USER_A)  # 지도 웹 등 다른 곳에서 동의한 경우: 이 기기에서 consent POST 가 없어도 입장한다
        self.assertTrue(ops.regate('login')['can_enter'])


class G7StrictnessTests(G.GateTestBase):
    """gate.kakao is False 이고 reasons 에 kakao_missing 일 때만 G7. 이상한 응답은 리셋이 아니라 확인 불가다."""

    def check(self, change, expect_reset):
        self.open_gate()
        original = self.account._status
        self.account._status = lambda uid, sid, body: (lambda r: (r[0], change(r[1])))(original(uid, sid, body))
        with mock.patch.object(ops, 'soft_reset', wraps=ops.soft_reset) as spy:
            gate.refresh_now()
        self.assertEqual(spy.call_count, 1 if expect_reset else 0)
        self.assertEqual(self.service.store.load().get('current') is None, expect_reset)


def _set(key, value):
    def change(body):
        body[key] = value
        return body
    return change


def _revoked_and_suspended(body):
    body['contributor'] = {'status': 'suspended'}
    body['consent'] = dict(body['consent'], state='revoked')
    return body


_G7_CASES = [
    ('kakao_missing_resets', _set('gate', {'kakao': False, 'reasons': ['kakao_missing']}), True),
    ('session_missing_does_not', _set('gate', {'kakao': False, 'reasons': ['session_missing']}), False),
    ('user_not_eligible_does_not', _set('gate', {'kakao': False, 'reasons': ['user_not_eligible']}), False),
    ('no_reasons_does_not', _set('gate', {'kakao': False, 'reasons': []}), False),
    ('non_string_reasons_do_not', _set('gate', {'kakao': False, 'reasons': [7, None]}), False),
    ('gate_missing', _set('gate', None), False),
    ('gate_not_object', _set('gate', 'oops'), False),
    ('kakao_key_missing', _set('gate', {'reasons': ['kakao_missing']}), False),
    ('kakao_string_false', _set('gate', {'kakao': 'false', 'reasons': ['kakao_missing']}), False),
    ('kakao_zero', _set('gate', {'kakao': 0, 'reasons': ['kakao_missing']}), False),
    ('contributor_missing', _set('contributor', None), False),
    ('contributor_status_number', _set('contributor', {'status': 3}), False),
    ('consent_state_number', _set('consent', {'state': 3}), False),
    ('consent_not_object', _set('consent', 'x'), False),
    ('suspension_beats_revoke_reset', _revoked_and_suspended, False),
]
for _name, _change, _expect in _G7_CASES:
    setattr(G7StrictnessTests, 'test_' + _name, (lambda change, expect: lambda self: self.check(change, expect))(_change, _expect))


class RevokeRetryRouteTests(G.GateAppTests):
    def test_revoke_retry_route_is_allowed_in_the_gate_and_reports_the_result(self):
        token = self.login()
        rid = self.start_and_get_code(USER_A)
        self.assertEqual(self.post('/settings/community/confirm', {'request_id': rid}, token=token).status_code, 200)
        self.post('/settings/community/consent', {'accepted': True, 'policy_version': G.POLICY, 'consent_text_sha256': G.HASH}, token=token)
        grant = gate.current_grant_id()
        self.assertIn(('POST', '/settings/community/revoke-retry'), self.main._GATE_ALLOW)
        # 서버 철회가 일시 실패해 대기 중
        self.account.fail = [(503, 'server_error')] * 3
        reset.begin(grant)
        self.assertEqual(gate.evaluate()['state'], 'revoke_pending')
        no_csrf = self.post('/settings/community/revoke-retry', {})
        self.assertEqual(no_csrf.status_code, 403, 'CSRF 는 유지된다')
        self.account.fail = []
        answer = self.post('/settings/community/revoke-retry', {}, token=token)
        self.assertEqual(answer.status_code, 200, answer.text)  # 예전에는 항상 403
        body = answer.json()
        self.assertTrue(body['data']['result']['revoked'])
        self.assertEqual(body['data']['gate']['state'], 'kakao_required')
        self.assertIsNone(reset.pending())

    def test_revoke_pending_page_renders_retry_button_with_result_area(self):
        token = self.login()
        rid = self.start_and_get_code(USER_A)
        self.post('/settings/community/confirm', {'request_id': rid}, token=token)
        self.post('/settings/community/consent', {'accepted': True, 'policy_version': G.POLICY, 'consent_text_sha256': G.HASH}, token=token)
        self.account.fail = [(503, 'server_error')] * 3
        reset.begin(gate.current_grant_id())
        page = self.client.get('/onboarding/community')
        self.assertEqual(page.status_code, 200)
        for marker in ('id="revokeRetry"', 'id="revokeRetryResult"', '/settings/community/revoke-retry'):
            self.assertIn(marker, page.text)

    def test_revoke_retry_requires_admin_session(self):
        r = self.post('/settings/community/revoke-retry', {})
        self.assertIn(r.status_code, (401, 403, 303))

    def test_relogin_through_routes_after_revoke_gets_consent_screen_not_another_reset(self):
        token = self.login()
        rid = self.start_and_get_code(USER_A)
        self.post('/settings/community/confirm', {'request_id': rid}, token=token)
        self.post('/settings/community/consent', {'accepted': True, 'policy_version': G.POLICY, 'consent_text_sha256': G.HASH}, token=token)
        self.account.consents[USER_A['id']]['state'] = 'revoked'
        with mock.patch.object(ops, 'soft_reset', wraps=ops.soft_reset) as spy:
            self.assertEqual(gate.refresh_now()['state'], 'kakao_required')
            for _ in range(2):
                rid = self.start_and_get_code(USER_A)
                answer = self.post('/settings/community/confirm', {'request_id': rid}, token=token)
                self.assertEqual(answer.status_code, 200, answer.text)
                self.assertEqual(answer.json()['gate']['state'], 'consent_required')
                self.assertEqual(self.client.get('/settings/community/gate').json()['data']['state'], 'consent_required')
                self.assertIsNotNone(self.service.store.load().get('current'))
            self.assertEqual(spy.call_count, 1)
        policy = self.client.get('/settings/community/policy').json()['data']
        self.assertEqual(policy['policy_version'], G.POLICY)
        done = self.post('/settings/community/consent', {'accepted': True, 'policy_version': G.POLICY, 'consent_text_sha256': G.HASH}, token=token)
        self.assertEqual(done.json()['data']['gate']['state'], 'ok')


class UpgradeSeedTests(G.GateTestBase):
    """업데이트 직후 첫 status 가 실패해도, 최신 이력이 active 인 기존 사용자는 '지금'을 last_g1 으로 한 번 시드받는다."""

    def pre_upgrade(self, state='active', age_seconds=90 * 24 * 3600):
        self.open_gate()
        gate.refresh_now()
        with cloud._state() as st:
            row = next(iter(st['accounts'].values()))
            row.pop('last_g1', None)  # 옛 버전은 last_g1 을 쓰지 않았다
            row['consent'] = dict(row['consent'], state=state, at=time.time() - age_seconds)

    def outage(self):
        gate._gate._reset_for_tests()
        self.account.fail = [(503, 'server_error')] * 5
        return gate.refresh_now()

    def row(self):
        with cloud._state() as st:
            return dict(next(iter(st['accounts'].values())))

    def test_old_active_history_is_seeded_with_now_once(self):
        self.pre_upgrade()  # 이력은 90일 전 전이 — 예전 기준이면 즉시 잠겼다
        before = time.time()
        result = self.outage()
        self.assertTrue(result['can_local'], result)
        self.assertFalse(result['can_enter'])
        row = self.row()
        self.assertGreaterEqual(row['last_g1']['at'], before)
        self.assertTrue(row['g1_seeded'])
        # 시드는 한 번뿐: 지운 뒤에는 다시 시드하지 않는다
        with cloud._state() as st:
            next(iter(st['accounts'].values())).pop('last_g1')
        self.assertFalse(self.outage()['can_local'])

    def _not_seeded(self, state):
        self.pre_upgrade(state=state)
        self.assertFalse(self.outage()['can_local'])
        self.assertNotIn('g1_seeded', self.row())

    def test_latest_revoked_is_not_seeded(self):
        self._not_seeded('revoked')

    def test_latest_outdated_is_not_seeded(self):
        self._not_seeded('outdated')

    def test_latest_none_is_not_seeded(self):
        self._not_seeded('none')

    def test_other_member_is_not_seeded(self):
        self.pre_upgrade()
        with mock.patch('services.account_data.db_owner', return_value='another-member'):
            self.assertFalse(self.outage()['can_local'])
        self.assertNotIn('g1_seeded', self.row())

    def test_other_dataset_is_not_seeded(self):
        self.pre_upgrade()
        with mock.patch.object(gate, 'official_username', lambda: 'another-official'):
            self.assertFalse(self.outage()['can_local'])
        self.assertNotIn('g1_seeded', self.row())

    def test_reset_account_is_not_seeded_again(self):
        self.pre_upgrade()
        with cloud._state() as st:
            next(iter(st['accounts'].values()))['reset_completed_at'] = time.time()
        self.assertFalse(self.outage()['can_local'])

    def test_seeded_grace_still_expires_after_72_hours(self):
        self.pre_upgrade()
        self.assertTrue(self.outage()['can_local'])
        with mock.patch.object(cloud, '_now', return_value=time.time() + 73 * 3600):
            gate._gate._reset_for_tests()
            self.account.fail = [(503, 'server_error')] * 5
            self.assertFalse(gate.refresh_now()['can_local'])


class StatusPrivacyTests(G.GateTestBase):
    def test_ingest_status_cache_has_no_account_or_dataset_identity(self):
        self.open_gate()
        gate.refresh_now()
        text = self.store.meta('ingest_status')
        self.assertIsNotNone(text)
        data = json.loads(text)
        self.assertEqual(sorted(data), ['consent', 'ingest'])
        self.assertNotIn('로컬', text)  # fake account.display_name
        self.assertNotIn(G.community_gate.dataset_key(G.OFFICIAL_ID), text)


for _cls in (RevokeRetryRouteTests,):
    _quiet(_cls)

if __name__ == '__main__':
    unittest.main()
