import unittest
from unittest import mock
from core.crawler import login


class LoginRetryBoundaryTests(unittest.TestCase):
    def test_false_authentication_is_finite_as_well_as_exception(self):
        for throws in (False, True):
            with self.subTest(throws=throws):
                driver = mock.Mock(current_url='https://fixture/login')
                if throws:
                    driver.get.side_effect = RuntimeError('fixture')
                with mock.patch.object(login.settings._instance, 'load'), \
                     mock.patch.object(login.logger.LoggerFactory, 'logbot', mock.Mock()), \
                     mock.patch.object(login.settings, 'username', 'fixture'), \
                     mock.patch.object(login.settings, 'password', 'fixture'), \
                     mock.patch.object(login.settings, 'max_retry_attemps', 2), \
                     mock.patch.object(login.settings, 'retry_interval', 0), \
                     mock.patch.object(login, 'WebDriverWait'), \
                     mock.patch.object(login, '_set_input_value'), \
                     mock.patch.object(login, 'wait_for_logged_in', return_value=False) as auth, \
                     mock.patch.object(login, 'sleep'):
                    self.assertFalse(login.login_mysafety(driver))
                self.assertEqual(driver.get.call_count, 3 if throws else 6)
                self.assertEqual(auth.call_count, 0 if throws else 3)
