"""community-ingest 업로드 (PC 데이터 경로).

request_upload(trigger) 하나가 realtime/manual/midnight/recovery/rebuild/reshare 를 모두 처리한다.
- 게이트 require_fresh(60) (T3 제공, 없으면 통과 — 테스트는 monkeypatch)
- 프로세스 lock + community.db lease single-flight (동시 호출은 진행 중 run 에 합류)
- manual/midnight/recovery 면 현재 context 의 미ACK journal 을 outbox 에 enqueue + location_supplement 후보
- drain: 현재 context 와 같은 귀속 행만, 신고별 revision 순서, 요청당 ≤20건·본문 ≤256KiB,
  한 요청에 같은 신고 이벤트는 하나만
- plan-final §8.2 표대로 결과 적용 → durable ACK 만 outbox 삭제·journal 기록 → upload_runs 기록
"""
from __future__ import annotations

import json
import random
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

MAX_EVENTS_PER_REQUEST = 20
MAX_BODY_BYTES = 256 * 1024
LEASE_SECONDS = 300
RETRYABLE_OUTBOX = ("pending", "retry_wait", "in_flight", "auth_required")
DURABLE_ACK = {"accepted", "duplicate", "no_change", "stale_ignored", "quarantined"}

# 403 코드 → 게이트 무효화가 필요한 것들
_GATE_INVALIDATE_CODES = {
    "kakao_required", "session_revoked", "consent_missing", "consent_revoked",
    "consent_outdated", "consent_grant_unknown", "connection_unknown", "connection_revoked",
    "connection_suspended", "connection_session_mismatch", "connection_mode_mismatch",
    "writer_superseded", "contributor_suspended",
}

_run_lock = threading.Lock()
_active_run: dict | None = None  # {"run_id","trigger","finished":Event,"result":dict}
_wake_event = threading.Event()
_bg_thread: threading.Thread | None = None
_bg_stop = threading.Event()
_last_data_version: int | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _store(data_dir=None):
    from services.community_store import CommunityStore
    return CommunityStore.open(data_dir)


def _gate_check():
    """T3 community_gate.require_fresh(60). 없으면 통과로 본다(테스트 monkeypatch 대상)."""
    try:
        from services import community_gate as gate
    except ImportError:
        return {"state": "ok", "can_enter": True, "reasons": [], "verified_age": 0.0}
    return gate.require_fresh(60)


def _gate_invalidate(reason: str) -> None:
    try:
        from services import community_gate as gate
        gate.invalidate(reason)
    except (ImportError, AttributeError):
        pass


def _backoff_delay(attempt: int) -> str:
    seconds = min(3600, 1 * (2 ** max(0, attempt)))
    jittered = seconds * random.uniform(0.8, 1.2)
    return _iso(_now() + timedelta(seconds=jittered))


# ── enqueue ──────────────────────────────────────────────────────────────────

def _enqueue_missing(trigger: str, data_dir=None) -> int:
    """현재 context 의 미ACK journal 중 outbox 에 없는 것을 enqueue. 반환=추가 수."""
    store = _store(data_dir)
    now = _iso(_now())
    with store.transaction() as tx:
        ctx = tx.execute("SELECT * FROM context WHERE id=1").fetchone()
        if ctx is None or ctx["state"] != "active":
            return 0
        rows = tx.execute(
            "SELECT j.event_id FROM source_journal j LEFT JOIN outbox o ON o.event_id=j.event_id"
            " WHERE o.event_id IS NULL AND j.ack_status IS NULL"
            " AND (j.blocked_reason IS NULL OR j.blocked_reason='')"
            " AND j.project_namespace IS NOT NULL"
            " AND j.contributor_fingerprint IS ? AND j.connection_id IS ? AND j.consent_grant_id IS ?",
            (ctx["contributor_fingerprint"], ctx["connection_id"], ctx["consent_grant_id"])).fetchall()
        count = 0
        for row in rows:
            tx.execute(
                "INSERT OR IGNORE INTO outbox(event_id, state, attempt_count, enqueued_trigger, enqueued_at)"
                " VALUES (?, 'pending', 0, ?, ?)", (row["event_id"], trigger, now))
            count += 1
        return count


def _maybe_location_supplement(trigger: str, data_dir=None) -> int:
    """최신 eligible journal 행의 location.source=none 이고 그 address 의 geocode_cache 가 ok 면 보충 이벤트."""
    if trigger not in ("manual", "midnight", "recovery"):
        return 0
    store = _store(data_dir)
    try:
        from core.database.engine import get_engine
        from core.database import models
        from sqlalchemy import select as _select
        engine = get_engine()
    except Exception:
        return 0
    created = 0
    with store.transaction() as tx:
        ctx = tx.execute("SELECT * FROM context WHERE id=1").fetchone()
        if ctx is None or ctx["state"] != "active":
            return 0
        local_id = store.local_dataset_id()
        latest = tx.execute(
            "SELECT source_report_id, MAX(source_revision) AS rev FROM source_journal"
            " WHERE local_dataset_id=? GROUP BY source_report_id", (local_id,)).fetchall()
        for item in latest:
            row = tx.execute(
                "SELECT event_id, payload_json, eligible, blocked_reason FROM source_journal"
                " WHERE local_dataset_id=? AND source_report_id=? AND source_revision=?"
                " ORDER BY source_revision DESC LIMIT 1",
                (local_id, item["source_report_id"], item["rev"])).fetchone()
            if row is None or not row["eligible"] or row["blocked_reason"]:
                continue
            try:
                payload = json.loads(row["payload_json"])
            except ValueError:
                continue
            location = payload.get("location") or {}
            if location.get("source") != "none" or not payload.get("address"):
                continue
            address = payload["address"]
            try:
                with engine.connect() as conn:
                    from services import geocode_service as _geo
                    normalized = _geo.normalize_address(address)
                    cache_row = conn.execute(
                        _select(models.geocode_cache_table).where(
                            models.geocode_cache_table.c["주소정규화"] == normalized)).mappings().first()
                if cache_row is None or cache_row.get("상태") != "ok":
                    continue
                from services.community_capture import _canonical_double as _canon
                lat = _canon(cache_row.get("위도"))
                lng = _canon(cache_row.get("경도"))
                if lat is None or lng is None:
                    continue
                if not (32 <= float(lat) <= 39.5 and 124 <= float(lng) <= 132):
                    continue
            except Exception:
                continue
            new_payload = dict(payload)
            new_payload["location"] = {"lat": lat, "lng": lng, "source": "geocode"}
            from services.community_capture import canonical_json as _cj, payload_sha256 as _sha
            new_sha = _sha(new_payload)
            if new_sha == (json.loads(row["payload_json"]) and _sha(payload)):
                continue
            revision = store.next_revision(tx)
            event_id = str(uuid.uuid4())
            now = _iso(_now())
            tx.execute(
                "INSERT INTO source_journal(event_id, project_namespace, local_dataset_id, dataset_key,"
                " source_report_id, source_revision, event_type, captured_at, capture_trigger,"
                " schema_version, parser_version, payload_json, payload_sha256, eligible,"
                " contributor_fingerprint, connection_id, writer_epoch, consent_grant_id, personal_save_state)"
                " SELECT ?, project_namespace, local_dataset_id, ?, source_report_id, ?,"
                " 'location_supplement', ?, ?, schema_version, parser_version, ?, ?, 1,"
                " ?, ?, ?, ?, 'pending' FROM source_journal WHERE event_id=?",
                (event_id, ctx["dataset_key"], revision, now, trigger, _cj(new_payload), new_sha,
                 ctx["contributor_fingerprint"], ctx["connection_id"], ctx["writer_epoch"],
                 ctx["consent_grant_id"], row["event_id"]))
            tx.execute(
                "INSERT INTO outbox(event_id, state, attempt_count, enqueued_trigger, enqueued_at)"
                " VALUES (?, 'pending', 0, ?, ?)", (event_id, trigger, now))
            tx.execute(
                "INSERT INTO report_latest(local_dataset_id, source_report_id, event_id,"
                " payload_sha256, eligible, source_generation) VALUES (?, ?, ?, ?, 1, 0)"
                " ON CONFLICT(local_dataset_id, source_report_id) DO UPDATE"
                " SET event_id=excluded.event_id, payload_sha256=excluded.payload_sha256, eligible=1",
                (local_id, item["source_report_id"], event_id, new_sha))
            created += 1
    return created


def _due_rows(trigger: str, data_dir=None) -> list[dict]:
    store = _store(data_dir)
    conn = store.connect()
    ctx = store.active_context()
    if ctx is None:
        return []
    now = _iso(_now())
    try:
        from services.community_store import project_namespace as _ns
        from services import community_auth_service as _cas

        _current_ns = _ns(_cas.load_config_from_settings().supabase_url)
    except Exception:
        _current_ns = "unconfigured"
    rows = conn.execute(
        "SELECT o.event_id, o.attempt_count, j.source_report_id, j.source_revision, j.event_type,"
        " j.captured_at, j.payload_json, j.payload_sha256, j.contributor_fingerprint, j.connection_id,"
        " j.consent_grant_id, j.eligible FROM outbox o JOIN source_journal j ON j.event_id=o.event_id"
        " WHERE o.state IN ('pending','retry_wait','in_flight','auth_required')"
        " AND (o.next_retry_at IS NULL OR o.next_retry_at <= ?)"
        " AND j.project_namespace IS ?"
        " AND j.contributor_fingerprint IS ? AND j.connection_id IS ? AND j.consent_grant_id IS ?"
        " AND (j.blocked_reason IS NULL OR j.blocked_reason='')"
        " ORDER BY j.source_revision ASC", (now, _current_ns, ctx["contributor_fingerprint"],
                                            ctx["connection_id"], ctx["consent_grant_id"])).fetchall()
    return [dict(r) for r in rows]


# ── 결과 적용 ────────────────────────────────────────────────────────────────

def _apply_request_error(rows: list[dict], response, data_dir=None) -> None:
    """요청 단위 오류를 outbox/journal 에 반영한다."""
    from services.community_ingest_client import IngestResponse
    assert isinstance(response, IngestResponse)
    store = _store(data_dir)
    code = response.code or "server_error"
    now = _iso(_now())
    with store.transaction() as tx:
        if code == "auth_required":
            for row in rows:
                tx.execute("UPDATE outbox SET state='auth_required', attempt_count=attempt_count+1,"
                           " last_error_code='auth_required', next_retry_at=? WHERE event_id=?",
                           (_backoff_delay(row.get("_attempt", 0)), row["event_id"]))
        elif code in _GATE_INVALIDATE_CODES:
            for row in rows:
                tx.execute("UPDATE outbox SET state='blocked', last_error_code=? WHERE event_id=?",
                           (code, row["event_id"]))
                tx.execute("UPDATE source_journal SET blocked_reason=? WHERE event_id=?",
                           (f"blocked:{code}", row["event_id"]))
            _gate_invalidate(code)
        elif code in ("payload_too_large", "schema_invalid", "invalid_request", "event_type_mismatch",
                      "payload_hash_mismatch", "method_not_allowed", "unsupported_media_type"):
            for row in rows:
                tx.execute("UPDATE outbox SET state='dead_letter', last_error_code=? WHERE event_id=?",
                           (code, row["event_id"]))
        elif code == "rate_limited":
            wait = response.retry_after or 60
            at = _iso(_now() + timedelta(seconds=wait))
            for row in rows:
                tx.execute("UPDATE outbox SET state='retry_wait', attempt_count=attempt_count+1,"
                           " last_error_code='rate_limited', next_retry_at=? WHERE event_id=?",
                           (at, row["event_id"]))
        else:  # busy/server_error/offline/timeout → 지수 백오프
            for row in rows:
                tx.execute("UPDATE outbox SET state='retry_wait', attempt_count=attempt_count+1,"
                           " last_error_code=?, next_retry_at=? WHERE event_id=?",
                           (code, _backoff_delay(row.get("_attempt", 0)), row["event_id"]))


def _apply_event_acks(sent: list[dict], response, request_id: str | None, data_dir=None) -> dict:
    from services.community_ingest_client import IngestResponse
    assert isinstance(response, IngestResponse)
    store = _store(data_dir)
    by_id = {r.event_id: r for r in response.results}
    counts = {"acked": 0, "dead": 0, "blocked": 0, "retry": 0, "quarantined": 0}
    now = _iso(_now())
    with store.transaction() as tx:
        for row in sent:
            ack = by_id.get(row["event_id"])
            if ack is None:
                tx.execute("UPDATE outbox SET state='retry_wait', attempt_count=attempt_count+1,"
                           " next_retry_at=? WHERE event_id=?", (_backoff_delay(0), row["event_id"]))
                counts["retry"] += 1
                continue
            if ack.status in DURABLE_ACK:
                tx.execute("DELETE FROM outbox WHERE event_id=?", (row["event_id"],))
                tx.execute("UPDATE source_journal SET ack_status=?, receipt_id=?, acked_at=?,"
                           " projection_status=? WHERE event_id=?",
                           (ack.status, ack.receipt_id, now, ack.projection_status, row["event_id"]))
                if ack.status == "quarantined":
                    counts["quarantined"] += 1
                else:
                    counts["acked"] += 1
                if ack.status in ("accepted", "duplicate") and row.get("eligible"):
                    tx.execute("DELETE FROM server_completed WHERE key_prefix=?",
                               (_server_prefix(row["source_report_id"]),)) if False else None
            elif ack.status == "conflict":
                tx.execute("UPDATE outbox SET state='dead_letter', last_error_code='conflict',"
                           " last_request_id=? WHERE event_id=?", (request_id, row["event_id"]))
                counts["dead"] += 1
            elif ack.status == "rejected":
                tx.execute("UPDATE outbox SET state='blocked', last_error_code=? WHERE event_id=?",
                           (ack.error_code or "rejected", row["event_id"]))
                tx.execute("UPDATE source_journal SET blocked_reason=? WHERE event_id=?",
                           (f"blocked:{ack.error_code or 'rejected'}", row["event_id"]))
                counts["blocked"] += 1
            else:
                tx.execute("UPDATE outbox SET state='retry_wait', attempt_count=attempt_count+1,"
                           " next_retry_at=? WHERE event_id=?", (_backoff_delay(0), row["event_id"]))
                counts["retry"] += 1
            if ack.projection_status and ack.status not in DURABLE_ACK:
                tx.execute("UPDATE source_journal SET projection_status=? WHERE event_id=?",
                           (ack.projection_status, row["event_id"]))
    return counts


def _server_prefix(source_report_id: str) -> str:
    import hashlib as _hl
    return _hl.sha256(f"safetyreport|{source_report_id}".encode()).hexdigest()[:24]


def _build_batches(rows: list[dict], ctx: dict) -> list[list[dict]]:
    """revision 순서 유지 + 같은 신고는 앞 요청 ACK 뒤로 (한 배치에 신고당 1건) + ≤20건·≤256KiB."""
    batches: list[list[dict]] = []
    current: list[dict] = []
    current_ids: set[str] = set()
    current_bytes = 0
    for row in rows:
        try:
            payload = json.loads(row["payload_json"])
        except ValueError:
            continue
        event = {
            "event_id": row["event_id"],
            "event_type": row["event_type"],
            "source_system": "safetyreport",
            "source_report_id": row["source_report_id"],
            "source_revision": row["source_revision"],
            "writer_epoch": ctx.get("writer_epoch") or 1,
            "captured_at": row["captured_at"],
            "payload": payload,
            "payload_sha256": row["payload_sha256"],
        }
        raw = json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode()
        if row["source_report_id"] in current_ids or len(current) >= MAX_EVENTS_PER_REQUEST \
                or current_bytes + len(raw) > MAX_BODY_BYTES:
            if current:
                batches.append(current)
            current, current_ids, current_bytes = [], set(), 0
        # 단건이 256KiB 초과면 dead_letter 로 직행
        envelope_overhead = 600
        if len(raw) + envelope_overhead > MAX_BODY_BYTES:
            store = _store()
            with store.transaction() as tx:
                tx.execute("UPDATE outbox SET state='dead_letter', last_error_code='payload_too_large'"
                           " WHERE event_id=?", (row["event_id"],))
            continue
        current.append(row)
        current_ids.add(row["source_report_id"])
        current_bytes += len(raw)
    if current:
        batches.append(current)
    return batches


def _send_batch(batch: list[dict], ctx: dict, trigger: str, data_dir=None):
    from services import community_ingest_client as client
    events = []
    for row in batch:
        events.append({
            "event_id": row["event_id"],
            "event_type": row["event_type"],
            "source_system": "safetyreport",
            "source_report_id": row["source_report_id"],
            "source_revision": row["source_revision"],
            "writer_epoch": ctx.get("writer_epoch") or 1,
            "captured_at": row["captured_at"],
            "payload": json.loads(row["payload_json"]),
            "payload_sha256": row["payload_sha256"],
        })
    envelope = client.build_envelope(
        connection_id=ctx.get("connection_id") or "", consent_grant_id=ctx.get("consent_grant_id") or "",
        policy_version=ctx.get("policy_version") or "", source_mode=ctx.get("source_mode") or "server",
        trigger=trigger if trigger in ("realtime", "manual", "midnight", "recovery", "rebuild", "reshare") else "manual",
        events=events)
    store = _store(data_dir)
    with store.transaction() as tx:
        for row in batch:
            tx.execute("UPDATE outbox SET state='in_flight', attempt_count=attempt_count+1 WHERE event_id=?",
                       (row["event_id"],))
    response = client.post_envelope(envelope)
    if response.code == "auth_required" and response.http_status == 401:
        # 토큰 갱신 1회 후 재시도
        try:
            from services import community_auth_service as cas
            try:
                cas.get_access_token()
            except Exception:
                pass
            response = client.post_envelope(envelope)
        except Exception:
            pass
    return response


# ── 공개 API ─────────────────────────────────────────────────────────────────

def request_upload(trigger: str, data_dir=None) -> dict:
    """업로드 1회 실행. 동시 호출은 진행 중 run 에 합류해 같은 run_id 를 돌린다."""
    global _active_run
    with _run_lock:
        if _active_run is not None and not _active_run["finished"].is_set():
            active = _active_run
        else:
            run_id = str(uuid.uuid4())
            finished = threading.Event()
            _active_run = {"run_id": run_id, "trigger": trigger, "finished": finished, "result": {}}
            active = None
    if active is not None:
        active["finished"].wait(timeout=120)
        return dict(active["result"])
    try:
        result = _run_upload(run_id, trigger, data_dir)
    except Exception as exc:
        _log.exception("[community] upload run failed")
        result = {"run_id": run_id, "result": "failed", "counts": {},
                  "request_ids": [], "error_code": type(exc).__name__}
    finally:
        with _run_lock:
            _active_run["result"] = result
            _active_run["finished"].set()
    return result


def _run_upload(run_id: str, trigger: str, data_dir=None) -> dict:
    from services.community_ingest_client import IngestResponse
    store = _store(data_dir)
    started = _iso(_now())
    counts = {"sent": 0, "acked": 0, "dead": 0, "blocked": 0, "retry": 0, "quarantined": 0}
    request_ids: list[str] = []
    error_code: str | None = None

    def finish(result: str) -> dict:
        finished = _iso(_now())
        with store.transaction() as tx:
            tx.execute(
                "INSERT INTO upload_runs(run_id, trigger, contributor_fingerprint, started_at, finished_at,"
                " result, counts_json, request_ids, error_code) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, trigger, (store.active_context() or {}).get("contributor_fingerprint"),
                 started, finished, result, json.dumps(counts, ensure_ascii=False),
                 json.dumps(request_ids, ensure_ascii=False), error_code))
        return {"run_id": run_id, "result": result, "counts": counts, "request_ids": request_ids,
                "error_code": error_code}

    gate = _gate_check()
    if isinstance(gate, dict) and not gate.get("can_enter", True):
        reasons = gate.get("reasons") or []
        if any("consent" in r for r in reasons):
            return finish("consent_required")
        if any("connection" in r or "writer" in r for r in reasons):
            return finish("connection_required")
        if any("auth" in r or "kakao" in r or "session" in r for r in reasons):
            return finish("auth_required")
        return finish("deferred")
    ctx = store.active_context()
    if ctx is None:
        return finish("auth_required")
    owner = f"{run_id}:{trigger}"
    if not store.acquire_lease("upload", owner, LEASE_SECONDS):
        return finish("deferred")
    try:
        if trigger in ("manual", "midnight", "recovery"):
            try:
                _enqueue_missing(trigger, data_dir)
            except Exception:
                pass
            try:
                _maybe_location_supplement(trigger, data_dir)
            except Exception:
                pass
        rows = _due_rows(trigger, data_dir)
        if not rows:
            return finish("no_change")
        ctx = store.active_context()
        if ctx is None:
            return finish("auth_required")
        batches = _build_batches(rows, ctx)
        if not batches:
            return finish("no_change")
        # 신규 이벤트는 1건 즉시 (첫 배치가 1건이면 그대로)
        overall_ok = True
        partial = False
        for batch in batches:
            for row in batch:
                row["_attempt"] = row.get("attempt_count", 0)
            response = _send_batch(batch, store.active_context() or ctx, trigger, data_dir)
            if response.request_id:
                request_ids.append(response.request_id)
            if response.ok:
                applied = _apply_event_acks(batch, response, response.request_id, data_dir)
                counts["sent"] += len(batch)
                for key in ("acked", "dead", "blocked", "retry", "quarantined"):
                    counts[key] += applied.get(key, 0)
                if applied.get("retry") or applied.get("blocked") or applied.get("dead"):
                    partial = True
            else:
                error_code = response.code
                counts["sent"] += len(batch)
                _apply_request_error(batch, response, data_dir)
                if response.code in ("auth_required",) or response.code in _GATE_INVALIDATE_CODES:
                    overall_ok = False
                    break
                if response.code in ("payload_too_large", "schema_invalid", "invalid_request",
                                     "event_type_mismatch", "payload_hash_mismatch"):
                    partial = True
                    counts["dead"] += len(batch)
                else:
                    overall_ok = False
                    counts["retry"] += len(batch)
        if not overall_ok:
            if error_code == "auth_required":
                return finish("auth_required")
            if error_code in ("consent_missing", "consent_revoked", "consent_outdated", "consent_grant_unknown"):
                return finish("consent_required")
            if error_code in ("connection_unknown", "connection_revoked", "connection_suspended",
                              "connection_session_mismatch", "connection_mode_mismatch", "writer_superseded",
                              "contributor_suspended", "kakao_required", "session_revoked"):
                return finish("connection_required")
            if error_code in ("rate_limited", "busy", "server_error", "service_unavailable", "offline"):
                return finish("deferred")
            return finish("failed")
        if partial or counts.get("retry") or counts.get("blocked") or counts.get("dead"):
            return finish("partial")
        return finish("success")
    finally:
        try:
            store.release_lease("upload", owner)
        except Exception:
            pass


def wake() -> None:
    _wake_event.set()


def start_background(data_dir=None) -> None:
    global _bg_thread
    if _bg_thread is not None and _bg_thread.is_alive():
        return
    _bg_stop.clear()

    def loop() -> None:
        global _last_data_version
        store = _store(data_dir)
        try:
            _last_data_version = store.data_version()
        except Exception:
            _last_data_version = None
        while not _bg_stop.is_set():
            if _wake_event.wait(timeout=1.0):
                _wake_event.clear()
                try:
                    request_upload("realtime", data_dir)
                except Exception:
                    pass
                try:
                    _last_data_version = store.data_version()
                except Exception:
                    pass
                continue
            try:
                version = store.data_version()
            except Exception:
                continue
            if _last_data_version is not None and version != _last_data_version:
                _last_data_version = version
                try:
                    request_upload("realtime", data_dir)
                except Exception:
                    pass

    _bg_thread = threading.Thread(target=loop, name="community-upload-watch", daemon=True)
    _bg_thread.start()


def stop_background() -> None:
    _bg_stop.set()
    _wake_event.set()


def upload_status(data_dir=None) -> dict:
    """지도 패널용. 현재 context 계정의 것만 집계한다."""
    store = _store(data_dir)
    conn = store.connect()
    ctx = store.active_context()
    fingerprint = (ctx or {}).get("contributor_fingerprint")
    last = conn.execute("SELECT * FROM upload_runs ORDER BY started_at DESC LIMIT 1").fetchone()
    pending = 0
    needs_attention = 0
    blocked: dict[str, int] = {}
    if fingerprint:
        pending = conn.execute(
            "SELECT COUNT(*) FROM outbox o JOIN source_journal j ON j.event_id=o.event_id"
            " WHERE o.state IN ('pending','retry_wait','in_flight','auth_required')"
            " AND j.contributor_fingerprint IS ?", (fingerprint,)).fetchone()[0]
        needs_attention = conn.execute(
            "SELECT COUNT(*) FROM outbox o JOIN source_journal j ON j.event_id=o.event_id"
            " WHERE o.state IN ('blocked','dead_letter') AND j.contributor_fingerprint IS ?",
            (fingerprint,)).fetchone()[0]
        for row in conn.execute(
                "SELECT COALESCE(j.blocked_reason, o.last_error_code, 'unknown') AS reason, COUNT(*) AS n"
                " FROM outbox o JOIN source_journal j ON j.event_id=o.event_id"
                " WHERE o.state IN ('blocked','dead_letter') AND j.contributor_fingerprint IS ?"
                " GROUP BY reason", (fingerprint,)).fetchall():
            blocked[row["reason"]] = row["n"]
    candidates = reshare_candidates(data_dir) if fingerprint else 0
    projections: dict[str, int] = {}
    if fingerprint:
        for row in conn.execute(
                "SELECT j.projection_status AS p, COUNT(*) AS n FROM source_journal j"
                " WHERE j.contributor_fingerprint IS ? AND j.projection_status IS NOT NULL"
                " GROUP BY p", (fingerprint,)).fetchall():
            projections[row["p"]] = row["n"]
    last_upload_kst = None
    if last and last["finished_at"]:
        try:
            finished = datetime.fromisoformat(str(last["finished_at"]).replace("Z", "+00:00"))
            if finished.tzinfo is None:
                finished = finished.replace(tzinfo=timezone.utc)
            last_upload_kst = (finished + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            last_upload_kst = None
    try:
        request_ids = json.loads(last["request_ids"]) if last and last["request_ids"] else []
    except ValueError:
        request_ids = []
    result = {
        "last_upload_kst": last_upload_kst,
        "last_result": (last["result"] if last else None),
        "last_request": (request_ids[-1] if request_ids else None),
        "pending": pending,
        "needs_attention": needs_attention,
        "blocked": blocked,
        "projections": projections,
        "reshare_candidates": candidates,
        "next_midnight_kst": _next_midnight_label(),
        "has_journal": bool(conn.execute(
            "SELECT 1 FROM source_journal WHERE contributor_fingerprint IS ? LIMIT 1",
            (fingerprint,)).fetchone()) if fingerprint else False,
    }
    return result


def _next_midnight_label() -> str:
    from services.community_schedule import next_due_at
    due = next_due_at(_now())
    kst = due + timedelta(hours=9)
    return kst.strftime("%Y-%m-%d %H:%M")


def reshare_candidates(data_dir=None) -> int:
    store = _store(data_dir)
    ctx = store.active_context()
    if ctx is None:
        return 0
    conn = store.connect()
    local_id = store.local_dataset_id()
    rows = conn.execute(
        "SELECT source_report_id, MAX(source_revision) AS rev FROM source_journal"
        " WHERE local_dataset_id=? AND eligible=1 GROUP BY source_report_id", (local_id,)).fetchall()
    count = 0
    for item in rows:
        row = conn.execute(
            "SELECT consent_grant_id, connection_id, blocked_reason, ack_status FROM source_journal"
            " WHERE local_dataset_id=? AND source_report_id=? AND source_revision=?"
            " ORDER BY source_revision DESC LIMIT 1",
            (local_id, item["source_report_id"], item["rev"])).fetchone()
        if row is None or row["blocked_reason"]:
            continue
        if row["consent_grant_id"] == ctx.get("consent_grant_id") \
                and row["connection_id"] == ctx.get("connection_id"):
            continue
        count += 1
    return count


def request_reshare(data_dir=None) -> dict:
    """최신 eligible 행을 새 event_id·revision·reshare 로 재발급 후 업로드."""
    from services.community_capture import canonical_json as _cj
    store = _store(data_dir)
    ctx = store.active_context()
    if ctx is None:
        return {"result": "auth_required", "count": 0}
    local_id = store.local_dataset_id()
    created: list[str] = []
    with store.transaction() as tx:
        rows = tx.execute(
            "SELECT source_report_id, MAX(source_revision) AS rev FROM source_journal"
            " WHERE local_dataset_id=? AND eligible=1 GROUP BY source_report_id", (local_id,)).fetchall()
        for item in rows:
            row = tx.execute(
                "SELECT * FROM source_journal WHERE local_dataset_id=? AND source_report_id=?"
                " AND source_revision=? ORDER BY source_revision DESC LIMIT 1",
                (local_id, item["source_report_id"], item["rev"])).fetchone()
            if row is None or row["blocked_reason"]:
                continue
            if row["consent_grant_id"] == ctx.get("consent_grant_id") \
                    and row["connection_id"] == ctx.get("connection_id"):
                continue
            revision = store.next_revision(tx)
            event_id = str(uuid.uuid4())
            now = _iso(_now())
            tx.execute(
                "INSERT INTO source_journal(event_id, project_namespace, local_dataset_id, dataset_key,"
                " source_report_id, source_revision, event_type, captured_at, capture_trigger,"
                " schema_version, parser_version, payload_json, payload_sha256, eligible,"
                " contributor_fingerprint, connection_id, writer_epoch, consent_grant_id, personal_save_state)"
                " VALUES (?, ?, ?, ?, ?, ?, 'reshare', ?, 'reshare', ?, ?, ?, ?, 1, ?, ?, ?, ?, 'pending')",
                (event_id, row["project_namespace"], local_id, ctx.get("dataset_key"),
                 row["source_report_id"], revision, row["captured_at"],
                 row["schema_version"], row["parser_version"], row["payload_json"], row["payload_sha256"],
                 ctx.get("contributor_fingerprint"), ctx.get("connection_id"), ctx.get("writer_epoch"),
                 ctx.get("consent_grant_id")))
            tx.execute(
                "INSERT INTO outbox(event_id, state, attempt_count, enqueued_trigger, enqueued_at)"
                " VALUES (?, 'pending', 0, 'reshare', ?)", (event_id, now))
            tx.execute(
                "INSERT INTO report_latest(local_dataset_id, source_report_id, event_id,"
                " payload_sha256, eligible, source_generation) VALUES (?, ?, ?, ?, 1, 0)"
                " ON CONFLICT(local_dataset_id, source_report_id) DO UPDATE"
                " SET event_id=excluded.event_id, payload_sha256=excluded.payload_sha256, eligible=1",
                (local_id, row["source_report_id"], event_id, row["payload_sha256"]))
            created.append(event_id)
    if not created:
        return {"result": "no_change", "count": 0}
    result = request_upload("reshare", data_dir)
    result["reshared"] = len(created)
    return result


def refresh_server_completed(data_dir=None, limit: int = 5000) -> bool:
    """manifest 전 페이지를 받아 total·중복 검증 후 한 트랜잭션으로 교체. 실패면 False.

    모든 페이지의 manifest_token 이 같아야 교체 — 다르면 처음부터 다시, 최대 3회.
    """
    from services import community_ingest_client as client
    store = _store(data_dir)
    ctx = store.active_context()
    if ctx is None or not ctx.get("dataset_key"):
        return False
    dataset_key = ctx["dataset_key"]
    seen: list[str] = []
    total: int | None = None
    for _ in range(3):
        seen = []
        total = None
        token: str | None = None
        after: str | None = None
        consistent = True
        while True:
            ok, body = client.post_manifest(after=after, limit=limit)
            if not ok:
                return False
            page_token = body.get("manifest_token")
            if token is None:
                token = page_token
            elif page_token != token:
                consistent = False
                break
            if total is None:
                total = body.get("total")
            for key in body.get("keys", []):
                seen.append(str(key))
            after = body.get("next_after")
            if not body.get("has_more") or not after:
                break
        if consistent:
            break
    else:
        return False
    if not consistent:
        return False
    if total is not None and len(seen) != total:
        return False
    if len(set(seen)) != len(seen):
        return False
    now = _iso(_now())
    with store.transaction() as tx:
        tx.execute("DELETE FROM server_completed WHERE dataset_key=?", (dataset_key,))
        for prefix in seen:
            tx.execute("INSERT INTO server_completed(dataset_key, key_prefix, fetched_at) VALUES (?, ?, ?)",
                       (dataset_key, prefix, now))
        store.set_meta("manifest_scope", f"{dataset_key}:{ctx.get('writer_epoch')}", tx)
    return True


def on_contributions_deleted(data_dir=None) -> None:
    from services import community_capture as _cap
    _cap.on_contributions_deleted(data_dir)


def outbox_size_warning(data_dir=None, limit_bytes: int = 200 * 1024 * 1024) -> bool:
    """community.db 파일이 limit 초과면 True (자동 삭제는 하지 않음)."""
    store = _store(data_dir)
    try:
        return store.path and __import__("os").path.getsize(store.path) > limit_bytes
    except OSError:
        return False
