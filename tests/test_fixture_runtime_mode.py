"""fixture 런타임 seam 계약 테스트.

SAFETYREPORT_FIXTURE_MODE=1 이면 크롤러/별점/외부 HTTP 가 실제로 호출되지 않고,
환경변수가 없으면 기존 동작 경로를 그대로 탄다는 것을 확인한다.
"""
import os
import unittest
from unittest import mock

from core.utils import logger, runtime_mode
from core.utils.runtime_mode import ExternalSideEffectBlocked, FIXTURE_ENV, DATA_DIR_ENV

FIXTURE_ON = {FIXTURE_ENV: "1"}


class RuntimeModeFlagTest(unittest.TestCase):
    def test_flags_default_off(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(FIXTURE_ENV, None)
            os.environ.pop(DATA_DIR_ENV, None)
            self.assertFalse(runtime_mode.is_fixture_mode())
            self.assertIsNone(runtime_mode.data_dir_override())
            runtime_mode.block_if_fixture("noop")  # 예외 없어야 함
            self.assertFalse(runtime_mode.skip_in_fixture("noop"))

    def test_data_dir_override_is_absolute(self):
        with mock.patch.dict(os.environ, {DATA_DIR_ENV: "relative/fixture"}):
            self.assertTrue(os.path.isabs(runtime_mode.data_dir_override()))

    def test_fixture_mode_without_data_dir_is_refused(self):
        import settings.settings as app_settings
        env = {FIXTURE_ENV: "1"}
        with mock.patch.dict(os.environ, env):
            os.environ.pop(DATA_DIR_ENV, None)
            with self.assertRaises(RuntimeError):
                app_settings.AppSettings()

    def test_blocked_error_is_runtime_error_for_existing_handlers(self):
        with mock.patch.dict(os.environ, FIXTURE_ON):
            with self.assertRaises(RuntimeError):
                runtime_mode.block_if_fixture("x")


class FixtureBlocksSideEffectsTest(unittest.TestCase):
    def setUp(self):
        logger.LoggerFactory.create_logger(mode="crawl")
        runtime_mode.reset_blocked_actions()
        patcher = mock.patch.dict(os.environ, FIXTURE_ON)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_crawl_subprocess_not_started(self):
        from services import crawl_manager as crawl_manager_module
        manager = crawl_manager_module.crawl_manager
        with mock.patch.object(crawl_manager_module.subprocess, "Popen") as popen:
            with self.assertRaises(ExternalSideEffectBlocked):
                manager.start_crawl(["python", "start.py"], cwd=".", log_file="unused.log")
            popen.assert_not_called()
        self.assertIn("crawl subprocess", runtime_mode.blocked_actions())

    def test_star_rating_thread_not_started(self):
        from services import rating_service
        with mock.patch.object(rating_service, "resolve_rating_targets", return_value=["SPP-TEST-1"]), \
                mock.patch.object(rating_service.threading, "Thread") as thread:
            with self.assertRaises(ExternalSideEffectBlocked):
                rating_service.start_batch_rating(None, ["SPP-TEST-1"], score=5)
            thread.assert_not_called()

    def test_updater_network_blocked(self):
        from core.utils import updater
        with mock.patch("urllib.request.build_opener") as build_opener:
            with self.assertRaises(ExternalSideEffectBlocked):
                updater._urlopen("https://api.github.com/repos/x/y/releases/latest")
            build_opener.assert_not_called()

    def test_direct_login_session_blocked(self):
        from core.crawler import direct_login
        with self.assertRaises(ExternalSideEffectBlocked):
            direct_login._make_session()
        self.assertFalse(direct_login.start_keepalive(interval_seconds=3600))

    def test_media_upstream_download_blocked(self):
        from services import media_proxy_service
        with mock.patch.object(media_proxy_service._session, "get") as upstream_get:
            with self.assertRaises(ExternalSideEffectBlocked):
                media_proxy_service.ensure_cached("https://www.safetyreport.go.kr/fileDown/singo/test.mp4")
            upstream_get.assert_not_called()

    def test_sunwi_fetch_and_background_blocked(self):
        from services import sunwi_fetcher, sunwi_service
        with self.assertRaises(ExternalSideEffectBlocked):
            sunwi_fetcher.build_session()
        with mock.patch.object(sunwi_service.threading, "Thread") as thread:
            sunwi_service.start_background_refresh()
            thread.assert_not_called()

    def test_geocode_request_blocked(self):
        import tempfile
        from sqlalchemy import create_engine
        from core.database import database
        from services import geocode_service
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        engine = create_engine(f"sqlite:///{db_path}")
        self.addCleanup(os.remove, db_path)
        self.addCleanup(engine.dispose)
        database.upgrade_schema(engine)
        with mock.patch.object(geocode_service, "get_kakao_rest_api_key", return_value="test-key"), \
                mock.patch.object(geocode_service.requests, "get") as kakao_get:
            with self.assertRaises(ExternalSideEffectBlocked):
                geocode_service.resolve_address(engine, "서울특별시 강서구 마곡동 1")
            kakao_get.assert_not_called()

    def test_settings_disable_telegram_and_sheets(self):
        import settings.settings as app_settings
        instance = app_settings._instance
        original = (instance.telegram_token, instance.chat_id)
        try:
            instance.config.read_dict({"TELEGRAM": {"telegram_token": "fixture-token", "chat_id": "123"}})
            instance.load()
            self.assertFalse(instance.telegram_enabled)
            self.assertFalse(instance.google_sheet_enabled)
        finally:
            instance.config.remove_section("TELEGRAM")
            instance.load()
            self.assertEqual((instance.telegram_token, instance.chat_id), (None, None) if original == (None, None) else original)


if __name__ == "__main__":
    unittest.main()
