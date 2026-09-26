"""커뮤니티 공유용 로컬 저장소 `community.db` (contracts/community-ingest/local-store.md).

개인 DB(data.db)와 별도 파일이다. DB 백업·다운로드·편집기·모바일 변환·초기화(--reset)는 이 파일을 건드리지 않는다.
수집 서브프로세스(start.py)와 메인 서버 프로세스가 함께 연다 → WAL + busy_timeout, 쓰기는 짧은 BEGIN IMMEDIATE 트랜잭션.
비밀(토큰·연결 비밀)은 여기에 두지 않는다.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone

from settings import settings

SCHEMA_VERSION = 1
FILE_NAME = "community.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS context (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  state TEXT NOT NULL CHECK (state IN ('active','inactive')),
  contributor_fingerprint TEXT, connection_id TEXT, writer_epoch INTEGER, dataset_key TEXT,
  consent_grant_id TEXT, policy_version TEXT, consent_text_sha256 TEXT, source_app TEXT, source_mode TEXT,
  verified_at TEXT, inactive_reason TEXT);
CREATE TABLE IF NOT EXISTS source_journal (
  event_id TEXT PRIMARY KEY,
  project_namespace TEXT NOT NULL, local_dataset_id TEXT NOT NULL, dataset_key TEXT,
  source_report_id TEXT NOT NULL, source_revision INTEGER NOT NULL,
  event_type TEXT NOT NULL, captured_at TEXT NOT NULL, capture_trigger TEXT NOT NULL,
  rebuild_run_id TEXT, schema_version INTEGER NOT NULL, parser_version TEXT NOT NULL,
  payload_json TEXT NOT NULL, payload_sha256 TEXT NOT NULL, eligible INTEGER NOT NULL,
  contributor_fingerprint TEXT, connection_id TEXT, writer_epoch INTEGER, consent_grant_id TEXT,
  personal_save_state TEXT NOT NULL DEFAULT 'pending' CHECK (personal_save_state IN ('pending','saved','failed')),
  ack_status TEXT, receipt_id TEXT, acked_at TEXT, projection_status TEXT, blocked_reason TEXT,
  UNIQUE (local_dataset_id, source_revision));
CREATE INDEX IF NOT EXISTS source_journal_report ON source_journal(local_dataset_id, source_report_id, source_revision);
CREATE TABLE IF NOT EXISTS outbox (
  event_id TEXT PRIMARY KEY REFERENCES source_journal(event_id),
  state TEXT NOT NULL CHECK (state IN ('pending','in_flight','retry_wait','auth_required','blocked','dead_letter')),
  attempt_count INTEGER NOT NULL DEFAULT 0, next_retry_at TEXT, lease_owner TEXT, lease_until TEXT,
  last_error_code TEXT, last_request_id TEXT, enqueued_trigger TEXT NOT NULL, enqueued_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS outbox_due ON outbox(state, next_retry_at);
CREATE TABLE IF NOT EXISTS report_latest (
  local_dataset_id TEXT NOT NULL, source_report_id TEXT NOT NULL, event_id TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL, eligible INTEGER NOT NULL, source_generation INTEGER NOT NULL,
  PRIMARY KEY (local_dataset_id, source_report_id));
CREATE TABLE IF NOT EXISTS report_latest_staging (
  run_id TEXT NOT NULL, source_report_id TEXT NOT NULL, event_id TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL, eligible INTEGER NOT NULL, PRIMARY KEY (run_id, source_report_id));
CREATE TABLE IF NOT EXISTS detail_status (
  local_dataset_id TEXT NOT NULL, source_report_id TEXT NOT NULL, c_now_label TEXT NOT NULL, observed_at TEXT NOT NULL,
  PRIMARY KEY (local_dataset_id, source_report_id));
CREATE TABLE IF NOT EXISTS server_completed (
  dataset_key TEXT NOT NULL, key_prefix TEXT NOT NULL, fetched_at TEXT NOT NULL, PRIMARY KEY (dataset_key, key_prefix));
CREATE TABLE IF NOT EXISTS upload_runs (
  run_id TEXT PRIMARY KEY, trigger TEXT NOT NULL, schedule_key TEXT, contributor_fingerprint TEXT,
  started_at TEXT NOT NULL, finished_at TEXT,
  result TEXT CHECK (result IN ('running','no_change','success','partial','auth_required','consent_required',
                                'connection_required','offline','failed','deferred')),
  counts_json TEXT NOT NULL DEFAULT '{}', request_ids TEXT NOT NULL DEFAULT '[]', error_code TEXT);
CREATE TABLE IF NOT EXISTS schedule_runs (
  project_namespace TEXT NOT NULL, contributor_fingerprint TEXT NOT NULL, local_dataset_id TEXT NOT NULL,
  writer_epoch INTEGER NOT NULL, schedule_key TEXT NOT NULL, scheduled_date_kst TEXT NOT NULL, due_at_utc TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('due','running','succeeded','deferred','failed')),
  attempts INTEGER NOT NULL DEFAULT 0, last_attempt_at TEXT, finished_at TEXT, deferred_reason TEXT,
  lease_owner TEXT, lease_until TEXT, run_id TEXT,
  PRIMARY KEY (project_namespace, contributor_fingerprint, local_dataset_id, writer_epoch, schedule_key));
CREATE TABLE IF NOT EXISTS leases (name TEXT PRIMARY KEY, owner TEXT NOT NULL, until TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS rebuild_jobs (
  run_id TEXT PRIMARY KEY, required_version TEXT NOT NULL, local_dataset_id TEXT NOT NULL,
  source_account_namespace TEXT NOT NULL, state TEXT NOT NULL, phase TEXT, confirmed_at TEXT,
  started_at TEXT, updated_at TEXT NOT NULL, completed_at TEXT, list_complete INTEGER NOT NULL DEFAULT 0,
  counts_json TEXT NOT NULL DEFAULT '{}', backup_ref TEXT, backup_check TEXT, last_error TEXT,
  source_generation INTEGER, gaps_accepted_at TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS rebuild_one_active ON rebuild_jobs(required_version, local_dataset_id, source_account_namespace)
  WHERE state NOT IN ('completed','completed_with_gaps','abandoned');
CREATE TABLE IF NOT EXISTS rebuild_items (
  run_id TEXT NOT NULL, source_report_id TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('pending','fetched','failed_retryable','failed_permanent')),
  attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT, event_id TEXT, last_list_label TEXT,
  PRIMARY KEY (run_id, source_report_id));
"""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def project_namespace(supabase_url: str | None) -> str:
    """공개 설정의 Supabase URL 로 만든 네임스페이스. URL 이 바뀌면 이전 대기 자료를 새 곳에 보내지 않는다(E05)."""
    normalized = (supabase_url or "").strip().rstrip("/").lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16] if normalized else "unconfigured"


class CommunityStore:
    """`community.db` 연결과 공통 쿼리. 스레드마다 연결을 따로 연다(sqlite3 연결은 스레드 공유 금지)."""

    _instances: dict[str, "CommunityStore"] = {}
    _lock = threading.Lock()

    def __init__(self, path: str):
        self.path = path
        self._local = threading.local()
        self._migrate()

    @classmethod
    def open(cls, data_dir: str | None = None) -> "CommunityStore":
        base = data_dir or settings.datapath
        path = os.path.abspath(os.path.join(base, FILE_NAME))
        with cls._lock:
            inst = cls._instances.get(path)
            if inst is None:
                inst = cls._instances[path] = cls(path)
            return inst

    @classmethod
    def _forget(cls, path: str) -> None:  # 테스트 전용
        with cls._lock:
            cls._instances.pop(os.path.abspath(path), None)

    # --- 연결 -------------------------------------------------------------------------
    def connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            conn = sqlite3.connect(self.path, timeout=30, isolation_level=None, check_same_thread=True)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=30000")
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    @contextlib.contextmanager
    def transaction(self):
        """BEGIN IMMEDIATE … COMMIT. 예외면 ROLLBACK. 중첩 호출은 바깥 트랜잭션에 합류한다."""
        conn = self.connect()
        if conn.in_transaction:
            yield conn
            return
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")

    def data_version(self) -> int:
        """다른 연결(수집 서브프로세스 포함)이 commit 하면 바뀌는 값 — uploader wake 용."""
        return int(self.connect().execute("PRAGMA data_version").fetchone()[0])

    def _migrate(self) -> None:
        conn = self.connect()
        conn.executescript(_SCHEMA)
        with self.transaction() as tx:
            current = tx.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if current is None:
                tx.execute("INSERT INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
                tx.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('local_dataset_id', ?)", (str(uuid.uuid4()),))
                tx.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('next_revision', '1')")
                tx.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('dataset_history', '[]')")
            elif int(current["value"]) > SCHEMA_VERSION:
                raise RuntimeError(f"community.db schema {current['value']} is newer than this program ({SCHEMA_VERSION})")

    # --- meta ---------------------------------------------------------------------------
    def meta(self, key: str) -> str | None:
        row = self.connect().execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str, conn: sqlite3.Connection | None = None) -> None:
        (conn or self.connect()).execute(
            "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def local_dataset_id(self) -> str:
        return self.meta("local_dataset_id") or ""

    def rotate_dataset(self, reason: str) -> str:
        """개인 DB 가 다른 데이터셋이 됐을 때(복원·가져오기·공식 계정 변경) 호출. 이전 journal/outbox 는 지우지 않는다."""
        new_id = str(uuid.uuid4())
        with self.transaction() as tx:
            old = tx.execute("SELECT value FROM meta WHERE key='local_dataset_id'").fetchone()
            history = json.loads((tx.execute("SELECT value FROM meta WHERE key='dataset_history'").fetchone() or {"value": "[]"})["value"])
            history.append({"dataset_id": old["value"] if old else None, "reason": reason, "rotated_at": iso(utc_now())})
            self.set_meta("dataset_history", json.dumps(history, ensure_ascii=False), tx)
            self.set_meta("local_dataset_id", new_id, tx)
        return new_id

    def next_revision(self, conn: sqlite3.Connection) -> int:
        """트랜잭션 안에서만 호출. 로컬 데이터셋 단조 증가 revision."""
        if not conn.in_transaction:
            raise RuntimeError("next_revision requires an open transaction")
        row = conn.execute("SELECT value FROM meta WHERE key='next_revision'").fetchone()
        value = int(row["value"]) if row else 1
        self.set_meta("next_revision", str(value + 1), conn)
        return value

    def raise_revision_floor(self, last_accepted: int) -> None:
        """중앙이 이미 받은 revision 보다 작아지지 않게(로컬 파일 되돌림 대비)."""
        with self.transaction() as tx:
            row = tx.execute("SELECT value FROM meta WHERE key='next_revision'").fetchone()
            if int(row["value"]) <= int(last_accepted):
                self.set_meta("next_revision", str(int(last_accepted) + 1), tx)

    # --- context ------------------------------------------------------------------------
    _CONTEXT_FIELDS = ("contributor_fingerprint", "connection_id", "writer_epoch", "dataset_key", "consent_grant_id",
                       "policy_version", "consent_text_sha256", "source_app", "source_mode")

    def context(self) -> dict | None:
        row = self.connect().execute("SELECT * FROM context WHERE id=1").fetchone()
        return dict(row) if row else None

    def active_context(self) -> dict | None:
        ctx = self.context()
        return ctx if ctx and ctx.get("state") == "active" else None

    def set_context(self, **fields) -> None:
        unknown = set(fields) - set(self._CONTEXT_FIELDS)
        if unknown:
            raise ValueError(f"unknown context fields: {sorted(unknown)}")
        values = {k: fields.get(k) for k in self._CONTEXT_FIELDS}
        with self.transaction() as tx:
            tx.execute(
                "INSERT INTO context(id, state, contributor_fingerprint, connection_id, writer_epoch, dataset_key,"
                " consent_grant_id, policy_version, consent_text_sha256, source_app, source_mode, verified_at, inactive_reason)"
                " VALUES (1, 'active', :contributor_fingerprint, :connection_id, :writer_epoch, :dataset_key,"
                " :consent_grant_id, :policy_version, :consent_text_sha256, :source_app, :source_mode, :verified_at, NULL)"
                " ON CONFLICT(id) DO UPDATE SET state='active', contributor_fingerprint=excluded.contributor_fingerprint,"
                " connection_id=excluded.connection_id, writer_epoch=excluded.writer_epoch, dataset_key=excluded.dataset_key,"
                " consent_grant_id=excluded.consent_grant_id, policy_version=excluded.policy_version,"
                " consent_text_sha256=excluded.consent_text_sha256,"
                " source_app=excluded.source_app, source_mode=excluded.source_mode, verified_at=excluded.verified_at,"
                " inactive_reason=NULL",
                {**values, "verified_at": iso(utc_now())})

    def deactivate_context(self, reason: str) -> None:
        with self.transaction() as tx:
            tx.execute("INSERT INTO context(id, state, inactive_reason) VALUES (1, 'inactive', ?)"
                       " ON CONFLICT(id) DO UPDATE SET state='inactive', inactive_reason=excluded.inactive_reason", (reason,))

    # --- lease --------------------------------------------------------------------------
    def acquire_lease(self, name: str, owner: str, seconds: int) -> bool:
        now = utc_now()
        with self.transaction() as tx:
            row = tx.execute("SELECT owner, until FROM leases WHERE name=?", (name,)).fetchone()
            if row and row["owner"] != owner and row["until"] > iso(now):
                return False
            tx.execute("INSERT INTO leases(name, owner, until) VALUES (?, ?, ?)"
                       " ON CONFLICT(name) DO UPDATE SET owner=excluded.owner, until=excluded.until",
                       (name, owner, iso(now + timedelta(seconds=seconds))))
            return True

    def release_lease(self, name: str, owner: str) -> None:
        with self.transaction() as tx:
            tx.execute("DELETE FROM leases WHERE name=? AND owner=?", (name, owner))
