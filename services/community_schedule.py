"""자정 업로드 스케줄 (contracts/community-ingest/schedule.md).

KST=UTC+9 고정. due_key(t)="midnight:"+kst_date(t), next_due_at(t)=엄격히 다음 자정,
should_run(t, run)=최신 키 succeeded 면 False, running+유효 lease 면 False, 그 밖 True.
register_community_jobs(scheduler): community-midnight-upload(CronTrigger 00:00 Asia/Seoul)
+ community-gate-poll(60초 interval), replace_existing=True 멱등.
run_midnight/catch_up_on_start 는 schedule_runs 키
(project_namespace, contributor_fingerprint, local_dataset_id, writer_epoch, schedule_key) 를 쓴다.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

_log = logging.getLogger("safetyreport.community.schedule")

MIDNIGHT_JOB_ID = "community-midnight-upload"
GATE_POLL_JOB_ID = "community-gate-poll"
SEOUL = ZoneInfo("Asia/Seoul")
_KST = timedelta(hours=9)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def kst_date(now_utc: datetime) -> str:
    return (_utc(now_utc) + _KST).date().isoformat()


def due_key(now_utc: datetime) -> str:
    """t 시점에 이미 지난 가장 최근 KST 자정의 키."""
    return "midnight:" + kst_date(now_utc)


def due_at_utc(schedule_key: str) -> datetime:
    day = schedule_key.split(":", 1)[1]
    year, month, day_no = int(day[0:4]), int(day[5:7]), int(day[8:10])
    return datetime(year, month, day_no, tzinfo=timezone.utc) - _KST


def next_due_at(now_utc: datetime) -> datetime:
    """t 보다 엄격히 늦은 다음 KST 자정 (UTC)."""
    now = _utc(now_utc)
    kst_now = now + _KST
    midnight = kst_now.replace(hour=0, minute=0, second=0, microsecond=0)
    if midnight <= kst_now:
        midnight += timedelta(days=1)
    return (midnight - _KST).replace(tzinfo=timezone.utc)


def should_run(now_utc: datetime, run: dict | None) -> bool:
    """최신 키 1회 보충. succeeded 면 False, running+유효 lease 면 False(합류), 그 밖 True."""
    if not run:
        return True
    state = run.get("state")
    if state == "succeeded":
        return False
    if state == "running":
        try:
            until_raw = run.get("lease_until")
            if until_raw:
                until = datetime.fromisoformat(str(until_raw).replace("Z", "+00:00"))
                if until.tzinfo is None:
                    until = until.replace(tzinfo=timezone.utc)
                if until > _utc(now_utc):
                    return False
        except ValueError:
            pass
        return True
    return True


def _store(data_dir=None):
    from services.community_store import CommunityStore
    return CommunityStore.open(data_dir)


def _key_parts(store) -> dict | None:
    from services.community_store import project_namespace
    ctx = store.active_context()
    if not ctx:
        return None
    try:
        from services import community_auth_service as cas
        namespace = project_namespace(cas.load_config_from_settings().supabase_url)
    except Exception:
        namespace = "unconfigured"
    return {
        "project_namespace": namespace,
        "contributor_fingerprint": ctx.get("contributor_fingerprint"),
        "local_dataset_id": store.local_dataset_id(),
        "writer_epoch": ctx.get("writer_epoch"),
    }


def register_community_jobs(scheduler) -> None:
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger
    for job_id in (MIDNIGHT_JOB_ID, GATE_POLL_JOB_ID):
        try:
            scheduler.remove_job(job_id)
        except Exception:
            pass
    scheduler.add_job(run_midnight, CronTrigger(hour=0, minute=0, timezone=SEOUL),
                      id=MIDNIGHT_JOB_ID, replace_existing=True, max_instances=1, coalesce=True)

    def _poll() -> None:
        try:
            from services import community_gate as _gate
            _gate.refresh_now()
        except ImportError:
            pass

    scheduler.add_job(_poll, IntervalTrigger(seconds=60),
                      id=GATE_POLL_JOB_ID, replace_existing=True, max_instances=1, coalesce=True)


def run_midnight(now_utc: datetime | None = None, data_dir=None) -> dict:
    """should_run → lease → request_upload('midnight') → schedule_runs 기록."""
    from services import community_uploader as _uploader
    store = _store(data_dir)
    now = _utc(now_utc) if now_utc is not None else datetime.now(timezone.utc)
    key = due_key(now)
    parts = _key_parts(store)
    if parts is None:
        return {"result": "deferred", "deferred_reason": "no_active_context", "schedule_key": key}
    scheduled_date = key.split(":", 1)[1]
    with store.transaction() as tx:
        row = tx.execute(
            "SELECT state, lease_until FROM schedule_runs WHERE project_namespace=? AND contributor_fingerprint=?"
            " AND local_dataset_id=? AND writer_epoch=? AND schedule_key=?",
            (parts["project_namespace"], parts["contributor_fingerprint"], parts["local_dataset_id"],
             parts["writer_epoch"], key)).fetchone()
        current = dict(row) if row else None
        if current is not None and not should_run(now, current):
            return {"result": "already_succeeded" if current.get("state") == "succeeded" else "joined",
                    "schedule_key": key}
        owner = str(uuid.uuid4())
        tx.execute(
            "INSERT INTO schedule_runs(project_namespace, contributor_fingerprint, local_dataset_id, writer_epoch,"
            " schedule_key, scheduled_date_kst, due_at_utc, state, attempts, last_attempt_at,"
            " lease_owner, lease_until) VALUES (?, ?, ?, ?, ?, ?, ?, 'running',"
            " COALESCE((SELECT attempts FROM schedule_runs WHERE project_namespace=? AND contributor_fingerprint=?"
            " AND local_dataset_id=? AND writer_epoch=? AND schedule_key=?), 0)+1, ?, ?, ?)"
            " ON CONFLICT(project_namespace, contributor_fingerprint, local_dataset_id, writer_epoch, schedule_key)"
            " DO UPDATE SET state='running', last_attempt_at=excluded.last_attempt_at,"
            " lease_owner=excluded.lease_owner, lease_until=excluded.lease_until,"
            " attempts=schedule_runs.attempts+1",
            (parts["project_namespace"], parts["contributor_fingerprint"], parts["local_dataset_id"],
             parts["writer_epoch"], key, scheduled_date, _iso(due_at_utc(key)), parts["project_namespace"],
             parts["contributor_fingerprint"], parts["local_dataset_id"], parts["writer_epoch"], key,
             _iso(now), owner, _iso(now + timedelta(minutes=10))))
    outcome = _uploader.request_upload("midnight", data_dir=data_dir)
    upload_result = outcome.get("result")
    if upload_result in ("success", "no_change"):
        state, reason = "succeeded", None
    elif upload_result in ("deferred", "joined"):
        state, reason = "deferred", outcome.get("error_code") or upload_result
    else:
        state, reason = "failed", outcome.get("error_code") or upload_result
    with store.transaction() as tx:
        tx.execute(
            "UPDATE schedule_runs SET state=?, finished_at=?, deferred_reason=?, lease_owner=NULL, lease_until=NULL,"
            " run_id=? WHERE project_namespace=? AND contributor_fingerprint=? AND local_dataset_id=?"
            " AND writer_epoch=? AND schedule_key=?",
            (state, _iso(datetime.now(timezone.utc)), reason, outcome.get("run_id"),
             parts["project_namespace"], parts["contributor_fingerprint"], parts["local_dataset_id"],
             parts["writer_epoch"], key))
    return {"result": state, "schedule_key": key, "upload": outcome,
            **({"deferred_reason": reason} if reason else {})}


def catch_up_on_start(data_dir=None) -> None:
    """서버 시작 시 should_run 보충 (run_midnight 과 같은 판단)."""
    try:
        outcome = run_midnight(data_dir=data_dir)
        _log.info("[community] midnight catch-up: %s", outcome.get("result"))
    except Exception:
        _log.exception("[community] midnight catch-up failed")
