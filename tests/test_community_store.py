"""community.db 골격(contracts/community-ingest/local-store.md)과 계약 사본 무결성."""
import hashlib
import os
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from services.community_store import CommunityStore, project_namespace

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "community-ingest"


class ContractCopyTest(unittest.TestCase):
    def test_contract_copy_matches_manifest(self):
        manifest = (CONTRACT / "MANIFEST.sha256").read_text(encoding="utf-8").splitlines()
        listed = set()
        for line in manifest:
            digest, _, rel = line.partition("  ")
            rel = rel.removeprefix("./")
            listed.add(rel)
            self.assertEqual(hashlib.sha256((CONTRACT / rel).read_bytes()).hexdigest(), digest, rel)
        actual = {str(p.relative_to(CONTRACT)) for p in CONTRACT.rglob("*") if p.is_file()} - {"MANIFEST.sha256"}
        self.assertEqual(actual, listed)


class CommunityStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = CommunityStore.open(self.tmp)

    def tearDown(self):
        self.store.close()
        CommunityStore._forget(os.path.join(self.tmp, "community.db"))

    def test_schema_and_meta(self):
        tables = {r[0] for r in self.store.connect().execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for t in ("meta", "context", "source_journal", "outbox", "report_latest", "report_latest_staging", "detail_status",
                  "server_completed", "upload_runs", "schedule_runs", "leases", "rebuild_jobs", "rebuild_items"):
            self.assertIn(t, tables)
        self.assertEqual(self.store.meta("schema_version"), "2")
        self.assertIn("upload_control", tables)
        self.assertTrue(self.store.local_dataset_id())
        self.assertEqual(self.store.connect().execute("PRAGMA journal_mode").fetchone()[0], "wal")

    def test_v1_file_upgrades_to_v2_keeping_every_row(self):
        """UC-1 v2 단계: 표 추가·upload_runs 재생성뿐 — 대기 사본·실행 기록·meta 는 그대로(community.db 를 지우지 않는다)."""
        import sqlite3
        from services import community_store as cs

        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "community.db")
        con = sqlite3.connect(path)
        con.executescript(cs._SCHEMA)
        con.execute("INSERT INTO meta VALUES ('schema_version','1'), ('local_dataset_id','ds-1'), ('next_revision','8'),"
                    " ('dataset_history','[]')")
        con.execute("INSERT INTO source_journal(event_id, project_namespace, local_dataset_id, source_report_id,"
                    " source_revision, event_type, captured_at, capture_trigger, schema_version, parser_version,"
                    " payload_json, payload_sha256, eligible) VALUES ('e1','ns','ds-1','R1',7,'completed_observation',"
                    " '2026-09-26T00:00:00.000Z','realtime',1,'p','{\"a\":\"한글\"}','h',1)")
        con.execute("INSERT INTO outbox(event_id, state, attempt_count, next_retry_at, enqueued_trigger, enqueued_at)"
                    " VALUES ('e1','retry_wait',3,'2026-09-26T01:00:00.000Z','realtime','2026-09-26T00:00:00.000Z')")
        con.execute("INSERT INTO upload_runs(run_id, trigger, started_at, finished_at, result)"
                    " VALUES ('r1','realtime','2026-09-26T00:00:00.000Z','2026-09-26T00:00:01.000Z','deferred')")
        con.commit()
        con.close()
        store = CommunityStore.open(tmp)
        try:
            self.assertEqual(store.meta("schema_version"), "2")
            self.assertEqual(store.local_dataset_id(), "ds-1")
            row = dict(store.connect().execute("SELECT * FROM outbox").fetchone())
            self.assertEqual((row["state"], row["attempt_count"], row["next_retry_at"]),
                             ("retry_wait", 3, "2026-09-26T01:00:00.000Z"))
            self.assertEqual(store.connect().execute("SELECT payload_json FROM source_journal").fetchone()[0], '{"a":"한글"}')
            self.assertEqual(store.connect().execute("SELECT result FROM upload_runs").fetchone()[0], "deferred")
            with store.transaction() as tx:  # 새 결과 코드도 들어간다
                tx.execute("INSERT INTO upload_runs(run_id, trigger, started_at, result) VALUES ('r2','recovery','t','cooldown')")
        finally:
            store.close()
            CommunityStore._forget(path)

    def test_a_second_process_upgrading_first_is_not_upgraded_again(self):
        """이 프로세스가 버전 1 을 읽은 뒤 다른 프로세스가 먼저 v2 로 올리면, 단계 트랜잭션 안에서 다시 읽고 건너뛴다(Sol 구현 검토)."""
        import contextlib
        import sqlite3
        from services import community_store as cs

        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "community.db")
        con = sqlite3.connect(path)
        con.executescript(cs._SCHEMA)
        con.execute("INSERT INTO meta VALUES ('schema_version','1'), ('local_dataset_id','ds-1'), ('next_revision','8'),"
                    " ('dataset_history','[]')")
        con.execute("INSERT INTO upload_runs(run_id, trigger, started_at, result) VALUES ('r1','realtime','t','deferred')")
        con.commit()
        con.close()

        class Counting(dict):
            used = 0

            def __getitem__(self, key):
                Counting.used += 1
                return dict.__getitem__(self, key)

        real_tx = cs.CommunityStore.transaction
        calls = {"n": 0}
        others = []

        @contextlib.contextmanager
        def racing_tx(store_self):
            calls["n"] += 1
            if calls["n"] == 2:  # 첫 단계 트랜잭션 직전 — 다른 프로세스가 먼저 전부 올린다
                with mock.patch.object(cs.CommunityStore, "transaction", real_tx):
                    others.append(cs.CommunityStore(path))
            with real_tx(store_self) as c:
                yield c

        with mock.patch.object(cs, "_MIGRATIONS", Counting(cs._MIGRATIONS)), \
                mock.patch.object(cs.CommunityStore, "transaction", racing_tx):
            store = cs.CommunityStore(path)
        try:
            self.assertEqual(Counting.used, 1, "v2 단계는 먼저 올린 프로세스에서 한 번만")
            self.assertEqual(store.meta("schema_version"), "2")
            self.assertEqual(store.connect().execute("SELECT result FROM upload_runs").fetchone()[0], "deferred")
        finally:
            store.close()
            for o in others:
                o.close()

    def test_lease_renew_only_by_its_owner(self):
        self.assertTrue(self.store.acquire_lease("upload", "run:a", 60))
        self.assertTrue(self.store.renew_lease("upload", "run:a", 60))
        self.assertFalse(self.store.renew_lease("upload", "run:b", 60))
        self.assertFalse(self.store.acquire_lease("upload", "run:b", 60))
        self.store.release_lease("upload", "run:b")  # 남의 lease 는 풀리지 않는다
        self.assertFalse(self.store.acquire_lease("upload", "run:b", 60))
        self.store.release_lease("upload", "run:a")
        self.assertTrue(self.store.acquire_lease("upload", "run:b", 60))

    def test_revision_is_file_wide_monotonic_across_rotation(self):
        with self.store.transaction() as tx:
            first = self.store.next_revision(tx)
        old = self.store.local_dataset_id()
        new = self.store.rotate_dataset("restore_server")
        self.assertNotEqual(old, new)
        with self.store.transaction() as tx:
            second = self.store.next_revision(tx)
        self.assertEqual(second, first + 1)
        self.store.raise_revision_floor(100)
        with self.store.transaction() as tx:
            self.assertEqual(self.store.next_revision(tx), 101)

    def test_next_revision_requires_transaction(self):
        with self.assertRaises(RuntimeError):
            self.store.next_revision(self.store.connect())

    def test_context_active_and_inactive(self):
        self.assertIsNone(self.store.active_context())
        self.store.set_context(contributor_fingerprint="f" * 32, connection_id="c", writer_epoch=3, dataset_key="d" * 64,
                               consent_grant_id="g", policy_version="2026-09-28.1", consent_text_sha256="h" * 64,
                               source_app="safetyreport", source_mode="server")
        self.assertEqual(self.store.active_context()["writer_epoch"], 3)
        self.store.deactivate_context("consent_revoked")
        self.assertIsNone(self.store.active_context())
        self.assertEqual(self.store.context()["inactive_reason"], "consent_revoked")
        with self.assertRaises(ValueError):
            self.store.set_context(token="secret")

    def test_lease_is_exclusive_until_expiry(self):
        self.assertTrue(self.store.acquire_lease("upload", "a", 60))
        self.assertFalse(self.store.acquire_lease("upload", "b", 60))
        self.store.release_lease("upload", "a")
        self.assertTrue(self.store.acquire_lease("upload", "b", 60))

    def test_project_namespace(self):
        self.assertEqual(project_namespace("https://X.supabase.co/"), project_namespace("https://x.supabase.co"))
        self.assertEqual(project_namespace(""), "unconfigured")


if __name__ == "__main__":
    unittest.main()
