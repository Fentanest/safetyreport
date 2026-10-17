"""원본 보기 화면·API·파일 탐색기·다시 쓰기 (2026-10-17 명세 4절).

웹 읽기 라우트 인증(비로그인 거부)·없음·손상·정상 JSON, API 키 인증·404·409·
ID 이탈 거부·응답 필드, 파일 탐색기 허용·이탈 거부·삭제 금지, 다시 쓰기.
실데이터·네트워크 금지. 합성 원본에는 신고자 키 5개를 넣어 제거를 확인한다.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from starlette.middleware.sessions import SessionMiddleware

from core.database import models
from core.utils import logger, raw_detail
from scripts.dev import fixture_server
from services import file_service
from web.routers import api_route, file_browser_route, raw_detail_route


def _envelope(seed=""):
    return {
        "resultCode": "0000",
        "result": {
            "C_NO": "90000001",
            "C_NAME": "홍길동" + seed,
            "C_EMAIL": "a@b.c",
            "C_PHONE": "01012345678",
            "C_PHONE2": "010-1234-5678",
            "C_ID": "fixtureid1",
            "C_NOW": 10,
            "VHRNO": "12가3456",
            "C_A_CONTENTS": "<script>alert('xss')</script>불법주정차 신고합니다." + seed,
            "answers": [{"C_MANAGE_MAN": "김담당", "C_MANAGE_MAN_PHONE": "02-123-4567"}],
        },
    }


def _insert_raw(engine, report_id, envelope, **overrides):
    canonical = raw_detail.canonical_bytes(envelope)
    row = {"ID": report_id, "raw_zlib": raw_detail.compress(canonical),
           "raw_sha256": raw_detail.sha256_hex(canonical), "raw_size": len(canonical),
           "fetched_at": "2026-10-17T12:00:00.000Z", "source": "crawl"}
    row.update(overrides)
    with engine.begin() as conn:
        conn.execute(models.report_detail_raw_table.insert().values(**row))
    return canonical


class _IsolatedEngine(unittest.TestCase):
    def _make_engine(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        engine = create_engine(f"sqlite:///{Path(directory.name) / 'data.db'}")
        self.addCleanup(engine.dispose)
        fixture_server.seed_engine(engine)
        return engine


class RawDetailServiceTests(_IsolatedEngine):
    def setUp(self):
        logger.LoggerFactory.create_logger(mode="crawl")
        self.engine = self._make_engine()

    def test_read_returns_stored_envelope_without_reporter_keys(self):
        from services import raw_detail_service

        canonical = _insert_raw(self.engine, "90000001", _envelope())
        got = raw_detail_service.read(self.engine, "90000001")
        self.assertEqual(got["id"], "90000001")
        self.assertEqual(got["fetched_at"], "2026-10-17T12:00:00.000Z")
        self.assertEqual(got["source"], "crawl")
        self.assertEqual(got["raw_sha256"], raw_detail.sha256_hex(canonical))
        self.assertIsInstance(got["json"], dict)
        for key in raw_detail.REPORTER_KEYS:
            self.assertNotIn(key, got["json"]["result"])
        # 공무원 담당자 연락처·본문은 그대로 둔다
        self.assertEqual(got["json"]["result"]["answers"][0]["C_MANAGE_MAN_PHONE"], "02-123-4567")
        self.assertIn("불법주정차 신고합니다.", got["json"]["result"]["C_A_CONTENTS"])

    def test_missing_and_unsafe_id_raise_missing(self):
        from services import raw_detail_service

        for bad in ("90000002", "../../etc/passwd", "90000001.json", "", "a/b", None):
            with self.subTest(bad=bad):
                with self.assertRaises(raw_detail_service.RawDetailMissing):
                    raw_detail_service.read(self.engine, bad)

    def test_corrupt_sha_raises_corrupt(self):
        from services import raw_detail_service

        _insert_raw(self.engine, "90000001", _envelope(), raw_sha256="0" * 64)
        with self.assertRaises(raw_detail_service.RawDetailCorrupt):
            raw_detail_service.read(self.engine, "90000001")

    def test_corrupt_blob_raises_corrupt(self):
        from services import raw_detail_service

        _insert_raw(self.engine, "90000001", _envelope(), raw_zlib=b"not-zlib")
        with self.assertRaises(raw_detail_service.RawDetailCorrupt):
            raw_detail_service.read(self.engine, "90000001")

    def test_non_object_envelope_raises_corrupt_with_diagnosis(self):
        # Sol P3 [낮음]: sha 는 맞지만 최상위가 객체가 아닌 원본도 다른 손상 분기처럼
        # 진단 기록(note_fallback)·경고 로그를 남긴다.
        from services import raw_detail_service

        canonical = json.dumps(["not", "an", "object"], ensure_ascii=False,
                               separators=(",", ":")).encode("utf-8")
        with self.engine.begin() as conn:
            conn.execute(models.report_detail_raw_table.insert().values(
                ID="90000001", raw_zlib=raw_detail.compress(canonical),
                raw_sha256=raw_detail.sha256_hex(canonical), raw_size=len(canonical),
                fetched_at="2026-10-17T12:00:00.000Z", source="crawl"))
        with mock.patch.object(raw_detail_service, "note_fallback") as noted, \
                mock.patch.object(raw_detail_service.logger.LoggerFactory.logbot,
                                  "warning") as warned:
            with self.assertRaises(raw_detail_service.RawDetailCorrupt):
                raw_detail_service.read(self.engine, "90000001")
        noted.assert_called_once()
        self.assertEqual(noted.call_args.args[0], "raw_detail_view.shape")
        warned.assert_called_once()
        self.assertIn("90000001", warned.call_args.args[0])

    def test_rewrite_regenerates_the_file(self):
        from services import raw_detail_service

        _insert_raw(self.engine, "90000001", _envelope())
        path = raw_detail_service.rewrite_file(self.engine, "90000001")
        try:
            self.assertEqual(os.path.basename(path), "90000001.json")
            stored = json.loads(Path(path).read_text(encoding="utf-8"))
            self.assertEqual(stored, json.loads(raw_detail.verify(
                self._row_blob("90000001"), self._row_sha("90000001")).decode("utf-8")))
            for key in raw_detail.REPORTER_KEYS:
                self.assertNotIn(key, stored["result"])
        finally:
            Path(path).unlink(missing_ok=True)

    def _row_blob(self, report_id):
        with self.engine.connect() as conn:
            return conn.execute(select(models.report_detail_raw_table.c.raw_zlib)
                                .where(models.report_detail_raw_table.c.ID == report_id)).scalar()

    def _row_sha(self, report_id):
        with self.engine.connect() as conn:
            return conn.execute(select(models.report_detail_raw_table.c.raw_sha256)
                                .where(models.report_detail_raw_table.c.ID == report_id)).scalar()


class WebRawDetailRouteTests(_IsolatedEngine):
    """읽기 라우트는 관리자 인증 필수(전역 auth_middleware) — 비로그인이면 거부."""

    def setUp(self):
        logger.LoggerFactory.create_logger(mode="crawl")
        self.engine = self._make_engine()
        _insert_raw(self.engine, "90000001", _envelope())
        app = FastAPI()
        from fastapi import Request as _Request

        import main as app_main

        async def _csrf(request: _Request):
            from starlette.responses import JSONResponse

            from core.utils import csrf as _csrf_mod

            return JSONResponse({"csrf": _csrf_mod.get_or_create_token(request)})

        app.add_api_route("/test-csrf", _csrf)

        async def _test_login(request, call_next):
            # 시험 전용: 헤더가 있으면 관리자 세션을 넣어준다(Session 뒤·auth 앞에서 실행).
            if request.headers.get("X-Test-Admin") == "1":
                request.session["admin_logged_in"] = True
            return await call_next(request)

        app.middleware("http")(app_main.auth_middleware)
        app.middleware("http")(app_main.inject_version_middleware)
        app.middleware("http")(_test_login)
        app.include_router(raw_detail_route.router)
        # SessionMiddleware 를 가장 마지막에 넣어 가장 바깥에서 실행되게 한다(main.create_app 과 같은 순서)
        app.add_middleware(SessionMiddleware, secret_key="raw-detail-fixture")
        engine_patch = mock.patch.object(raw_detail_route, "engine", self.engine)
        engine_patch.start()
        self.addCleanup(engine_patch.stop)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.admin = TestClient(app, headers={"X-Test-Admin": "1"})
        self.addCleanup(self.admin.close)

    def test_anonymous_is_refused(self):
        response = self.client.get("/reports/90000001/raw-detail", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers.get("location", ""))

    def test_anonymous_ajax_gets_401_not_the_envelope(self):
        response = self.client.get("/reports/90000001/raw-detail",
                                   headers={"Accept": "application/json",
                                            "X-Requested-With": "XMLHttpRequest"})
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("C_A_CONTENTS", response.text)

    def _csrf(self):
        response = self.admin.get("/test-csrf")
        self.assertEqual(response.status_code, 200)
        return response.json()["csrf"]

    def test_ok_returns_the_stored_object(self):
        response = self.admin.get("/reports/90000001/raw-detail")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["id"], "90000001")
        self.assertEqual(body["fetched_at"], "2026-10-17T12:00:00.000Z")
        self.assertEqual(body["source"], "crawl")
        self.assertEqual(len(body["raw_sha256"]), 64)
        self.assertIsInstance(body["json"], dict)
        for key in raw_detail.REPORTER_KEYS:
            self.assertNotIn(key, body["json"]["result"])

    def test_missing_is_404(self):
        response = self.admin.get("/reports/90000002/raw-detail")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"error": "raw_detail_missing"})

    def test_unsafe_id_is_404(self):
        for bad in ("..%2F..%2Fetc%2Fpasswd", "90000001.json"):
            with self.subTest(bad=bad):
                response = self.admin.get(f"/reports/{bad}/raw-detail")
                self.assertEqual(response.status_code, 404)

    def test_corrupt_is_409(self):
        with self.engine.begin() as conn:
            conn.execute(models.report_detail_raw_table.update()
                         .where(models.report_detail_raw_table.c.ID == "90000001")
                         .values(raw_sha256="0" * 64))
        response = self.admin.get("/reports/90000001/raw-detail")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {"error": "raw_detail_corrupt"})

    def test_rewrite_without_csrf_is_refused(self):
        response = self.admin.post("/reports/90000001/raw-detail/rewrite",
                                    content="{}",
                                    headers={"Content-Type": "application/json"})
        self.assertEqual(response.status_code, 403)

    def test_rewrite_regenerates_the_file(self):
        token = self._csrf()
        response = self.admin.post("/reports/90000001/raw-detail/rewrite",
                                    content="{}",
                                    headers={"Content-Type": "application/json",
                                             "X-CSRF-Token": token})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body, {"status": "success", "id": "90000001",
                                "filename": "90000001.json"})
        import settings.settings as settings

        path = Path(settings.raw_details_dir) / "90000001.json"
        try:
            self.assertTrue(path.is_file())
            stored = json.loads(path.read_text(encoding="utf-8"))
            for key in raw_detail.REPORTER_KEYS:
                self.assertNotIn(key, stored["result"])
        finally:
            path.unlink(missing_ok=True)

    def test_rewrite_missing_is_404(self):
        token = self._csrf()
        response = self.admin.post("/reports/90000002/raw-detail/rewrite",
                                    content="{}",
                                    headers={"Content-Type": "application/json",
                                             "X-CSRF-Token": token})
        self.assertEqual(response.status_code, 404)


class MobileRawDetailApiTests(_IsolatedEngine):
    def setUp(self):
        logger.LoggerFactory.create_logger(mode="crawl")
        self.engine = self._make_engine()
        _insert_raw(self.engine, "90000001", _envelope())
        with self.engine.connect() as conn:
            self.api_key = conn.execute(
                select(models.api_keys_table.c.key)).scalars().first()
        if self.api_key is None:
            from core.database import database

            self.api_key = database.create_api_key(self.engine, "raw-detail-test")
        patch = mock.patch.object(api_route, "engine", self.engine)
        patch.start()
        self.addCleanup(patch.stop)
        app = FastAPI()
        app.include_router(api_route.router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def _get(self, report_id, *, key=True):
        headers = {"X-API-Key": self.api_key} if key else {}
        return self.client.get(f"/api/v1/reports/{report_id}/raw-detail", headers=headers)

    def test_api_key_is_required(self):
        self.assertEqual(self._get("90000001", key=False).status_code, 401)
        self.assertEqual(self.client.get("/api/v1/reports/90000001/raw-detail",
                                         headers={"X-API-Key": "wrong"}).status_code, 401)

    def test_ok_response_fields(self):
        response = self._get("90000001")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["id"], "90000001")
        self.assertEqual(body["fetched_at"], "2026-10-17T12:00:00.000Z")
        self.assertEqual(body["source"], "crawl")
        self.assertEqual(len(body["raw_sha256"]), 64)
        self.assertIsInstance(body["json"], dict)
        for key in raw_detail.REPORTER_KEYS:
            self.assertNotIn(key, body["json"]["result"])
        self.assertEqual(body["json"]["result"]["answers"][0]["C_MANAGE_MAN"], "김담당")

    def test_missing_is_404(self):
        response = self._get("90000002")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"error": "raw_detail_missing"})

    def test_unsafe_id_is_refused(self):
        response = self._get("90000001.json")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"error": "raw_detail_missing"})
        # 경로 이탈 시도도 구분해 알리지 않고 거부한다(라우터 불일치 포함 어떤 경우든 원본이 나가지 않음)
        response = self._get("..%2F..%2Fx")
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("C_A_CONTENTS", response.text)

    def test_corrupt_is_409(self):
        with self.engine.begin() as conn:
            conn.execute(models.report_detail_raw_table.update()
                         .where(models.report_detail_raw_table.c.ID == "90000001")
                         .values(raw_sha256="f" * 64))
        response = self._get("90000001")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {"error": "raw_detail_corrupt"})

    def test_existing_reports_routes_are_unchanged(self):
        response = self.client.get("/api/v1/reports/traffic",
                                   headers={"X-API-Key": self.api_key})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "success")
        self.assertIn("data", body)

    def test_app_config_advertises_raw_detail(self):
        response = self.client.get("/api/v1/app/config", headers={"X-API-Key": self.api_key})
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertIn("raw_detail_v1", data["capabilities"])
        self.assertEqual(data["raw_detail_version"], 1)
        for kept in ("rating_cause", "community_account", "report_search_v1", "law_search_v2"):
            self.assertIn(kept, data["capabilities"])


class RawDetailsBrowserDirTests(unittest.TestCase):
    def setUp(self):
        logger.LoggerFactory.create_logger(mode="crawl")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.roots = {name: str(base / name) for name in ("logs", "results", "raw_details")}
        for path in self.roots.values():
            Path(path).mkdir()
        self.raw_file = base / "raw_details" / "90000001.json"
        self.raw_file.write_text('{"result": {}}', encoding="utf-8")
        self.log_file = base / "logs" / "a.log"
        self.log_file.write_text("log\n", encoding="utf-8")
        for patch in [mock.patch.object(file_service, "ALLOWED_BROWSER_DIRS", self.roots),
                      mock.patch.object(file_service, "ALLOWED_API_ROOTS", frozenset(self.roots)),
                      mock.patch.object(file_service.settings, "datapath", str(base)),
                      mock.patch.object(file_service.settings, "logpath", self.roots["logs"]),
                      mock.patch.object(file_service.settings, "resultpath", self.roots["results"]),
                      mock.patch.object(file_service.settings, "raw_details_dir",
                                        self.roots["raw_details"])]:
            patch.start()
            self.addCleanup(patch.stop)

    def test_raw_details_is_listed_and_downloadable(self):
        groups = file_service.list_browser_groups()
        self.assertEqual([item["name"] for item in groups["raw_details"]], ["90000001.json"])
        self.assertEqual(file_service.ensure_browser_file(str(self.raw_file)), str(self.raw_file))
        with file_service.open_browser_file(str(self.raw_file)) as handle:
            self.assertEqual(handle.read(), b'{"result": {}}')

    def test_escape_is_denied(self):
        outside = Path(self.temp.name) / "outside.json"
        outside.write_text("{}", encoding="utf-8")
        with self.assertRaises(PermissionError):
            file_service.ensure_browser_file(str(outside))
        with self.assertRaises(PermissionError):
            file_service.ensure_browser_file(
                str(Path(self.roots["raw_details"]) / ".." / "outside.json"))

    def test_raw_details_cannot_be_deleted(self):
        with self.assertRaises(PermissionError):
            file_service.delete_file(str(self.raw_file))
        self.assertTrue(self.raw_file.exists())
        count, errors = file_service.delete_files([str(self.raw_file), str(self.log_file)])
        self.assertEqual(count, 1)
        self.assertEqual(len(errors), 1)
        self.assertTrue(self.raw_file.exists())
        self.assertFalse(self.log_file.exists())
        with self.assertRaises(PermissionError):
            file_service.delete_all_in_target("raw_details")
        count, errors = file_service.delete_api_files(["raw_details/90000001.json"])
        self.assertEqual((count, len(errors)), (0, 1))
        self.assertTrue(self.raw_file.exists())

    def test_file_browser_route_serves_the_raw_details_tab(self):
        app = FastAPI()
        app.add_middleware(SessionMiddleware, secret_key="raw-detail-fixture")
        app.include_router(file_browser_route.router)
        with TestClient(app) as client:
            response = client.get("/file-browser?target=raw_details")
            self.assertEqual(response.status_code, 200)
            self.assertIn("raw_details-tab", response.text)
            self.assertIn("90000001.json", response.text)
            self.assertEqual(response.context["explicit_target"], "raw_details")


if __name__ == "__main__":
    unittest.main()
