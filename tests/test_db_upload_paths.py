"""EO R-12: 웹(/backup/upload)과 API(/api/v1/settings/db/upload)가 같은 업로드·복원 절차를 쓰는지.

서버·모바일·알 수 없는 종류·미래 버전·손상·.db 아님을 두 경로에 똑같이 올려 응답 필드·상태 코드가 같고
(웹만 message 를 더함) 임시 파일이 남지 않는지 본다. 데이터 왕복(모든 열)은 scripts/dev/db_roundtrip_check.py 가 본다.
"""

import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

import settings.settings as settings
from core.database.engine import get_engine
from core.storage import exchange
from core.utils import logger
from scripts.dev import fixture_server
from test_storage_exchange import _mobile_db
from web.routers import api_route, backup_route


class UploadPathsTest(unittest.TestCase):
    def setUp(self):
        logger.LoggerFactory.create_logger(mode="crawl")
        for target, kwargs in (("services.crawl_control._check_crawl_allowed", {"side_effect": RuntimeError("test")}),
                               ("core.storage.exchange._current_kakao_id", {"return_value": "910001"}),
                               ("services.account_data.file_owner", {"return_value": "910001"})):
            p = mock.patch(target, **kwargs)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self._join_threads)
        get_engine().dispose()
        for ext in ("", "-wal", "-shm"):
            if os.path.exists(settings.db_path + ext):
                os.remove(settings.db_path + ext)
        fixture_server.seed_engine(get_engine())
        self.tmp = Path(tempfile.mkdtemp(prefix="sr-r12-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.staging = self.tmp / "staging"
        self.staging.mkdir()
        p = mock.patch("web.db_upload.tempfile.tempdir", str(self.staging))
        p.start()
        self.addCleanup(p.stop)
        app = FastAPI()
        app.include_router(backup_route.router)
        app.include_router(api_route.router)
        app.dependency_overrides[api_route._require_api_key_flex] = lambda: "test"
        self.client = TestClient(app)
        self.files = self._make_files()

    @staticmethod
    def _join_threads():
        import threading
        for t in threading.enumerate():
            if t.name in ("crawl-resume-after-restore", "crawl-pending-after"):
                t.join(15)

    def _make_files(self) -> dict:
        files = {}
        server = self.tmp / "server.db"
        with get_engine().begin() as conn:
            conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
        shutil.copyfile(settings.db_path, server)
        files["server"] = server
        mobile = self.tmp / "mobile.db"
        _mobile_db(mobile)
        files["mobile"] = mobile
        future = self.tmp / "future.db"
        _mobile_db(future, version=exchange.MOBILE_SCHEMA_VERSION + 1)
        files["future"] = future
        unknown = self.tmp / "unknown.db"
        con = sqlite3.connect(unknown)
        con.execute("CREATE TABLE something (x TEXT)")
        con.commit()
        con.close()
        files["unknown"] = unknown
        corrupt = self.tmp / "corrupt.db"
        corrupt.write_bytes(b"SQLite format 3\x00" + b"\x00\xff" * 4096)
        files["corrupt"] = corrupt
        notsqlite = self.tmp / "text.db"
        notsqlite.write_text("hello")
        files["text"] = notsqlite
        return files

    def _post(self, route: str, name: str, filename: str | None = None):
        path = self.files[name]
        url = "/backup/upload" if route == "web" else "/api/v1/settings/db/upload"
        with open(path, "rb") as fh:
            response = self.client.post(url, files={"file": (filename or path.name, fh, "application/octet-stream")})
        self.assertEqual(list(self.staging.iterdir()), [], f"{route}/{name}: 임시 파일이 남았다")
        return response

    def test_both_routes_answer_the_same_for_every_kind(self):
        for name, status in (("server", 200), ("mobile", 200), ("future", 409), ("unknown", 409), ("corrupt", 409),
                             ("text", 409)):
            with self.subTest(name=name):
                web, api = self._post("web", name), self._post("api", name)
                self.assertEqual((web.status_code, api.status_code), (status, status), (web.text, api.text))
                web_body, api_body = web.json(), api.json()
                if status == 200:
                    self.assertTrue(web_body.pop("message"))
                    self.assertEqual(web_body.pop("backup") != "", api_body.pop("backup") != "")
                    self.assertEqual(web_body, api_body)
                    self.assertEqual(api_body["kind"], name)
                else:
                    self.assertEqual(web_body, api_body)
                    self.assertEqual(api_body["status"], "error")

    def test_non_db_filename_is_rejected_before_staging(self):
        for route in ("web", "api"):
            with self.subTest(route=route):
                response = self._post(route, "server", filename="backup.sqlite")
                self.assertEqual(response.status_code, 400)

    def test_failed_write_still_removes_the_staged_file(self):
        from web import db_upload

        with mock.patch.object(db_upload, "run_in_threadpool", side_effect=OSError("disk full")):
            for route in ("web", "api"):
                with self.subTest(route=route), self.assertRaises(OSError):
                    self._post(route, "server")


if __name__ == "__main__":
    unittest.main()
