"""철회 웹훅이 세션을 지운 뒤 같은 카카오로 다시 로그인하면 로그인을 두 번 시키지 않는다(G1 세션 기록 + keep_session)."""
import time
import unittest
from unittest import mock

from services import community_auth_service as cas, community_cloud as cloud, community_gate as gate
from test_community_auth import USER_A
from test_final_pc_fixes import _AppBase


def _g1_row():
    service = cas.get_service()
    key = cloud._account_key(service.config().supabase_url, USER_A['id'])
    return key, cloud._read_state()['accounts'].get(key, {})


class FreshSessionReset(_AppBase):
    def _g1_then_lost_session(self, token):
        self.route_login(token)
        self.consent(token)
        self.assertEqual(gate.refresh_now()['state'], 'ok')
        self.assertTrue(_g1_row()[1].get('last_g1', {}).get('session'))
        cas.get_service().disconnect()  # 웹훅이 세션을 지움 → reauth
        time.sleep(1.1)  # connected_at 은 초 단위

    def test_relogin_after_revoked_goes_to_consent_without_second_login(self):
        token = self.login()
        self._g1_then_lost_session(token)
        self.account.consents[USER_A['id']]['state'] = 'revoked'
        answer = self.route_login(token)
        self.assertEqual(answer['gate']['state'], 'consent_required', answer['gate'])
        self.assertIsNotNone(cas.get_service().store.load().get('current'), '새 로그인 세션이 끊기면 안 된다')
        self.assertNotIn('last_g1', _g1_row()[1])

    def test_same_session_revoked_still_disconnects(self):
        token = self.login()
        self.route_login(token)
        self.consent(token)
        self.assertEqual(gate.refresh_now()['state'], 'ok')
        self.account.consents[USER_A['id']]['state'] = 'revoked'
        gate.invalidate('t')
        view = gate.refresh_now()
        self.assertIsNone(cas.get_service().store.load().get('current'))
        self.assertEqual(view['state'], 'kakao_required', view)

    def test_seeded_g1_without_session_still_disconnects(self):
        token = self.login()
        self._g1_then_lost_session(token)
        self.account.consents[USER_A['id']]['state'] = 'revoked'
        key, _ = _g1_row()
        with cloud._state() as st:
            st['accounts'][key]['last_g1'].pop('session')
        self.route_login(token)
        self.assertIsNone(cas.get_service().store.load().get('current'))

    def test_contributor_none_uses_same_rule(self):
        token = self.login()
        self._g1_then_lost_session(token)
        self.account.contributor[USER_A['id']] = 'none'
        answer = self.route_login(token)
        self.assertIsNotNone(cas.get_service().store.load().get('current'))
        self.assertNotEqual(answer['gate']['state'], 'kakao_required', answer['gate'])

    def test_mark_g1_refreshes_session_within_600_seconds(self):
        token = self.login()
        self.route_login(token)
        self.consent(token)
        service = cas.get_service()
        current = dict(service.store.load()['current'])
        dkey = gate.dataset_key(gate.official_username())
        cloud.mark_g1(service, current, dkey)
        first = _g1_row()[1]['last_g1']
        self.assertEqual(first['session'], current['connected_at'])
        cloud.mark_g1(service, dict(current, connected_at='2999-01-01T00:00:00Z'), dkey)
        self.assertEqual(_g1_row()[1]['last_g1']['session'], '2999-01-01T00:00:00Z')
        self.assertTrue(cloud.g1_session_is_fresh(service, current))
        self.assertFalse(cloud.g1_session_is_fresh(service, dict(current, connected_at='2999-01-01T00:00:00Z')))


if __name__ == '__main__':
    unittest.main()
