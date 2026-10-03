import unittest
from unittest import mock

from services.community_gate import _Gate


class ActionFreshnessTests(unittest.TestCase):
    def test_failed_refresh_does_not_extend_action_freshness(self):
        for age in (61, 599):
            with self.subTest(age=age):
                gate = _Gate(clock=lambda: age)
                gate._verified_at = 0
                gate._invalidated = False
                gate._last_error = 'network_error'
                cached = {'state': 'ok', 'can_enter': True, 'reasons': [], 'verified_age': age}
                with mock.patch.object(gate, 'refresh_now', return_value=cached), \
                     mock.patch.object(gate, 'evaluate', return_value=cached):
                    result = gate.require_fresh()
                    self.assertFalse(result['can_enter'])
                    self.assertEqual(result['state'], 'verification_required')
                    self.assertIn('status_stale', result['reasons'])
                    self.assertTrue(gate.evaluate()['can_enter'])
                    self.assertTrue(cached['can_enter'])

    def test_successful_refresh_and_inclusive_sixty_seconds_allow_action(self):
        for age in (0, 59, 60):
            with self.subTest(age=age):
                gate = _Gate(clock=lambda: age)
                gate._verified_at = 0
                gate._invalidated = False
                cached = {'state': 'ok', 'can_enter': True, 'reasons': [], 'verified_age': age}
                with mock.patch.object(gate, 'evaluate', return_value=cached), \
                     mock.patch.object(gate, 'refresh_now') as refresh:
                    self.assertTrue(gate.require_fresh()['can_enter'])
                    refresh.assert_not_called()

    def test_refresh_cannot_allow_action_without_verified_age(self):
        gate = _Gate(clock=lambda: 70)
        with mock.patch.object(gate, 'refresh_now', return_value={'state': 'ok', 'can_enter': True}):
            self.assertFalse(gate.require_fresh()['can_enter'])
