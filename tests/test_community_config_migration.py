"""폐기 커뮤니티 설정 마이그레이션: 합성 ini/번들/세션만 사용한다."""
import base64
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from services import community_auth_config as config


OLD_URL = "https://nxdcxccixoswvqgjeprh.supabase.co"
OLD_KEY = "sb_publishable_oldfixture12345"
BUNDLE = {"supabase_url": "https://supabase.worklazy.net",
          "publishable_key": "sb_publishable_newfixture12345"}
RAW = {"supabase_url": OLD_URL, "publishable_key": OLD_KEY,
       "site_url": "https://safeauth.worklazy.net/", "device_label": "우리 PC"}


class ConfigSelectionTests(unittest.TestCase):
    def test_retired_ini_uses_bundled_pair_without_mutating_inputs(self):
        raw = dict(RAW)
        cfg = config.build_config(raw, env={}, bundled=BUNDLE)
        self.assertTrue(cfg.configured)
        self.assertEqual((cfg.supabase_url, cfg.publishable_key), tuple(BUNDLE.values()))
        self.assertEqual(cfg.site_url, RAW["site_url"])
        self.assertEqual(cfg.device_label, RAW["device_label"])
        self.assertFalse(cfg.env_locked)
        self.assertEqual(raw, RAW)

    def test_current_and_custom_hosts_preserved(self):
        for url in (BUNDLE["supabase_url"], "https://personal.internal.net",
                    OLD_URL + ".attacker.net"):
            with self.subTest(url=url):
                cfg = config.build_config(dict(RAW, supabase_url=url), env={}, bundled=BUNDLE)
                self.assertEqual((cfg.supabase_url, cfg.publishable_key), (url, OLD_KEY))

    def test_retired_environment_aliases_use_bundle_not_ini_and_unlock(self):
        for url_name in (config.ENV_SUPABASE_URL, *config.ENV_ALIASES[config.ENV_SUPABASE_URL]):
            for key_name in (config.ENV_PUBLISHABLE_KEY, *config.ENV_ALIASES[config.ENV_PUBLISHABLE_KEY]):
                with self.subTest(url_name=url_name, key_name=key_name):
                    env = {url_name: OLD_URL.upper() + "/", key_name: OLD_KEY,
                           config.ENV_SITE_URL: "https://custom-safeauth.internal.net/"}
                    cfg = config.build_config(dict(RAW, supabase_url="https://personal.internal.net"),
                                              env=env, bundled=BUNDLE)
                    self.assertTrue(cfg.configured)
                    self.assertEqual((cfg.supabase_url, cfg.publishable_key), tuple(BUNDLE.values()))
                    self.assertEqual(cfg.env_locked, frozenset({"site_url"}))
                    self.assertEqual(cfg.site_url, env[config.ENV_SITE_URL])
                    self.assertEqual(env[url_name], OLD_URL.upper() + "/")

    def test_retired_environment_key_alias_conflict_is_discarded_with_pair(self):
        cfg = config.build_config(RAW, env={config.ENV_SUPABASE_URL: OLD_URL,
                                           config.ENV_PUBLISHABLE_KEY: OLD_KEY,
                                           "COMMUNITY_PUBLISHABLE_KEY": "sb_publishable_stalefixture12345"},
                                  bundled=BUNDLE)
        self.assertTrue(cfg.configured)
        self.assertFalse(cfg.env_locked)

    def test_different_environment_hosts_still_conflict(self):
        cfg = config.build_config(RAW, env={config.ENV_SUPABASE_URL: OLD_URL,
                                           "COMMUNITY_SUPABASE_URL": BUNDLE["supabase_url"]}, bundled=BUNDLE)
        self.assertIn("config_conflict", cfg.problems)

    def test_valid_environment_wins_over_retired_ini(self):
        cfg = config.build_config(RAW, env={config.ENV_SUPABASE_URL: "https://personal.internal.net",
                                           config.ENV_PUBLISHABLE_KEY: OLD_KEY}, bundled=BUNDLE)
        self.assertTrue(cfg.configured)
        self.assertEqual((cfg.supabase_url, cfg.publishable_key), ("https://personal.internal.net", OLD_KEY))
        self.assertEqual(cfg.env_locked, frozenset({"supabase_url", "publishable_key"}))

    def test_no_usable_bundle_preserves_old_values_and_reports_problem(self):
        for bundle in ({}, {"supabase_url": BUNDLE["supabase_url"]},
                       dict(BUNDLE, publishable_key="sb_secret_fixture"),
                       dict(BUNDLE, supabase_url=OLD_URL)):
            for env in ({}, {config.ENV_SUPABASE_URL: OLD_URL, config.ENV_PUBLISHABLE_KEY: OLD_KEY}):
                with self.subTest(bundle=bundle, env=env):
                    cfg = config.build_config(RAW, env=env, bundled=bundle)
                    self.assertEqual((cfg.supabase_url, cfg.publishable_key), (OLD_URL, OLD_KEY))
                    self.assertIn("retired_supabase_url", cfg.problems)
                    self.assertFalse(cfg.configured)


class ConfigFileMigrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "config.ini"
        self.removed = [f"Supabase_URL : {OLD_URL}\r\n", "publishable_KEY =\r\n",
                        f"    {OLD_KEY}\r\n"]
        self.original = ("# 설정 주석\r\n[OTHER]\r\nsupabase_url = keep\r\n"
                         "publishable_key = keep-too\r\n\r\n[COMMUNITY]\r\n"
                         + self.removed[0] + "; URL 주석\r\n" + self.removed[1]
                         + "    # 공개 키 주석\r\n" + self.removed[2]
                         + "site_url = https://safeauth.worklazy.net/\r\n"
                         "device_label = 우리 PC\r\n\r\n[LAST]\r\nvalue: unchanged\r\n").encode()
        self.path.write_bytes(self.original)

    def migrate(self, bundled=None):
        return config.cleanup_retired_config(str(self.path), BUNDLE if bundled is None else bundled)

    def backups(self):
        return list(self.path.parent.glob("config.ini.bak-*"))

    def test_cleanup_preserves_all_other_bytes_and_backs_up_once(self):
        with self.assertLogs("safetyreport.core.community_auth", level="WARNING") as logs:
            self.assertTrue(self.migrate())
        expected = self.original
        for line in self.removed:
            expected = expected.replace(line.encode(), b"")
        self.assertEqual(self.path.read_bytes(), expected)
        self.assertEqual(len(self.backups()), 1)
        backup = self.backups()[0]
        self.assertEqual(backup.read_bytes(), self.original)
        if os.name != "nt":
            self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        self.assertEqual(len(logs.output), 1)
        self.assertIn("이전 커뮤니티 서버 주소 설정을 정리했습니다(백업: ", logs.output[0])
        self.assertNotIn(OLD_KEY, logs.output[0])
        self.assertFalse(self.migrate())
        self.assertEqual(len(self.backups()), 1)

    def test_current_and_custom_files_unchanged(self):
        for url in (BUNDLE["supabase_url"], "https://personal.internal.net"):
            with self.subTest(url=url):
                payload = self.original.replace(OLD_URL.encode(), url.encode())
                self.path.write_bytes(payload)
                self.assertFalse(self.migrate())
                self.assertEqual(self.path.read_bytes(), payload)
                self.assertFalse(self.backups())

    def test_no_bundle_keeps_file_without_backup(self):
        self.assertFalse(self.migrate({}))
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertFalse(self.backups())

    def test_backup_failure_keeps_original_and_runtime_falls_back(self):
        with mock.patch("core.utils.atomic_file.write_bytes", side_effect=PermissionError):
            self.assertFalse(self.migrate())
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertFalse(self.backups())
        self.assertEqual(config.build_config(RAW, env={}, bundled=BUNDLE).supabase_url, BUNDLE["supabase_url"])

    def test_replace_failure_keeps_original_and_backup(self):
        from core.utils.atomic_file import write_bytes

        def fail_config(path, payload):
            if path == str(self.path):
                raise PermissionError
            return write_bytes(path, payload)

        with mock.patch("core.utils.atomic_file.write_bytes", side_effect=fail_config):
            self.assertFalse(self.migrate())
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(self.backups()[0].read_bytes(), self.original)

    def test_read_only_lock_failure_still_loads_bundled_settings(self):
        from settings import settings

        with mock.patch.dict(os.environ, {"SAFETYREPORT_DATA_DIR": self.directory.name}, clear=True), \
             mock.patch.object(config, "load_bundled_public", return_value=BUNDLE), \
             mock.patch("core.utils.file_lock.exclusive", side_effect=PermissionError):
            app = settings.AppSettings()
            self.assertEqual(app.config.get("COMMUNITY", "supabase_url"), OLD_URL)
            with mock.patch.object(settings, "_instance", app):
                cfg = config.load_config_from_settings()
                self.assertTrue(cfg.configured)
                self.assertEqual((cfg.supabase_url, cfg.publishable_key), tuple(BUNDLE.values()))
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertFalse(self.backups())

    def test_startup_migration_then_foreign_session_requires_login(self):
        from settings import settings
        from services.community_auth_service import CommunityAuthService, CommunityAuthError
        from services.community_gate import _Gate

        with mock.patch.dict(os.environ, {"SAFETYREPORT_DATA_DIR": self.directory.name}, clear=True), \
             mock.patch.object(config, "load_bundled_public", return_value=BUNDLE):
            app = settings.AppSettings()
            self.assertFalse(app.config.has_option("COMMUNITY", "supabase_url"))
            self.assertFalse(app.config.has_option("COMMUNITY", "publishable_key"))
            with mock.patch.object(settings, "_instance", app):
                service = CommunityAuthService(self.directory.name, client_factory=mock.Mock())
                claims = base64.urlsafe_b64encode(json.dumps({"iss": OLD_URL + "/auth/v1"}).encode()).rstrip(b"=").decode()
                st = service.store.load()
                st["current"] = {"access_token": "e30." + claims + ".c2ln",
                                 "refresh_token": "fixture-refresh", "user_id": "fixture-user",
                                 "kakao_id": "910001", "display_name": "우리 계정", "has_email": False,
                                 "connected_at": "2026-10-01T00:00:00Z"}
                service.store.save(st)
                self.assertEqual(_Gate._session_state(service), ("reauth_required", None))
                dto = service.status(can_manage=True)
                self.assertEqual(dto["state"], "reauth_required")
                self.assertEqual(dto["account"]["display_name"], "우리 계정")
                self.assertEqual(dto["last_error"]["code"], "reauth_required")
                with self.assertRaises(CommunityAuthError) as raised:
                    service.get_access_token()
                self.assertEqual(raised.exception.code, "reauth_required")
                self.assertIsNone(service.store.load()["current"])
                self.assertEqual(service.store.load()["reauth"]["kakao_id"], "910001")
                service._client_factory.assert_not_called()
                self.assertFalse(service.retire_foreign_session())
            app.load()
            self.assertEqual(len(self.backups()), 1)


if __name__ == "__main__":
    unittest.main()
