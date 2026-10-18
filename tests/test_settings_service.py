"""EO R-13: 설정 명령(웹 전체·API 부분)과 저장 뒤 후속 작업(services/settings_service)."""

import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("SAFETYREPORT_DATA_DIR", tempfile.mkdtemp(prefix="sr-r13-"))

import settings.settings as app_settings
from services import settings_service as svc

FORM = {"username": "user1", "password": "pw1", "telegram_token": "", "chat_id": "",
        "sheet_key": "https://docs.google.com/spreadsheets/d/AbC-12_x/edit#gid=0",
        "exclude_withdraw": True, "use_representative_records": True, "auto_export_excel": False,
        "auto_export_sheet": False, "retry_interval": 10, "max_retry_attemps": 5, "log_level": "INFO",
        "chrome_mode": "hub", "remote_debug_port": "9222", "headless": False, "scheduler_enabled": False,
        "scheduler_mode": "interval", "scheduler_interval_hours": 24, "scheduler_cron_times": "09:00",
        "scheduler_interval_start": "00:00", "phone_number": "010-1234-5678",
        "remotepath": "http://localhost:4444/wd/hub", "session_max_age": 10800, "trusted_proxies": "  10.0.0.1 "}


class CommandTest(unittest.TestCase):
    def test_web_command_normalizes_and_covers_every_form_field(self):
        command = svc.web_command(FORM)
        values = {(section, key): value for section, key, value in command.values}
        self.assertEqual(values[("GOOGLESHEET", "sheet_key")], "AbC-12_x")
        self.assertEqual(values[("RATING", "phone_number")], "01012345678")
        self.assertEqual(values[("SETTINGS", "trusted_proxies")], "10.0.0.1")
        self.assertEqual(len(values), len(FORM))
        self.assertTrue(command.refresh_jobs)

    def test_api_command_takes_only_mobile_booleans(self):
        command = svc.api_command({"exclude_withdraw": False, "normalize_police": True, "auto_export_sheet": True})
        self.assertEqual(command.values, (("SETTINGS", "exclude_withdraw", False), ("SETTINGS", "auto_export_sheet", True)))
        self.assertFalse(command.refresh_jobs)
        with self.assertRaises(svc.SettingsInvalid) as ctx:
            svc.api_command({"use_representative_records": "yes"})
        self.assertEqual(ctx.exception.key, "use_representative_records")


class ApplyTest(unittest.TestCase):
    def setUp(self):
        from core.utils import logger
        logger.LoggerFactory.create_logger(mode="crawl")
        app_settings._instance.load()
        self.addCleanup(app_settings._instance.load)
        invalidate = mock.patch("core.crawler.direct_login.invalidate_token")
        self.invalidate = invalidate.start()
        self.addCleanup(invalidate.stop)
        jobs = mock.patch("core.utils.scheduler.update_jobs")
        self.update_jobs = jobs.start()
        self.addCleanup(jobs.stop)

    def _read(self, section, key):
        app_settings._instance.load()
        return app_settings.config.get(section, key, fallback=None)

    def test_api_partial_save_keeps_every_other_value(self):
        svc._persist(svc.web_command(dict(FORM, telegram_token="keep-me")))
        svc._persist(svc.api_command({"auto_export_excel": True}))
        self.assertEqual(self._read("SETTINGS", "auto_export_excel"), "True")
        self.assertEqual(self._read("TELEGRAM", "telegram_token"), "keep-me")
        self.assertEqual(self._read("GOOGLESHEET", "sheet_key"), "AbC-12_x")

    def test_login_change_invalidates_the_previous_token_only_when_it_changes(self):
        svc._persist(svc.web_command(dict(FORM, username="first")))
        self.invalidate.reset_mock()
        result = svc._persist(svc.web_command(dict(FORM, username="first")))
        self.assertFalse(result.login_changed)
        self.invalidate.assert_not_called()
        result = svc._persist(svc.web_command(dict(FORM, username="second")))
        self.assertTrue(result.login_changed)
        self.invalidate.assert_called_once()
        self.assertNotEqual(self._read("LOGIN", "password"), "pw1", "비밀번호는 암호화해 저장한다")

    def test_job_refresh_failure_keeps_the_saved_settings(self):
        self.update_jobs.side_effect = RuntimeError("scheduler down")
        result = svc._persist(svc.web_command(dict(FORM, chat_id="42")))
        self.assertEqual(result.jobs_error, "RuntimeError")
        self.assertEqual(self._read("TELEGRAM", "chat_id"), "42")
        self.update_jobs.side_effect = None
        self.assertIsNone(svc._persist(svc.api_command({})).jobs_error)

    def test_proxy_save_applies_to_next_request_without_recreating_app(self):
        from fastapi import FastAPI, Request
        from starlette.testclient import TestClient
        from core.utils.proxy_headers import (TrustedProxyHeadersMiddleware, set_trusted_proxies,
                                               get_trusted_proxy_config)
        from web.routers import settings_route
        old_config = get_trusted_proxy_config()
        import core.utils.proxy_headers as proxy_headers
        self.addCleanup(setattr, proxy_headers, "_current_config", old_config)
        app = FastAPI()
        app.include_router(settings_route.router)
        @app.get("/probe")
        def probe(request: Request):
            return {"scheme": request.url.scheme, "host": request.headers["host"],
                    "client": request.client.host}
        app.add_middleware(TrustedProxyHeadersMiddleware)
        set_trusted_proxies('none')
        client = TestClient(app, client=('172.19.0.1', 1), base_url='http://internal.test')
        headers = {'x-forwarded-proto': 'https', 'x-forwarded-host': 'public.test',
                   'x-forwarded-for': '203.0.113.7'}
        original = {'scheme': 'http', 'host': 'internal.test', 'client': '172.19.0.1'}
        restored = {'scheme': 'https', 'host': 'public.test', 'client': '203.0.113.7'}
        self.assertEqual(client.get('/probe', headers=headers).json(), original)
        # Only account-binding side effects are excluded; route validation and disk save are real.
        with mock.patch.object(svc, 'apply', side_effect=svc._persist):
            for value, expected in (('auto', restored), ('none', original), ('', restored)):
                response = client.post('/settings/save', data=dict(FORM, trusted_proxies=value),
                                       headers=headers, follow_redirects=False)
                self.assertEqual((response.status_code, response.headers['location']),
                                 (303, '/settings/?saved=true'))
                self.assertEqual(self._read('SETTINGS', 'trusted_proxies'), value)
                self.assertEqual(client.get('/probe', headers=headers).json(), expected)
        svc._persist(svc.api_command({'auto_export_excel': False}))
        self.assertEqual(client.get('/probe', headers=headers).json(), restored)

    def test_failed_proxy_save_keeps_current_trust_config(self):
        from core.utils.proxy_headers import set_trusted_proxies, get_trusted_proxy_config
        set_trusted_proxies('none')
        with mock.patch.object(app_settings._instance, 'save', side_effect=OSError('fixture save failure')):
            with self.assertRaises(OSError):
                svc._persist(svc.web_command(dict(FORM, trusted_proxies='auto')))
        self.assertEqual(get_trusted_proxy_config().networks, ())
        # The mocked failing save did not consume thread-local pending values.
        app_settings._instance._draft.pending = {}

    def test_google_credential_validation(self):
        for body in (b"broken", b"[]", b"{}", b"\xff"):
            with self.assertRaises(svc.CredentialInvalid):
                svc.validate_google_credential(body)
        with self.assertRaises(svc.CredentialTooLarge):
            svc.validate_google_credential(b"x" * (svc.GOOGLE_CREDENTIAL_MAX_BYTES + 1))
        svc.validate_google_credential(b'{"type": "service_account", "client_email": "a@b", "private_key": "k",'
                                       b' "token_uri": "https://t"}')


if __name__ == "__main__":
    unittest.main()
