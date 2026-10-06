"""신고 자료의 주인 = 로그인한 카카오 계정 (services/account_data.py, 2026-09-27 사용자 결정).

- 카카오 회원번호는 서버가 관리하는 identities[provider=kakao] 에서만 읽는다(사용자가 고칠 수 있는 user_metadata 는 쓰지 않음).
- 게이트 통과 뒤 처음이면 DB 에 적고, 다르면 db_owner_mismatch.
- 카카오 로그아웃은 신고 자료만 지운다(관리자·API 키·감시목록·지오코딩 캐시 유지), 크롤링 중이면 아무것도 지우지 않는다.
- 가져오기·복원은 주인이 같은 DB 만 받는다(주인 없는 DB·다른 계정 DB 거절, 무엇이든 바꾸기 전에).
settings.db_path 는 SAFETYREPORT_DATA_DIR 아래 임시 DB 다.
"""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sqlalchemy import func, select, text

import settings.settings as settings
from core.database import database, models
from core.database.engine import get_engine
from core.storage import exchange
from core.utils import logger
from scripts.dev import fixture_server
from services import account_data
from services.community_auth_service import kakao_member_id


def _identity(provider, **data):
    return {"provider": provider, "id": data.pop("id", None), "provider_id": None, "identity_data": data}


class KakaoMemberIdTests(unittest.TestCase):
    def test_reads_only_the_server_managed_kakao_identity(self):
        self.assertEqual(kakao_member_id({"identities": [_identity("kakao", provider_id="920003", sub="920003")]}), "920003")
        self.assertEqual(kakao_member_id({"identities": [_identity("kakao", sub="920004")]}), "920004")
        self.assertEqual(kakao_member_id({"identities": [_identity("kakao", id="920005")]}), "920005")
        self.assertEqual(kakao_member_id({"identities": [_identity("google", provider_id="1"), _identity("kakao", provider_id="7")]}), "7")

    def test_user_metadata_and_bad_values_are_ignored(self):
        self.assertIsNone(kakao_member_id({"user_metadata": {"provider_id": "920003", "sub": "920003"}}),
                          "사용자가 고칠 수 있는 user_metadata 는 믿지 않는다")
        self.assertIsNone(kakao_member_id({"identities": [_identity("kakao", provider_id="abc")]}))
        self.assertIsNone(kakao_member_id({"identities": [_identity("kakao", provider_id=True)]}))
        self.assertIsNone(kakao_member_id({"identities": "x"}))
        self.assertIsNone(kakao_member_id({}))


class _DbCase(unittest.TestCase):
    def setUp(self):
        logger.LoggerFactory.create_logger(mode="crawl")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # 다른 시험 모듈이 import 때 만든 개인 DB 를 쓰므로(같은 SAFETYREPORT_DATA_DIR) 끝나면 원래 파일로 되돌린다
        get_engine().dispose()
        saved = os.path.join(self._tmp.name, "saved_live.db")
        had_db = os.path.exists(settings.db_path)
        if had_db:
            exchange._copy_sqlite(settings.db_path, saved)
        self.addCleanup(self._restore_live, saved if had_db else None)
        for ext in ("", "-wal", "-shm"):
            if os.path.exists(settings.db_path + ext):
                os.remove(settings.db_path + ext)
        fixture_server.seed_engine(get_engine())
        with get_engine().begin() as conn:
            conn.execute(text("INSERT INTO mysafety_geocode_cache(주소정규화, 상태, source) VALUES ('남길 주소', 'ok', 'kakao')"))
            conn.execute(text("INSERT OR IGNORE INTO mysafety_watchlist(신고번호) VALUES ('SPP-2609-9000011')"))

    @staticmethod
    def _restore_live(saved):
        get_engine().dispose()
        for ext in ("", "-wal", "-shm"):
            if os.path.exists(settings.db_path + ext):
                os.remove(settings.db_path + ext)
        if saved:
            exchange._copy_sqlite(saved, settings.db_path)

    def count(self, table):
        with get_engine().connect() as conn:
            return conn.execute(select(func.count()).select_from(table)).scalar()


class OwnerTests(_DbCase):
    def test_first_login_stamps_then_same_is_ok_and_other_is_mismatch(self):
        self.assertIsNone(account_data.db_owner())
        self.assertEqual(account_data.check_owner("910001"), "ok")
        self.assertEqual(account_data.db_owner(), "910001")
        self.assertEqual(account_data.check_owner("910001"), "ok")
        self.assertEqual(account_data.check_owner("910002"), "mismatch")
        self.assertEqual(account_data.db_owner(), "910001", "다른 계정이 와도 주인은 바뀌지 않는다")
        self.assertEqual(account_data.check_owner(None), "unknown")

    def test_a_read_failure_is_not_taken_as_no_owner(self):
        # Codex 검수 P1: 읽기 실패를 "주인 없음"으로 바꾸면 남의 자료에 새 주인을 적는다
        account_data.check_owner("910001")
        with mock.patch.object(database, "get_meta", side_effect=sqlite3.OperationalError("disk I/O error")):
            with self.assertRaises(sqlite3.OperationalError):
                account_data.db_owner()
        with mock.patch.object(database, "stamp_meta_if_missing", side_effect=sqlite3.OperationalError("disk I/O error")):
            with self.assertRaises(sqlite3.OperationalError):
                account_data.check_owner("910002")
        self.assertEqual(account_data.db_owner(), "910001")
        self.assertEqual(account_data.check_owner("910002"), "mismatch")


class WipeTests(_DbCase):
    def test_logout_wipe_empties_reports_only_and_clears_the_owner(self):
        account_data.check_owner("910001")
        self.assertGreater(self.count(models.title_table), 0)
        admins, keys = self.count(models.admin_users_table), self.count(models.api_keys_table)
        watch, geo = self.count(models.watchlist_table), self.count(models.geocode_cache_table)
        self.assertGreater(watch, 0)
        self.assertGreater(geo, 0)
        rotated = []
        with mock.patch("services.community_store.CommunityStore.rotate_dataset", lambda self, reason: rotated.append(reason)):
            info = account_data.wipe_report_data("kakao_logout")
        self.assertEqual(rotated, ["kakao_logout"], "지운 자료의 공유 대기 사본이 다음 계정으로 가지 않게 먼저 선회전")
        self.assertEqual(self.count(models.title_table), 0)
        self.assertEqual(self.count(models.merge_traffic_table), 0)
        self.assertEqual((self.count(models.admin_users_table), self.count(models.api_keys_table)), (admins, keys))
        self.assertEqual((self.count(models.watchlist_table), self.count(models.geocode_cache_table)), (watch, geo))
        self.assertIsNone(account_data.db_owner(), "비운 DB 는 다음 로그인 계정이 주인이 된다")
        self.assertIn("admin_users", info["kept"])
        with get_engine().connect() as conn:
            self.assertEqual(conn.execute(text("PRAGMA user_version")).scalar(), database.SCHEMA_VERSION)
            self.assertEqual(conn.execute(text("PRAGMA integrity_check")).scalar(), "ok")

    def test_adopt_wipes_and_stamps_the_new_owner(self):
        account_data.check_owner("910001")
        with mock.patch("services.community_store.CommunityStore.rotate_dataset", lambda self, reason: None):
            account_data.wipe_report_data("db_owner_adopt", then_owner="910002")
        self.assertEqual(self.count(models.title_table), 0)
        self.assertEqual(account_data.db_owner(), "910002")

    def test_a_failure_while_emptying_keeps_every_report(self):
        # 선회전 뒤 표를 다시 만들다 실패하면 신고 DB 는 통째로 되돌아간다(공유 대기 사본만 새 데이터셋으로 — 다음 계정으로 가지 않는 쪽)
        account_data.check_owner("910001")
        before = self.count(models.title_table)
        rotated = []
        with mock.patch("services.community_store.CommunityStore.rotate_dataset", lambda self, reason: rotated.append(reason)), \
                mock.patch.object(database, "_index_statements", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                account_data.wipe_report_data("kakao_logout")
        self.assertEqual(rotated, ["kakao_logout"])
        self.assertEqual(self.count(models.title_table), before)
        self.assertEqual(account_data.db_owner(), "910001")
        with get_engine().connect() as conn:
            self.assertEqual(conn.execute(text("PRAGMA integrity_check")).scalar(), "ok")

    def test_nothing_is_wiped_while_crawling(self):
        account_data.check_owner("910001")
        before = self.count(models.title_table)
        with mock.patch("services.crawl_manager.crawl_manager.is_crawling", return_value=True):
            with self.assertRaises(exchange.RestoreRefused):
                account_data.wipe_report_data("kakao_logout")
        self.assertEqual(self.count(models.title_table), before)
        self.assertEqual(account_data.db_owner(), "910001")


def _mobile_db(path: Path, owner: str | None):
    con = sqlite3.connect(path)
    con.execute(f"PRAGMA user_version = {exchange.MOBILE_SCHEMA_VERSION}")
    con.executescript(
        """
        CREATE TABLE reports (ID TEXT PRIMARY KEY, 상태 TEXT, 신고번호 TEXT, 신고명 TEXT, 신고일 TEXT, 만족도조사여부 TEXT, 별점 INTEGER,
          별점사유 TEXT, 감시목록 TEXT, 처리상태 TEXT, 처리기관 TEXT, 담당자 TEXT, 위반장소 TEXT, 종결여부 TEXT, category TEXT, entry_value TEXT,
          raw_content TEXT, synced_at INTEGER);
        CREATE TABLE sync_meta (key TEXT PRIMARY KEY, value TEXT);
        """
    )
    con.execute("INSERT INTO reports VALUES ('m1','수용','SPP-2609-9000011','신호위반','2026-09-01','참여 완료',NULL,NULL,'N','수용','기관','','서울 강서구 1','Y','traffic','자동차·교통위반-신호위반','',NULL)")
    if owner is not None:
        con.execute("INSERT INTO sync_meta VALUES (?, ?)", (account_data.KAKAO_MEMBER_META_KEY, owner))
    con.commit()
    con.close()


class ImportRefusalTests(_DbCase):
    def setUp(self):
        super().setUp()
        account_data.check_owner("910001")
        self.before = self.count(models.title_table)

    def _restore(self, path, kind, current="910001"):
        with mock.patch("core.storage.exchange._current_kakao_id", return_value=current):
            return exchange.restore(str(path), kind)

    def test_mobile_db_of_another_account_or_without_owner_is_refused_before_anything_changes(self):
        for owner, current in (("910002", "910001"), (None, "910001"), ("910001", None)):
            path = Path(self._tmp.name) / f"m_{owner}_{current}.db"
            _mobile_db(path, owner)
            with self.assertRaises(account_data.ForeignDatabaseRefused, msg=f"{owner}/{current}"):
                self._restore(path, "mobile", current)
            self.assertEqual(self.count(models.title_table), self.before)
            self.assertEqual(account_data.db_owner(), "910001")

    def test_mobile_db_of_the_same_account_is_restored_and_keeps_the_owner(self):
        path = Path(self._tmp.name) / "same.db"
        _mobile_db(path, "910001")
        with mock.patch("services.community_store.CommunityStore.rotate_dataset", lambda self, reason: None):
            _, count = self._restore(path, "mobile")
        self.assertEqual(count, 1)
        self.assertEqual(account_data.db_owner(), "910001")

    def test_same_kakao_foreign_or_missing_official_account_is_restored(self):
        from services import community_gate
        for kind, table in (("mobile", "sync_meta"), ("server", "mysafety_sync_meta")):
            for value in (None, community_gate.dataset_key("other-official")):
                with self.subTest(kind=kind, value=value):
                    path = Path(self._tmp.name) / f"official_{kind}_{value}.db"
                    if kind == "mobile":
                        _mobile_db(path, "910001")
                    else:
                        exchange._copy_sqlite(settings.db_path, str(path))
                    with sqlite3.connect(path) as con:
                        con.execute(f"DELETE FROM {table} WHERE key=?", ("official_account_dataset_key",))
                        if value:
                            con.execute(f"INSERT INTO {table} VALUES (?, ?)", ("official_account_dataset_key", value))
                    with mock.patch("services.community_store.CommunityStore.rotate_dataset", lambda self, reason: None):
                        backup, count = self._restore(path, kind)
                    self.assertTrue(Path(backup).is_file())
                    self.assertEqual(count, 1)
                    self.assertEqual(self.count(models.title_table), 1)
                    self.assertEqual(account_data.db_owner(), "910001")
                    # Legacy arbitrary metadata remains lossless, but is never an owner check.
                    self.assertEqual(database.get_meta(get_engine(), "official_account_dataset_key"), value)

    def test_server_db_owner_is_read_from_its_own_meta_table(self):
        other = Path(self._tmp.name) / "server_other.db"
        exchange._copy_sqlite(settings.db_path, str(other))
        con = sqlite3.connect(other)
        con.execute("UPDATE mysafety_sync_meta SET value='910002' WHERE key=?", (account_data.KAKAO_MEMBER_META_KEY,))
        con.commit()
        con.close()
        with self.assertRaises(account_data.ForeignDatabaseRefused):
            self._restore(other, "server")
        same = Path(self._tmp.name) / "server_same.db"
        exchange._copy_sqlite(settings.db_path, str(same))
        with mock.patch("services.community_store.CommunityStore.rotate_dataset", lambda self, reason: None):
            self._restore(same, "server")
        self.assertEqual(account_data.db_owner(), "910001")


class LogoutDecisionTests(unittest.TestCase):
    def test_logout_keeps_data_only_when_it_is_known_to_belong_to_another_account(self):
        from services import community_account_ops

        service = mock.Mock()
        for owner, session, wipes in (("910001", "910001", True), (None, "910001", True), ("910001", None, True),
                                      ("910001", "910002", False)):
            service.session_kakao_id.return_value = session
            with mock.patch("services.account_data.db_owner", return_value=owner):
                self.assertEqual(community_account_ops.logout_wipes(service), wipes, (owner, session))
        with mock.patch("services.account_data.db_owner", side_effect=sqlite3.OperationalError("x")):
            with self.assertRaises(sqlite3.OperationalError, msg="읽기 실패는 판단하지 않는다(라우트가 409)"):
                community_account_ops.logout_wipes(service)


if __name__ == "__main__":
    unittest.main()
