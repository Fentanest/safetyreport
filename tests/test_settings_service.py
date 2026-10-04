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
        svc.apply(svc.web_command(dict(FORM, telegram_token="keep-me")))
        svc.apply(svc.api_command({"auto_export_excel": True}))
        self.assertEqual(self._read("SETTINGS", "auto_export_excel"), "True")
        self.assertEqual(self._read("TELEGRAM", "telegram_token"), "keep-me")
        self.assertEqual(self._read("GOOGLESHEET", "sheet_key"), "AbC-12_x")

    def test_login_change_invalidates_the_previous_token_only_when_it_changes(self):
        svc.apply(svc.web_command(dict(FORM, username="first")))
        self.invalidate.reset_mock()
        result = svc.apply(svc.web_command(dict(FORM, username="first")))
        self.assertFalse(result.login_changed)
        self.invalidate.assert_not_called()
        result = svc.apply(svc.web_command(dict(FORM, username="second")))
        self.assertTrue(result.login_changed)
        self.invalidate.assert_called_once()
        self.assertNotEqual(self._read("LOGIN", "password"), "pw1", "비밀번호는 암호화해 저장한다")

    def test_job_refresh_failure_keeps_the_saved_settings(self):
        self.update_jobs.side_effect = RuntimeError("scheduler down")
        result = svc.apply(svc.web_command(dict(FORM, chat_id="42")))
        self.assertEqual(result.jobs_error, "RuntimeError")
        self.assertEqual(self._read("TELEGRAM", "chat_id"), "42")
        self.update_jobs.side_effect = None
        self.assertIsNone(svc.apply(svc.api_command({})).jobs_error)

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
