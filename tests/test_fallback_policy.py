"""EO R-17: 실패 대체 지점의 진단 정책.

- 조회·판정 실패를 기본값으로 바꾸는 곳은 note_fallback 으로 동작명·예외 종류를 남긴다(값은 그대로).
- 기록하지 않는 대체는 아래 SILENT_ALLOWED 에 분류와 함께 있어야 한다. 새 지점은 둘 중 하나를 고른다.
- 요청 경계 예외는 경로·종류·프레임만 남기고 메시지(비밀이 섞일 수 있음)는 남기지 않는다.
"""

import ast
import logging
import os
import pathlib
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("SAFETYREPORT_DATA_DIR", tempfile.mkdtemp(prefix="sr-r17-"))

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCAN_ROOTS = ("main.py", "start.py", "core", "services", "web", "settings")

# 분류: cleanup(정리·종료·알림 — 결과 의미가 바뀌지 않음) / parse(형식 판정 — 실패는 '해당 형식 아님')
#       retry(다음 실행에서 다시 시도) / optional(부가 정보 보강) / explicit(오류를 호출자에게 값으로 돌려줌)
#       logging(진단 경로 자체)
SILENT_ALLOWED = {
    "core/crawler/api_client.py::get_authorized_json": ("explicit", 3),
    "core/crawler/crawltitle_api.py::crawl_titles": ("cleanup", 1),
    "core/crawler/direct_login.py::load_token": ("retry", 1),
    "core/crawler/driv.py::create_driver": ("cleanup", 1),
    "core/crawler/login.py::_has_element": ("parse", 1),
    "core/crawler/login.py::is_logged_in": ("parse", 1),
    "core/crawler/login.py::wait_for_logged_in": ("retry", 1),
    "core/database/database.py::upgrade_schema": ("optional", 1),
    "core/utils/csrf.py::_trusted_proxy_configured": ("parse", 1),
    "core/utils/fallback.py::_log": ("logging", 1),
    "core/utils/fallback.py::log_request_exception": ("logging", 1),
    "core/utils/fallback.py::note_fallback": ("logging", 1),
    "core/utils/retry.py::get_configured_attempts": ("parse", 1),
    "core/utils/retry.py::get_retry_interval": ("parse", 1),
    "core/utils/updater.py::_build_ssl_context": ("optional", 1),
    "core/utils/updater.py::_fetch_latest_release": ("explicit", 1),
    "core/utils/updater.py::_version_gt": ("parse", 1),
    "core/utils/updater.py::get_current_version": ("parse", 1),
    "main.py::lifespan": ("cleanup", 4),
    "services/community_auth_client.py::jwt_claims_unverified": ("parse", 1),
    "services/community_auth_service.py::CommunityAuthService._fail_pending": ("cleanup", 1),
    "services/community_capture.py::mark_personal_save": ("cleanup", 1),
    "services/community_rebuild.py::_commit": ("cleanup", 1),
    "services/community_rebuild.py::_login_id": ("parse", 1),
    "services/community_schedule.py::register_community_jobs": ("cleanup", 1),
    "services/community_uploader.py::_run_upload": ("cleanup", 1),
    "services/community_uploader.py::_start_background_locked.loop": ("cleanup", 1),
    "services/community_upload_status.py::refresh_server_completed": ("cleanup", 1),
    "services/crawl_log_service.py::rotate_crawl_log": ("cleanup", 1),
    "services/crawl_manager.py::CrawlManager._publish_unresolved": ("cleanup", 1),
    "services/crawl_manager.py::CrawlManager.launch_pending_crawl._after": ("cleanup", 1),
    "services/crawl_manager.py::CrawlManager.run_after_crawl": ("cleanup", 3),
    "services/crawl_state_store.py::_take_json": ("cleanup", 1),
    "services/crawl_state_store.py::clear_crawl_changes": ("cleanup", 1),
    "services/file_service.py::delete_all_in_target": ("cleanup", 1),
    "services/media_proxy_service.py::cleanup_cache": ("cleanup", 1),
    "services/parser.py::parse_json_details": ("parse", 1),
    "services/photo_capture_time.py::collect": ("retry", 1),
    "services/satisfaction_fetcher.py::fetch_score_via_selenium_page": ("optional", 1),
    "services/star_rating_service.py::_site_result": ("optional", 1),
    "services/star_rating_service.py::run_batch_rating": ("cleanup", 1),
    "services/sunwi_fetcher.py::extract_count": ("parse", 1),
    "services/ws_manager.py::WsManager._close": ("cleanup", 1),
    "start.py::_rebuild_list_labels": ("parse", 1),
    "start.py::_rebuild_register_list": ("parse", 1),
    "web/routers/api_route.py::download_database._cleanup": ("cleanup", 1),
    "web/routers/backup_route.py::_safe_unlink": ("cleanup", 1),
    "web/routers/crawl.py::start_crawl": ("explicit", 1),
}


def _silent(handler: ast.ExceptHandler) -> bool:
    kind = handler.type
    broad = kind is None or (isinstance(kind, ast.Name) and kind.id in ("Exception", "BaseException"))
    return broad and len(handler.body) == 1 and isinstance(handler.body[0], (ast.Pass, ast.Continue, ast.Return))


def _scan() -> dict:
    found: dict = {}
    for root in SCAN_ROOTS:
        base = ROOT / root
        paths = [base] if base.is_file() else sorted(base.rglob("*.py"))
        for path in paths:
            rel = path.relative_to(ROOT).as_posix()

            def visit(node, qual):
                for child in ast.iter_child_nodes(node):
                    name = qual
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        name = f"{qual}.{child.name}" if qual else child.name
                    if isinstance(child, ast.ExceptHandler) and _silent(child):
                        key = f"{rel}::{qual or '<module>'}"
                        found[key] = found.get(key, 0) + 1
                    visit(child, name)

            visit(ast.parse(path.read_text(encoding="utf-8")), "")
    return found


class SilentFallbackInventoryTest(unittest.TestCase):
    def test_every_silent_fallback_is_classified(self):
        found = _scan()
        unlisted = {k: v for k, v in found.items() if k not in SILENT_ALLOWED}
        self.assertEqual(unlisted, {}, "기록 없이 대체하는 새 지점: note_fallback 을 쓰거나 SILENT_ALLOWED 에 분류해 넣는다")
        grown = {k: (v, SILENT_ALLOWED[k][1]) for k, v in found.items() if v > SILENT_ALLOWED[k][1]}
        self.assertEqual(grown, {}, "분류된 함수 안에 조용한 대체가 늘었다")

    def test_allowlist_has_no_stale_entries(self):
        found = _scan()
        stale = sorted(k for k in SILENT_ALLOWED if k not in found)
        self.assertEqual(stale, [], "없어진 지점은 목록에서 지운다")


class NoteFallbackTest(unittest.TestCase):
    def setUp(self):
        from core.utils import fallback
        fallback.reset_for_tests()
        self.fallback = fallback

    def test_records_action_and_type_only(self):
        with self.assertLogs("safetyreport.core", level="WARNING") as captured:
            with mock.patch("core.utils.logger.LoggerFactory.logbot", None):
                self.fallback.note_fallback("unit.lookup", ValueError("cookie=SECRET-123"))
        text = "\n".join(captured.output)
        self.assertIn("unit.lookup", text)
        self.assertIn("ValueError", text)
        self.assertNotIn("SECRET-123", text)

    def test_repeats_are_rate_limited_per_action_and_type(self):
        log = logging.getLogger("sr-r17-rate")
        with mock.patch.object(self.fallback, "_log", return_value=log):
            with self.assertLogs(log, level="WARNING") as captured:
                for _ in range(5):
                    self.fallback.note_fallback("unit.poll", OSError("x"))
                self.fallback.note_fallback("unit.poll", KeyError("x"))
        self.assertEqual(len(captured.output), 2)

    def test_request_exception_keeps_frames_without_message(self):
        log = logging.getLogger("sr-r17-req")
        secret = "SECRET-" + "456"
        try:
            raise RuntimeError(f"token={secret}")
        except RuntimeError as exc:
            error = exc
        with mock.patch.object(self.fallback, "_log", return_value=log):
            with self.assertLogs(log, level="ERROR") as captured:
                self.fallback.log_request_exception("/api/v1/x", error)
        text = "\n".join(captured.output)
        self.assertIn("/api/v1/x", text)
        self.assertIn("RuntimeError", text)
        self.assertIn("test_request_exception_keeps_frames_without_message", text)
        self.assertNotIn("SECRET-456", text)


class FallbackSitesKeepValuesTest(unittest.TestCase):
    """대체값은 그대로 두고 기록만 늘었는지 대표 지점에서 확인한다."""

    def setUp(self):
        from core.utils import fallback
        fallback.reset_for_tests()

    def _assert_logged(self, action, call, expected):
        from core.utils import fallback
        log = logging.getLogger("sr-r17-site")
        with mock.patch.object(fallback, "_log", return_value=log):
            with self.assertLogs(log, level="WARNING") as captured:
                value = call()
        self.assertEqual(value, expected)
        self.assertTrue(any(action in line for line in captured.output), captured.output)

    def test_community_label_lookups(self):
        from services import collection_policy
        with mock.patch("services.community_store.CommunityStore.open", side_effect=OSError("db")):
            self._assert_logged("collection_policy.community_detail_status_labels",
                                collection_policy._community_detail_status_labels, {})
            self._assert_logged("collection_policy.community_rebuild_permanent_labels",
                                collection_policy._community_rebuild_permanent_labels, {})

    def test_decrypt_failure_still_returns_empty(self):
        from core.utils import security
        datapath = tempfile.mkdtemp(prefix="sr-r17-key-")
        os.makedirs(os.path.join(datapath, "auth"))
        open(os.path.join(datapath, "auth", ".config_key"), "wb").close()
        with mock.patch.object(security, "_get_or_create_config_key", side_effect=ValueError("bad key")):
            self._assert_logged("security.decrypt_config_value",
                                lambda: security.decrypt_config_value("enc:garbage", datapath), "")

    def test_crawl_state_corrupt_file_returns_default(self):
        from services import crawl_state_store
        path = os.path.join(tempfile.mkdtemp(prefix="sr-r17-state-"), "state.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self._assert_logged("crawl_state.read:state.json", lambda: crawl_state_store._read_json(path, {"d": 1}), {"d": 1})
        self.assertTrue(os.path.exists(path))


class RequestBoundaryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import main
        from fastapi.testclient import TestClient
        cls.main = main
        cls.TestClient = TestClient

    def test_public_path_exception_returns_500_and_is_logged(self):
        from core.utils import fallback
        app = self.main.app
        path = "/static/__r17_boom"  # 공개 경로(정적 파일 앞에 끼워 넣는다)
        secret = "SECRET-" + "789"

        async def boom():
            raise RuntimeError(f"apikey={secret}")

        app.add_api_route(path, boom, methods=["GET"])
        app.router.routes.insert(0, app.router.routes.pop())
        try:
            log = logging.getLogger("sr-r17-boundary")
            with mock.patch.object(fallback, "_log", return_value=log):
                with self.assertLogs(log, level="ERROR") as captured:
                    response = self.TestClient(app, raise_server_exceptions=False).get(path)
        finally:
            app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", None) != path]
        self.assertEqual(response.status_code, 500)
        text = "\n".join(captured.output)
        self.assertIn(path, text)
        self.assertIn("RuntimeError", text)
        self.assertNotIn("SECRET-789", text)


if __name__ == "__main__":
    unittest.main()
