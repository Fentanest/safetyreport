"""PC 1회 초기화 크롤링 job (contracts/community-ingest/rebuild.md 상태기계).

- 범위 키 = (REQUIRED_VERSION, local_dataset_id, source_account_namespace).
- 상태는 community.db 의 rebuild_jobs·rebuild_items 에 둔다. 개인 DB(data.db)는
  제자리 갱신만 하고, 시작 전에 sqlite backup API 로 사전 백업을 남긴다.
- 게이트(T3a)·업로더(T4)는 함수 안에서 import 한다(순환 import 방지). 검사를 생략하는 경로는 없다(fail-closed,
  통합 때 병렬 작업용 부재 허용 분기를 제거함).
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import uuid
from datetime import datetime, timezone

REQUIRED_VERSION = "source-rebuild-2026-09-26.1"

#: unique index rebuild_one_active 에 걸리지 않는 종결 상태.
TERMINAL_STATES = ("completed", "completed_with_gaps", "abandoned")

#: 일반 크롤(start_crawl·enqueue)이 막혀 있는 rebuild 상태.
_BLOCKING_STATES = ("awaiting_confirmation", "preparing_backup", "running",
                    "validating", "committing", "paused", "prerequisites_required")

_LEASE_NAME = "rebuild"
_MAX_ATTEMPTS = 5


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _store():
    from services.community_store import CommunityStore
    return CommunityStore.open()


def _datapath() -> str:
    import settings.settings as app_settings
    return app_settings.datapath


def _login_id() -> str | None:
    """설정의 안전신문고 로그인 ID([LOGIN] username). 없으면 None."""
    try:
        import settings.settings as app_settings
        value = getattr(app_settings, "username", None)
    except Exception:
        return None
    if value is None:
        return None
    text = str(value).strip()
    return text or None


_USE_SETTINGS = object()


def source_account_namespace(login_id=_USE_SETTINGS) -> str | None:
    """공식 계정 네임스페이스. 로그인 ID 가 없으면 None(사전조건 미충족)."""
    raw = _login_id() if login_id is _USE_SETTINGS else login_id
    if raw is None:
        return None
    normalized = raw.strip().lower()
    if not normalized:
        return None
    return hashlib.sha256(("safetyreport-dataset|v1|" + normalized).encode("utf-8")).hexdigest()


def _scope() -> tuple[str, str, str | None]:
    store = _store()
    return REQUIRED_VERSION, store.local_dataset_id(), source_account_namespace()


def _gate_fresh() -> dict:
    """community_gate.require_fresh(60)."""
    from services import community_gate as gate
    return gate.require_fresh(max_age=60.0)


def _refresh_manifest() -> bool:
    """community_uploader.refresh_server_completed() — 실패면 False(크롤을 시작하지 않음)."""
    from services import community_uploader as uploader
    return bool(uploader.refresh_server_completed())


def _launch_crawl(run_id: str):
    from services import crawl_control
    return crawl_control.start_rebuild(run_id)


# ── 조회 ─────────────────────────────────────────────────────────────────────

def _row_to_dict(row) -> dict | None:
    return dict(row) if row is not None else None


def _get_job(run_id: str) -> dict | None:
    store = _store()
    row = store.connect().execute(
        "SELECT * FROM rebuild_jobs WHERE run_id=?", (run_id,)).fetchone()
    return _row_to_dict(row)


def _active_job() -> dict | None:
    """현재 범위 키의 미종결 run. 없으면 None."""
    version, dataset_id, namespace = _scope()
    if not namespace:
        return None
    store = _store()
    row = store.connect().execute(
        "SELECT * FROM rebuild_jobs WHERE required_version=? AND local_dataset_id=?"
        " AND source_account_namespace=? AND state NOT IN ('completed','completed_with_gaps','abandoned')"
        " ORDER BY updated_at DESC LIMIT 1",
        (version, dataset_id, namespace)).fetchone()
    return _row_to_dict(row)


def _latest_job() -> dict | None:
    version, dataset_id, namespace = _scope()
    if not namespace:
        return None
    store = _store()
    row = store.connect().execute(
        "SELECT * FROM rebuild_jobs WHERE required_version=? AND local_dataset_id=?"
        " AND source_account_namespace=? ORDER BY updated_at DESC LIMIT 1",
        (version, dataset_id, namespace)).fetchone()
    return _row_to_dict(row)


def _counts(run_id: str) -> dict:
    store = _store()
    rows = store.connect().execute(
        "SELECT state, COUNT(*) AS n FROM rebuild_items WHERE run_id=? GROUP BY state",
        (run_id,)).fetchall()
    counts = {"total": 0, "pending": 0, "fetched": 0,
              "failed_retryable": 0, "failed_permanent": 0}
    for row in rows:
        counts[row["state"]] = int(row["n"])
        counts["total"] += int(row["n"])
    return counts


def _phase_for(job: dict | None) -> str:
    if job is None:
        return "idle"
    state = job.get("state")
    if state == "preparing_backup":
        return "backup"
    if state in ("running", "paused"):
        return "details" if job.get("list_complete") else "listing"
    if state == "validating":
        return "validating"
    if state == "committing":
        return "committing"
    if state in ("completed", "completed_with_gaps"):
        return "done"
    if state == "failed":
        return "details" if job.get("list_complete") else "listing"
    return "idle"


def required() -> bool:
    """이 범위 키에 completed/completed_with_gaps 행이 없으면 True."""
    version, dataset_id, namespace = _scope()
    if not namespace:
        return True  # 공식 계정 없이는 초기화 자체가 필요 상태
    store = _store()
    row = store.connect().execute(
        "SELECT 1 FROM rebuild_jobs WHERE required_version=? AND local_dataset_id=?"
        " AND source_account_namespace=? AND state IN ('completed','completed_with_gaps') LIMIT 1",
        (version, dataset_id, namespace)).fetchone()
    return row is None


def blocking_state() -> dict | None:
    """일반 크롤을 막는 활성 run. 없으면 None."""
    job = _active_job()
    if job is None:
        return None
    if job.get("state") in _BLOCKING_STATES:
        return job
    return None


def status() -> dict:
    """비밀·토큰 없는 화면 표시용 상태."""
    job = _active_job() or _latest_job()
    if job is None:
        version, _, namespace = _scope()
        if not namespace:
            return {"required": True, "state": "prerequisites_required",
                    "phase": "idle", "run_id": None, "counts": _counts("__none__"),
                    "backup_ref": None, "last_error": None,
                    "reason": "official_account_missing",
                    "required_version": version}
        return {"required": True, "state": "required", "phase": "idle",
                "run_id": None, "counts": _counts("__none__"),
                "backup_ref": None, "last_error": None,
                "required_version": version}
    run_id = job["run_id"]
    return {"required": required(), "state": job["state"],
            "phase": _phase_for(job), "run_id": run_id,
            "counts": _counts(run_id), "backup_ref": job.get("backup_ref"),
            "last_error": job.get("last_error"),
            "required_version": job.get("required_version")}


# ── 쓰기 ─────────────────────────────────────────────────────────────────────

def _touch(run_id: str, **fields) -> None:
    store = _store()
    fields["updated_at"] = _iso(_now())
    assigns = ", ".join(f"{key}=?" for key in fields)
    with store.transaction() as tx:
        tx.execute(f"UPDATE rebuild_jobs SET {assigns} WHERE run_id=?",
                   (*fields.values(), run_id))


def _set_state(run_id: str, state: str, *, last_error: str | None = None,
               clear_error: bool = False) -> None:
    fields: dict = {"state": state}
    if last_error is not None:
        fields["last_error"] = last_error
    elif clear_error:
        fields["last_error"] = None
    _touch(run_id, **fields)


def _backup_personal_db(run_id: str) -> tuple[str | None, str]:
    """sqlite backup API 로 개인 DB 사전 백업 + integrity_check.

    (경로|None, 검사 결과) 반환. 실패하면 예외를 올린다(개인 DB 무변경).
    개인 DB 파일이 아직 없으면(새 설치) 건너뛴다.
    """
    import settings.settings as app_settings

    src = app_settings.db_path
    if not src or not os.path.exists(src):
        return None, "skipped_no_personal_db"
    backup_dir = os.path.join(_datapath(), "backups")
    os.makedirs(backup_dir, exist_ok=True)
    target = os.path.join(backup_dir, f"pre-rebuild-{run_id}.db")
    source = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    dest = sqlite3.connect(target)
    try:
        source.backup(dest)
    finally:
        dest.close()
        source.close()
    check = sqlite3.connect(target)
    try:
        result = check.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        check.close()
    if result != "ok":
        try:
            os.remove(target)
        except OSError:
            pass
        raise RuntimeError(f"backup integrity check failed: {result}")
    return target, "ok"


def start(confirmed_by: str) -> dict:
    """사용자 확인 뒤 파이프라인 진입. 이미 활성 run 이 있으면 그 run 을 반환한다."""
    gate = _gate_fresh()
    if not gate.get("can_enter"):
        return {"state": "prerequisites_required", "run_id": None,
                "reason": "gate", "gate": {k: gate.get(k) for k in ("state", "reasons")}}

    version, dataset_id, namespace = _scope()
    if not namespace:
        return {"state": "prerequisites_required", "run_id": None,
                "reason": "official_account_missing",
                "required_version": version}

    job = _active_job()
    if job is not None and job.get("state") in TERMINAL_STATES:
        job = None
    if job is not None:
        _continue_pipeline(job)
        return status()

    done = _latest_job()
    if done is not None and done.get("state") in TERMINAL_STATES:
        return status()  # G05: 완료 뒤 재확인에도 재실행 없음

    run_id = uuid.uuid4().hex
    store = _store()
    now = _iso(_now())
    try:
        with store.transaction() as tx:
            tx.execute(
                "INSERT INTO rebuild_jobs(run_id, required_version, local_dataset_id,"
                " source_account_namespace, state, phase, confirmed_at, started_at,"
                " updated_at, list_complete, counts_json)"
                " VALUES (?, ?, ?, ?, 'awaiting_confirmation', 'idle', ?, ?, ?, 0, '{}')",
                (run_id, version, dataset_id, namespace, str(confirmed_by or "web"), now, now))
    except sqlite3.IntegrityError:
        # 확인 연타·다중 브라우저: 먼저 들어온 run 을 반환한다(G06).
        existing = _active_job()
        if existing is not None:
            return status()
        raise
    store.acquire_lease(_LEASE_NAME, run_id, 3600)
    job = _get_job(run_id)
    assert job is not None
    _continue_pipeline(job)
    return status()


def _continue_pipeline(job: dict) -> None:
    """awaiting_confirmation/preparing_backup/prerequisites_required → backup → manifest → running."""
    run_id = job["run_id"]
    state = job.get("state")
    if state in TERMINAL_STATES + ("running", "validating", "committing",
                                   "paused", "completed", "completed_with_gaps"):
        if state in ("running", "validating", "committing"):
            return
        if state == "paused":
            return
        return

    store = _store()
    if state in ("awaiting_confirmation", "preparing_backup", "failed",
                 "prerequisites_required"):
        if state != "preparing_backup" or not job.get("backup_ref"):
            _set_state(run_id, "preparing_backup", clear_error=(state == "failed"))
            try:
                backup_ref, backup_check = _backup_personal_db(run_id)
            except Exception as exc:
                _touch(run_id, state="failed", last_error=f"backup_failed: {exc}")
                return
            _touch(run_id, backup_ref=backup_ref, backup_check=backup_check)
        if not _refresh_manifest():
            _set_state(run_id, "prerequisites_required",
                       last_error="manifest_unavailable")
            return
        now = _iso(_now())
        _touch(run_id, state="running", started_at=now)
        _store().acquire_lease(_LEASE_NAME, run_id, 3600)
        try:
            _launch_crawl(run_id)
        except Exception as exc:
            _touch(run_id, last_error=f"crawl_launch_failed: {exc}")
        return


def pause(reason: str) -> dict:
    job = _active_job()
    if job is None:
        return status()
    if job.get("state") not in ("running", "validating"):
        return status()
    _set_state(job["run_id"], "paused", last_error=str(reason or "user_paused")[:200])
    return status()


def resume() -> dict:
    """paused/failed → 같은 run 으로 다시 크롤."""
    gate = _gate_fresh()
    if not gate.get("can_enter"):
        return {"state": "prerequisites_required", "run_id": None, "reason": "gate"}
    job = _active_job()
    if job is None:
        return status()
    if job.get("state") not in ("paused", "failed"):
        return status()
    _set_state(job["run_id"], "running", clear_error=True)
    _store().acquire_lease(_LEASE_NAME, job["run_id"], 3600)
    try:
        _launch_crawl(job["run_id"])
    except Exception as exc:
        _touch(job["run_id"], last_error=f"crawl_launch_failed: {exc}")
    return status()


def accept_gaps() -> dict:
    """영구 누락을 사용자가 수락 → committing → completed_with_gaps."""
    job = _active_job()
    if job is None:
        return status()
    if job.get("state") != "validating":
        return status()
    counts = _counts(job["run_id"])
    if not counts["failed_permanent"]:
        return status()
    _touch(job["run_id"], gaps_accepted_at=_iso(_now()))
    _commit(job["run_id"], with_gaps=True)
    return status()


def on_crawl_finished(run_id: str) -> dict:
    """크롤 종료 훅(crawl_manager.run_after_crawl): validating → committing → 종결."""
    job = _get_job(run_id)
    if job is None:
        return {"state": "unknown", "run_id": run_id}
    state = job.get("state")
    if state in TERMINAL_STATES + ("paused", "failed", "committing"):
        return status()
    if state != "running":
        return status()
    counts = _counts(run_id)
    remaining = counts["pending"] + counts["failed_retryable"]
    if not job.get("list_complete"):
        _set_state(run_id, "failed", last_error="list_incomplete")
        return status()
    if remaining:
        # 프로세스가 남은 항목을 두고 끝남(강제 종료·충돌) → 사용자가 resume.
        _set_state(run_id, "paused", last_error="crawl_interrupted")
        return status()
    _set_state(run_id, "validating", clear_error=True)
    counts = _counts(run_id)
    if counts["failed_permanent"]:
        return status()  # 사용자 accept_gaps 대기
    _commit(run_id, with_gaps=False)
    return status()


def _commit(run_id: str, *, with_gaps: bool) -> None:
    """staging → report_latest upsert 병합 + source_generation 증가 (한 트랜잭션)."""
    store = _store()
    _set_state(run_id, "committing")
    with store.transaction() as tx:
        dataset_id = tx.execute(
            "SELECT value FROM meta WHERE key='local_dataset_id'").fetchone()
        local_dataset_id = dataset_id["value"] if dataset_id else ""
        staging = tx.execute(
            "SELECT source_report_id, event_id, payload_sha256, eligible"
            " FROM report_latest_staging WHERE run_id=?", (run_id,)).fetchall()
        row = tx.execute(
            "SELECT MAX(source_generation) AS m FROM report_latest"
            " WHERE local_dataset_id=?", (local_dataset_id,)).fetchone()
        new_gen = int(row["m"] or 0) + 1
        for item in staging:
            tx.execute(
                "INSERT INTO report_latest(local_dataset_id, source_report_id, event_id,"
                " payload_sha256, eligible, source_generation)"
                " VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(local_dataset_id, source_report_id) DO UPDATE SET"
                " event_id=excluded.event_id, payload_sha256=excluded.payload_sha256,"
                " eligible=excluded.eligible, source_generation=excluded.source_generation",
                (local_dataset_id, item["source_report_id"], item["event_id"],
                 item["payload_sha256"], item["eligible"], new_gen))
        final = "completed_with_gaps" if with_gaps else "completed"
        now = _iso(_now())
        tx.execute(
            "UPDATE rebuild_jobs SET state=?, completed_at=?, source_generation=?,"
            " updated_at=? WHERE run_id=?",
            (final, now, new_gen, now, run_id))
    try:
        store.release_lease(_LEASE_NAME, run_id)
    except Exception:
        pass


def mark_login_failed(run_id: str, note: str = "login_failed") -> None:
    """start.py 로그인 실패 경로: job failed (0건 성공 금지)."""
    job = _get_job(run_id)
    if job is None:
        return
    if job.get("state") in TERMINAL_STATES:
        return
    _set_state(run_id, "failed", last_error=str(note)[:300])


def mark_list_failed(run_id: str, note: str = "list_incomplete") -> None:
    """start.py 목록 부분 실패 경로: job failed."""
    mark_login_failed(run_id, note)


def mark_paused_auth(run_id: str) -> None:
    job = _get_job(run_id)
    if job is None:
        return
    if job.get("state") in TERMINAL_STATES + ("paused",):
        return
    _set_state(run_id, "paused", last_error="auth")


def mark_store_unavailable(run_id: str) -> None:
    job = _get_job(run_id)
    if job is None:
        return
    if job.get("state") in TERMINAL_STATES:
        return
    _set_state(run_id, "failed", last_error="community_store_unavailable")


def register_list(run_id: str, report_ids: list[str]) -> None:
    """목록 전 페이지 성공 때만: ID 전부 pending 등록 + list_complete=1."""
    store = _store()
    with store.transaction() as tx:
        for report_id in dict.fromkeys(str(rid) for rid in report_ids):
            tx.execute(
                "INSERT OR IGNORE INTO rebuild_items(run_id, source_report_id, state)"
                " VALUES (?, ?, 'pending')", (run_id, report_id))
        tx.execute("UPDATE rebuild_jobs SET list_complete=1, updated_at=? WHERE run_id=?",
                   (_iso(_now()), run_id))


def pending_detail_ids(run_id: str) -> list[str]:
    """상세 대상: pending·failed_retryable 만(재개 시 fetched 건너뜀)."""
    store = _store()
    rows = store.connect().execute(
        "SELECT source_report_id FROM rebuild_items WHERE run_id=?"
        " AND state IN ('pending','failed_retryable') ORDER BY source_report_id",
        (run_id,)).fetchall()
    return [row["source_report_id"] for row in rows]


def record_item(run_id: str, report_id: str, outcome: str, *,
                note: str = "", event_id: str | None = None,
                list_label: str | None = None) -> None:
    """상세 저장 결과로 item 갱신.

    outcome: fetched | retryable | permanent | auth.
    retryable 은 attempts+1(>=5 면 permanent). permanent 는 당시 목록 라벨 보존.
    """
    store = _store()
    with store.transaction() as tx:
        row = tx.execute(
            "SELECT attempts FROM rebuild_items WHERE run_id=? AND source_report_id=?",
            (run_id, report_id)).fetchone()
        if row is None:
            return
        attempts = int(row["attempts"] or 0)
        if outcome == "fetched":
            if event_id is not None:
                tx.execute(
                    "UPDATE rebuild_items SET state='fetched', attempts=?, last_error=NULL,"
                    " event_id=? WHERE run_id=? AND source_report_id=?",
                    (attempts + 1, event_id, run_id, report_id))
            else:
                tx.execute(
                    "UPDATE rebuild_items SET state='fetched', attempts=?, last_error=NULL"
                    " WHERE run_id=? AND source_report_id=?",
                    (attempts + 1, run_id, report_id))
        elif outcome == "permanent":
            tx.execute(
                "UPDATE rebuild_items SET state='failed_permanent', attempts=?, last_error=?,"
                " last_list_label=? WHERE run_id=? AND source_report_id=?",
                (attempts + 1, str(note)[:300], list_label, run_id, report_id))
        elif outcome == "auth":
            return  # run 전체 paused 는 호출자가 처리
        else:  # retryable
            attempts += 1
            if attempts >= _MAX_ATTEMPTS:
                tx.execute(
                    "UPDATE rebuild_items SET state='failed_permanent', attempts=?, last_error=?"
                    " WHERE run_id=? AND source_report_id=?",
                    (attempts, str(note)[:300], run_id, report_id))
            else:
                tx.execute(
                    "UPDATE rebuild_items SET state='failed_retryable', attempts=?, last_error=?"
                    " WHERE run_id=? AND source_report_id=?",
                    (attempts, str(note)[:300], run_id, report_id))


def note_counts(run_id: str, **values) -> None:
    """counts_json 에 표시용 수치 합산(파서 오류 등)."""
    import json as _json
    store = _store()
    with store.transaction() as tx:
        row = tx.execute("SELECT counts_json FROM rebuild_jobs WHERE run_id=?",
                         (run_id,)).fetchone()
        try:
            counts = _json.loads((row["counts_json"] if row else None) or "{}")
        except ValueError:
            counts = {}
        for key, value in values.items():
            counts[key] = int(value)
        tx.execute("UPDATE rebuild_jobs SET counts_json=?, updated_at=? WHERE run_id=?",
                   (_json.dumps(counts, ensure_ascii=False), _iso(_now()), run_id))


def resume_on_startup() -> dict:
    """재시작 시: running + lease 만료 → 같은 run 재개."""
    store = _store()
    rows = store.connect().execute(
        "SELECT run_id FROM rebuild_jobs WHERE state='running'").fetchall()
    for row in rows:
        run_id = row["run_id"]
        lease = store.connect().execute(
            "SELECT owner, until FROM leases WHERE name=?", (_LEASE_NAME,)).fetchone()
        now = _iso(_now())
        if lease is not None and lease["owner"] == run_id and lease["until"] > now:
            continue  # 다른 살아 있는 워커가 잡고 있음
        store.acquire_lease(_LEASE_NAME, run_id, 3600)
        try:
            _launch_crawl(run_id)
        except Exception as exc:
            _touch(run_id, last_error=f"crawl_launch_failed: {exc}")
            continue
        return {"resumed": run_id}
    return {"resumed": None}
