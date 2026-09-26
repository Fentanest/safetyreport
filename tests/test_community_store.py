"""community.db 골격(contracts/community-ingest/local-store.md)과 계약 사본 무결성."""
import hashlib
import os
import tempfile
import unittest
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
        self.assertEqual(self.store.meta("schema_version"), "1")
        self.assertTrue(self.store.local_dataset_id())
        self.assertEqual(self.store.connect().execute("PRAGMA journal_mode").fetchone()[0], "wal")

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
                               consent_grant_id="g", policy_version="2026-09-26.1", consent_text_sha256="h" * 64,
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
