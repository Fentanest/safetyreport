"""community-ingest 업로드 (PC 데이터 경로) — 공통 업로드 제어 UC-1(contracts/upload-control, 모바일과 같은 규칙).

request_upload(trigger) 하나가 realtime/manual/midnight/recovery/rebuild/reshare 를 모두 처리한다.
- 삭제 정리 대기·게이트(require_fresh 60) → 영속 전송 제어(upload_control: 서비스·계정 cooldown) → lease(`upload`, 실행별 owner)
- manual/midnight/recovery 면 현재 context 의 미ACK journal 을 outbox 에 넣는다
- 신고마다 보낼 수 있는 가장 앞 revision 하나만 후보(뒤 revision 이 먼저 가지 않음), 요청당 ≤20건·envelope UTF-8 ≤256KiB·신고당 1건
- 실제 HTTP 요청마다 그 요청의 이벤트만 attempt_count+1. 요청 전 lease heartbeat(소유권을 잃으면 멈춤), 요청 간격 ≥1.1초,
  실행 예산(요청 25개·90초) — 남으면 곧바로 다시 깨운다
- 응답은 UC-1 로 판정: durable ACK(+receipt)만 완료(journal 기록+outbox 삭제 한 트랜잭션), 누락 ACK 는 백오프,
  형식 오류는 invalid_ack(재시도, dead_letter 아님), 일시 장애면 남은 배치를 보내지 않고 cooldown, 413·payload 오류는 배치 이분
- 401 은 거절된 토큰으로 실제 강제 갱신 1회 → 재전송. 갱신 네트워크 실패는 offline, 갱신 토큰 폐기는 auth_required
- upload_runs 는 요청을 보냈거나 조치가 필요한 실행만 기록(최근 500행·30일). 미전송 사본은 정리하지 않는다
"""
from __future__ import annotations

import json
import logging
import random
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

from services import community_upload_policy as policy

_log = logging.getLogger("safetyreport.community.uploader")

MAX_EVENTS_PER_REQUEST = 20
MAX_BODY_BYTES = 256 * 1024
LEASE_SECONDS = 120
RUN_MAX_REQUESTS = 25
RUN_MAX_SECONDS = 90.0
MIN_REQUEST_INTERVAL = 1.1
PAGE_ROWS = 200
RUNS_KEEP_ROWS = 500
RUNS_KEEP_DAYS = 30
SENDABLE_STATES = ("pending", "retry_wait", "in_flight", "auth_required")
RETRYABLE_OUTBOX = SENDABLE_STATES
DURABLE_ACK = set(policy.DURABLE_STATUSES)
# 기록하는 결과(요청을 보냈거나 사용자 조치가 필요한 것). no_pending·not_due·cooldown·busy_other_run 은 상태만 돌려준다.
_RECORDED_WITHOUT_REQUESTS = {"needs_auth", "needs_consent", "blocked_gate", "failed"}

_run_lock = threading.Lock()
_run_seq = 0  # 이 프로세스에서 시작한 실행 수(합류 판단용)
_ENQUEUE_TRIGGERS = {"manual", "midnight", "recovery"}
_waiting_enqueue: dict[str, int] = {}  # 합류해 기다리는 enqueue 호출 수(트리거별)
# UC-1 영수증(UUID) 형식의 SQLite GLOB — community_upload_policy._UUID 와 같은 규칙
RECEIPT_GLOB = "-".join("[0-9a-f]" * n for n in (8, 4, 4, 4, 12))
_active_run: dict | None = None  # {"run_id","trigger","finished":Event,"result":dict}
_wake_event = threading.Event()
_bg_thread: threading.Thread | None = None
_bg_stop = threading.Event()
_last_data_version: int | None = None
_next_due: datetime | None = None  # 재시도 실행기가 깨어날 시각(가장 이른 next_retry_at·cooldown)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _rand() -> float:
    return random.random()


def _sleep(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)


def _monotonic() -> float:
    return time.monotonic()


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _store(data_dir=None):
    from services.community_store import CommunityStore
    return CommunityStore.open(data_dir)


def _gate_check():
    """community_gate.require_fresh(60) — 60초 이내 확인이 없으면 동기 재검증(실패면 업로드하지 않음)."""
    from services import community_gate as gate
    return gate.require_fresh(60)


def _gate_invalidate(reason: str) -> None:
    try:
        from services import community_gate as gate
        gate.invalidate(reason)
    except Exception:
        _log.info("[community] gate invalidate 실패: %s", reason)


def _project_ns() -> str:
    try:
        from services.community_store import project_namespace as _ns
        from services import community_auth_service as _cas
        return _ns(_cas.load_config_from_settings().supabase_url)
    except Exception:
        return "unconfigured"


def _backoff_at(attempts: int, hint: int | None = None) -> str:
    return _iso(_now() + timedelta(seconds=policy.retry_delay_seconds(max(1, attempts), _rand(), hint)))


# ── 영속 전송 제어(UC-1 §1-4) ────────────────────────────────────────────────

def _scopes(ctx: dict) -> dict[str, str]:
    ns = _project_ns()
    return {"service": f"service:{ns}", "account": f"account:{ns}:{ctx.get('contributor_fingerprint') or '-'}"}


def _control_rows(conn, scopes: dict[str, str]) -> dict[str, dict]:
    out = {}
    for kind, scope in scopes.items():
        row = conn.execute("SELECT * FROM upload_control WHERE scope=?", (scope,)).fetchone()
        if row is not None:
            out[kind] = dict(row)
    return out


def _control_gate(store, scopes: dict[str, str]) -> tuple[str, datetime | None, str | None]:
    """('ready'|'probe'|'cooldown', 대기 끝 시각, 마지막 오류). cooling_down 이고 시각 전이면 cooldown."""
    rows = _control_rows(store.connect(), scopes)
    probe = False
    wait_until: datetime | None = None
    last_error = None
    for row in rows.values():
        if row["state"] == "ready":
            continue
        until = _parse(row["next_attempt_at"])
        if until is not None and until > _now():
            if wait_until is None or until > wait_until:
                wait_until, last_error = until, row["last_error_code"]
        else:
            probe = True
    if wait_until is not None:
        return "cooldown", wait_until, last_error
    return ("probe" if probe else "ready"), None, None


def _control_mark(store, scope: str, *, state: str, error_code: str | None = None, hint: int | None = None) -> datetime | None:
    """일시 장애면 cooling_down(연속 실패+1, next = now + max(서버 지시, 백오프)), 성공이면 ready. 확률 난수는 _rand()."""
    now = _now()
    with store.transaction() as tx:
        row = tx.execute("SELECT consecutive_failures FROM upload_control WHERE scope=?", (scope,)).fetchone()
        failures = int(row["consecutive_failures"]) if row else 0
        if state == "ready":
            tx.execute("INSERT INTO upload_control(scope, state, next_attempt_at, consecutive_failures, last_error_code,"
                       " updated_at) VALUES (?, 'ready', NULL, 0, NULL, ?) ON CONFLICT(scope) DO UPDATE SET"
                       " state='ready', next_attempt_at=NULL, consecutive_failures=0, last_error_code=NULL,"
                       " updated_at=excluded.updated_at", (scope, _iso(now)))
            return None
        failures += 1
        until = now + timedelta(seconds=policy.retry_delay_seconds(failures, _rand(), hint))
        tx.execute("INSERT INTO upload_control(scope, state, next_attempt_at, consecutive_failures, last_error_code,"
                   " updated_at) VALUES (?, 'cooling_down', ?, ?, ?, ?) ON CONFLICT(scope) DO UPDATE SET"
                   " state='cooling_down', next_attempt_at=excluded.next_attempt_at,"
                   " consecutive_failures=excluded.consecutive_failures, last_error_code=excluded.last_error_code,"
                   " updated_at=excluded.updated_at", (scope, _iso(until), failures, error_code, _iso(now)))
        return until


def _control_probing(store, scopes: dict[str, str]) -> None:
    with store.transaction() as tx:
        for scope in scopes.values():
            tx.execute("UPDATE upload_control SET state='probing', updated_at=? WHERE scope=? AND state='cooling_down'",
                       (_iso(_now()), scope))


# ── enqueue ──────────────────────────────────────────────────────────────────

def _enqueue_missing(trigger: str, data_dir=None) -> int:
    """현재 context 의 미ACK journal 중 outbox 에 없는 것을 enqueue. 반환=추가 수."""
    store = _store(data_dir)
    now = _iso(_now())
    with store.transaction() as tx:
        ctx = tx.execute("SELECT * FROM context WHERE id=1").fetchone()
        if ctx is None or ctx["state"] != "active":
            return 0
        # 예전 코드가 durable 확인 없이 완료로 적은 행(영수증 없음·형식 틀림)은 같은 event_id 로 다시 확인받는다(UC-1 — 중앙은 duplicate
        # 로 영수증을 돌려준다). 현재 연결·동의의 것만.
        # UC-1 과 같은 UUID 판정(소문자 16진 8-4-4-4-12, 길이만 보지 않는다)을 SQL GLOB 한 문장으로 — 이력을 메모리로 읽지 않는다
        tx.execute("UPDATE source_journal SET ack_status=NULL, receipt_id=NULL, acked_at=NULL, projection_status=NULL"
                   " WHERE ack_status IN ('accepted','duplicate','no_change','stale_ignored','quarantined')"
                   " AND (receipt_id IS NULL OR receipt_id NOT GLOB ?)"
                   " AND contributor_fingerprint IS ? AND connection_id IS ? AND consent_grant_id IS ?",
                   (RECEIPT_GLOB, ctx["contributor_fingerprint"], ctx["connection_id"], ctx["consent_grant_id"]))
        # durable ACK 를 받은 journal 의 대기 행은 정리한다(모바일과 같은 단계 — 2026-09-26 감사 SOL-01).
        tx.execute("DELETE FROM outbox WHERE state IN ('pending','retry_wait') AND event_id IN"
                   " (SELECT event_id FROM source_journal WHERE ack_status IS NOT NULL)")
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


# ── 후보 선택 ────────────────────────────────────────────────────────────────

_CTX_FILTER = (" AND j.project_namespace IS ? AND j.contributor_fingerprint IS ? AND j.connection_id IS ?"
               " AND j.consent_grant_id IS ? AND (j.blocked_reason IS NULL OR j.blocked_reason='')")


def _ctx_args(ctx: dict) -> tuple:
    return (_project_ns(), ctx.get("contributor_fingerprint"), ctx.get("connection_id"), ctx.get("consent_grant_id"))


def _front_rows(conn, ctx: dict, now: str, limit: int, exclude: set[str]) -> list[dict]:
    """신고마다 보낼 수 있는 가장 앞 revision 하나(그 행이 due 일 때만). 앞 revision 이 대기 중이면 그 신고는 건너뛴다."""
    rows = conn.execute(
        "SELECT o.event_id, o.attempt_count, o.state, j.source_report_id, j.source_revision, j.event_type, j.captured_at,"
        " j.payload_sha256, j.eligible, j.writer_epoch FROM source_journal j JOIN outbox o ON o.event_id=j.event_id"
        " JOIN (SELECT j2.source_report_id AS rid, MIN(j2.source_revision) AS rev FROM source_journal j2"
        "       JOIN outbox o2 ON o2.event_id=j2.event_id WHERE o2.state IN ('pending','retry_wait','in_flight','auth_required')"
        + _CTX_FILTER.replace("j.", "j2.") + " GROUP BY j2.source_report_id) f"
        " ON f.rid=j.source_report_id AND f.rev=j.source_revision"
        " WHERE o.state IN ('pending','retry_wait','auth_required') AND (o.next_retry_at IS NULL OR o.next_retry_at <= ?)"
        + _CTX_FILTER + " ORDER BY j.source_revision ASC LIMIT ?",
        (*_ctx_args(ctx), now, *_ctx_args(ctx), limit + len(exclude))).fetchall()
    return [dict(r) for r in rows if r["event_id"] not in exclude][:limit]


def _sendable_count(conn, ctx: dict) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM outbox o JOIN source_journal j ON j.event_id=o.event_id"
        " WHERE o.state IN ('pending','retry_wait','in_flight','auth_required')" + _CTX_FILTER,
        _ctx_args(ctx)).fetchone()[0]


def _event(row: dict, payload_json: str, ctx: dict) -> dict:
    """저장된 불변 필드를 그대로 쓴다(event_id·event_type·revision·writer_epoch·captured_at·payload·sha).
    중앙은 같은 event_id 의 재전송에서 이 값들이 다르면 conflict 로 본다 — 현재 context 의 epoch 로 바꾸지 않는다."""
    return {"event_id": row["event_id"], "event_type": row["event_type"], "source_system": "safetyreport",
            "source_report_id": row["source_report_id"], "source_revision": row["source_revision"],
            "writer_epoch": row.get("writer_epoch"), "captured_at": row["captured_at"],
            "payload": json.loads(payload_json), "payload_sha256": row["payload_sha256"]}


def _envelope(ctx: dict, trigger: str, events: list[dict]) -> dict:
    from services import community_ingest_client as client
    return client.build_envelope(
        connection_id=ctx.get("connection_id") or "", consent_grant_id=ctx.get("consent_grant_id") or "",
        policy_version=ctx.get("policy_version") or "", source_mode=ctx.get("source_mode") or "server",
        trigger=trigger if trigger in ("realtime", "manual", "midnight", "recovery", "rebuild", "reshare") else "manual",
        events=events)


def _build_batch(store, rows: list[dict], ctx: dict, trigger: str, max_events: int) -> tuple[list[dict], list[dict]]:
    """(배치 행+event, 단건 초과로 격리한 행). envelope 전체 UTF-8 바이트를 정확히 계산한다(빈 envelope + 이벤트 + 쉼표)."""
    from services import community_ingest_client as client
    base = len(client.envelope_bytes(_envelope(ctx, trigger, [])))
    batch: list[dict] = []
    oversize: list[dict] = []
    total = base
    reports: set[str] = set()
    conn = store.connect()
    for row in rows:
        if len(batch) >= max_events:
            break
        if row["source_report_id"] in reports:
            continue
        found = conn.execute("SELECT payload_json FROM source_journal WHERE event_id=?", (row["event_id"],)).fetchone()
        if found is None:
            continue
        epoch = row.get("writer_epoch")
        if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 1:
            oversize.append(dict(row, _reason="writer_epoch_missing"))  # 지어낸 epoch 로 보내지 않는다
            continue
        try:
            event = _event(row, found["payload_json"], ctx)
        except ValueError:
            oversize.append(dict(row, _reason="payload_unreadable"))
            continue
        size = len(client.envelope_bytes(event))
        if base + size > MAX_BODY_BYTES:
            oversize.append(dict(row, _reason="payload_too_large"))
            continue
        extra = size + (1 if batch else 0)
        if total + extra > MAX_BODY_BYTES:
            continue
        batch.append(dict(row, _event=event))
        reports.add(row["source_report_id"])
        total += extra
    return batch, oversize


# ── 결과 적용 ────────────────────────────────────────────────────────────────

def _dead_letter(store, rows: list[dict], code: str) -> None:
    with store.transaction() as tx:
        for row in rows:
            tx.execute("UPDATE outbox SET state='dead_letter', last_error_code=?, lease_owner=NULL, lease_until=NULL"
                       " WHERE event_id=?", (code, row["event_id"]))


def _mark_in_flight(store, rows: list[dict], owner: str) -> None:
    """실제 HTTP 요청 직전: 이 요청의 이벤트만 attempt_count+1(UC-1 §1-3)."""
    until = _iso(_now() + timedelta(seconds=LEASE_SECONDS))
    with store.transaction() as tx:
        for row in rows:
            tx.execute("UPDATE outbox SET state='in_flight', attempt_count=attempt_count+1, lease_owner=?, lease_until=?"
                       " WHERE event_id=?", (owner, until, row["event_id"]))
            row["attempt_count"] = int(row.get("attempt_count") or 0) + 1


def _retry_rows(tx, rows: list[dict], code: str, hint: int | None, request_id: str | None = None,
                owner: str | None = None) -> None:
    """owner 를 주면 그 실행이 잡고 있는 in_flight 행만 바꾼다(lease 를 잃은 뒤 새 실행이 회수한 행을 덮지 않게)."""
    only_mine = " AND state='in_flight' AND lease_owner=?" if owner else ""
    for row in rows:
        tx.execute("UPDATE outbox SET state='retry_wait', next_retry_at=?, last_error_code=?, last_request_id=COALESCE(?, last_request_id),"
                   " lease_owner=NULL, lease_until=NULL WHERE event_id=?" + only_mine,
                   (_backoff_at(int(row.get("attempt_count") or 1), hint), code, request_id, row["event_id"],
                    *((owner,) if owner else ())))


def _ack_input(response) -> dict:
    """IngestResponse(ok) → UC-1 재판정용 본문. 전송 계층이 이미 판정했어도 완료 조건을 여기서 다시 확인한다."""
    results = []
    for r in response.results:
        item = {"event_id": r.event_id, "status": r.status, "durable": r.durable, "receipt_id": r.receipt_id,
                "projection_status": r.projection_status}
        if not r.durable:
            item["error"] = {"code": r.error_code, "retryable": bool(r.error_retryable)} if r.error_code else None
        results.append(item)
    return {"protocol": 1, "request_id": response.request_id, "results": results}


def _apply_ack(store, batch: list[dict], interp, counts: dict) -> None:
    """확인된 이벤트: journal ack 기록 + outbox 삭제를 한 트랜잭션. 누락 ACK 는 백오프(같은 실행에서 다시 보내지 않음)."""
    now = _iso(_now())
    by_id = {row["event_id"]: row for row in batch}
    with store.transaction() as tx:
        for event_id, ev in interp.events.items():
            row = by_id[event_id]
            if ev.outcome == "done":
                tx.execute("DELETE FROM outbox WHERE event_id=?", (event_id,))
                tx.execute("UPDATE source_journal SET ack_status=?, receipt_id=?, acked_at=?, projection_status=?"
                           " WHERE event_id=?", (ev.status, ev.receipt_id, now, ev.projection_status, event_id))
                counts["quarantined" if ev.status == "quarantined" else "acked"] += 1
            elif ev.outcome == "dead":
                tx.execute("UPDATE outbox SET state='dead_letter', last_error_code=?, last_request_id=?,"
                           " lease_owner=NULL, lease_until=NULL WHERE event_id=?",
                           (ev.error_code or "event_conflict", interp.request_id, event_id))
                counts["dead"] += 1
            else:  # rejected → blocked(보존)
                code = ev.error_code or "rejected"
                tx.execute("UPDATE outbox SET state='blocked', last_error_code=?, last_request_id=?,"
                           " lease_owner=NULL, lease_until=NULL WHERE event_id=?", (code, interp.request_id, event_id))
                tx.execute("UPDATE source_journal SET blocked_reason=? WHERE event_id=?", (f"blocked:{code}", event_id))
                counts["blocked"] += 1
            del by_id[event_id]
        missing = [by_id[e] for e in interp.missing if e in by_id]
        _retry_rows(tx, missing, "ack_missing", None, interp.request_id)
        counts["retry"] += len(missing)


def _classify(response) -> "policy.Interpretation":
    if response.ok:
        return None  # ACK 는 _apply_ack 에서
    cls = response.error_class
    if cls is None:  # 오래된 호출자·테스트가 만든 응답: 코드로 같은 규칙을 적용
        obj = {"error": {"code": response.code, "message": "", "request_id": response.request_id or "",
                         "retryable": response.retryable}} if response.code else None
        body = json.dumps(obj).encode() if obj else b""
        headers = {"Retry-After": str(response.retry_after)} if response.retry_after else {}
        interp = policy.interpret_response([], response.http_status, headers, body, _now()) \
            if response.http_status is not None else policy._error(
                "offline" if response.code in ("offline", "timeout") else
                "request_too_large" if response.code == "payload_too_large" else
                "auth_required" if response.code in policy.AUTH_CODES else "server_busy", None, code=response.code)
        return interp
    return policy.Interpretation(kind="error", http_status=response.http_status, error_class=cls,
                                 code=response.code or cls, scope=policy.RETRYABLE_CLASSES.get(cls),
                                 hint=response.retry_after, request_id=response.request_id)


# ── 공개 API ─────────────────────────────────────────────────────────────────

def request_upload(trigger: str, data_dir=None, progress=None) -> dict:
    """업로드 1회 실행. 같은 프로세스의 동시 호출은 진행 중 run 에 합류한다(다른 프로세스는 lease 가 막는다).
    합류한 호출이 manual/midnight/recovery/reshare 면 자기가 도착한 뒤 시작한(enqueue 하는) 실행의 결과를 돌려준다 — 없으면 앞선 실행이
    끝난 뒤 직접 실행한다(enqueue 가 빠지지 않게, 여러 호출이 와도 실행은 하나로 합쳐진다)."""
    global _active_run, _run_seq
    with _run_lock:
        arrived = _run_seq  # 내가 도착하기 전까지 시작된 실행 수
        if trigger in _ENQUEUE_TRIGGERS:
            _waiting_enqueue[trigger] = _waiting_enqueue.get(trigger, 0) + 1
    try:
        return _request_upload(trigger, data_dir, arrived, progress)
    finally:
        with _run_lock:
            if trigger in _ENQUEUE_TRIGGERS:
                _waiting_enqueue[trigger] -= 1


def _effective_trigger(trigger: str) -> str:
    """새 실행을 시작할 때(_run_lock 안): enqueue 가 필요한 호출이 기다리고 있으면 realtime 시작을 그 트리거로 올린다.
    realtime 이 연달아 먼저 시작해도 기다리는 manual/midnight/recovery 가 굶지 않는다(Sol 3차 확인)."""
    if trigger in _ENQUEUE_TRIGGERS or trigger == "reshare":
        return trigger
    for waiting in ("midnight", "recovery", "manual"):
        if _waiting_enqueue.get(waiting):
            return waiting
    return trigger


def _request_upload(trigger: str, data_dir, arrived: int, progress=None) -> dict:
    global _active_run, _run_seq
    while True:
        with _run_lock:
            if _active_run is not None and not _active_run["finished"].is_set():
                active = _active_run
            else:
                _run_seq += 1
                run_id = str(uuid.uuid4())
                trigger = _effective_trigger(trigger)
                _active_run = {"run_id": run_id, "trigger": trigger, "finished": threading.Event(), "result": {},
                               "seq": _run_seq}
                active = None
        if active is None:
            break
        active["finished"].wait(timeout=RUN_MAX_SECONDS + 60)
        if not active["finished"].is_set():
            return {"run_id": active["run_id"], "result": "busy_other_run", "counts": {}, "request_ids": [], "error_code": None}
        # enqueue 가 필요한 호출은 **내가 도착한 뒤 시작한** 실행의 결과만 자기 것으로 쓴다(자정 key 를 앞선 실행의 결과로 끝내지 않게).
        # manual/midnight/recovery 는 그 실행도 enqueue 해야 하고, reshare 는 이미 outbox 에 넣었으므로 뒤에 시작한 어느 실행이든 된다.
        # 앞선 실행이면 다시 돌아 새 실행을 시작하거나 뒤에 시작한 실행에 합류한다(재귀 없음). 새 실행은 기다리는 enqueue 호출이 있으면
        # 그 트리거로 시작하므로(_effective_trigger) realtime 이 연달아 와도 굶지 않는다.
        started_after = active["seq"] > arrived
        if trigger not in _ENQUEUE_TRIGGERS | {"reshare"}:
            return dict(active["result"])
        if started_after and (trigger == "reshare" or active["trigger"] in _ENQUEUE_TRIGGERS):
            return dict(active["result"])
    result: dict = {"run_id": run_id, "result": "failed", "counts": {}, "request_ids": [], "error_code": "not_started"}
    try:
        result = _run_upload(run_id, trigger, data_dir, progress)
    except Exception as exc:
        _log.exception("[community] upload run failed")
        result = {"run_id": run_id, "result": "failed", "counts": {}, "request_ids": [], "error_code": type(exc).__name__}
        try:
            _record_run(_store(data_dir), run_id, trigger, _iso(_now()), result)
        except Exception:
            pass
    finally:
        with _run_lock:
            if _active_run is not None:
                _active_run["result"] = result
                _active_run["finished"].set()
        try:
            _refresh_next_due(data_dir, result)
        except Exception:
            pass
    return result


def _record_run(store, run_id: str, trigger: str, started: str, result: dict) -> None:
    """요청을 보냈거나 조치가 필요한 실행만 기록하고 오래된 기록을 정리한다(미전송 사본은 건드리지 않음)."""
    if not result.get("request_ids") and not result.get("counts", {}).get("sent") \
            and result.get("result") not in _RECORDED_WITHOUT_REQUESTS:
        return
    cutoff = _iso(_now() - timedelta(days=RUNS_KEEP_DAYS))
    with store.transaction() as tx:
        tx.execute(
            "INSERT OR REPLACE INTO upload_runs(run_id, trigger, contributor_fingerprint, started_at, finished_at,"
            " result, counts_json, request_ids, error_code) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, trigger, (store.active_context() or {}).get("contributor_fingerprint"), started, _iso(_now()),
             result.get("result"), json.dumps(result.get("counts") or {}, ensure_ascii=False),
             json.dumps(result.get("request_ids") or [], ensure_ascii=False), result.get("error_code")))
        tx.execute("DELETE FROM upload_runs WHERE started_at < ? OR run_id NOT IN"
                   " (SELECT run_id FROM upload_runs ORDER BY started_at DESC LIMIT ?)", (cutoff, RUNS_KEEP_ROWS))


def _gate_result(gate: dict) -> str | None:
    if not isinstance(gate, dict) or gate.get("can_enter", True):
        return None
    reasons = " ".join(str(r) for r in (gate.get("reasons") or [])) + " " + str(gate.get("state") or "")
    if "consent" in reasons or "suspend" in reasons:
        return "needs_consent"
    if "kakao" in reasons or "session" in reasons or "auth" in reasons:
        return "needs_auth"
    return "blocked_gate"


def _run_upload(run_id: str, trigger: str, data_dir=None, progress=None) -> dict:
    store = _store(data_dir)
    started = _iso(_now())
    counts = {"sent": 0, "acked": 0, "dead": 0, "blocked": 0, "retry": 0, "quarantined": 0, "requests": 0}
    request_ids: list[str] = []
    state = {"error_code": None, "next_attempt_at": None}

    def report(message: str) -> None:
        if progress is not None:
            try:
                progress(message)
            except Exception:
                _log.info("[community] upload progress callback failed", exc_info=True)

    def finish(result: str) -> dict:
        out = {"run_id": run_id, "result": result, "counts": counts, "request_ids": request_ids,
               "error_code": state["error_code"], "next_attempt_at": state["next_attempt_at"]}
        _record_run(store, run_id, trigger, started, out)
        report(f"결과 {result}: 전송 {counts['sent']}건, 확인 {counts['acked']}건, 재시도 {counts['retry']}건"
               + (f" ({state['error_code']})" if state['error_code'] else ""))
        return out

    from services import community_capture as _cap
    if _cap.deletion_cleanup_pending(data_dir):  # 삭제 뒤 로컬 차단이 끝나기 전에는 아무것도 보내지 않는다(Sol H-03)
        state["error_code"] = "deletion_cleanup_pending"
        return finish("blocked_gate")
    gate_block = _gate_result(_gate_check())
    if gate_block:
        return finish(gate_block)
    ctx = store.active_context()
    if ctx is None:
        return finish("needs_auth")
    epoch = ctx.get("writer_epoch")
    if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 1:
        state["error_code"] = "writer_epoch_missing"  # 지어낸 epoch(예전 `or 1`)로 보내지 않는다
        return finish("blocked_gate")
    scopes = _scopes(ctx)
    control, wait_until, last_error = _control_gate(store, scopes)
    if control == "cooldown":
        state["error_code"], state["next_attempt_at"] = last_error, _iso(wait_until)
        return finish("cooldown")
    owner = f"run:{run_id}:{trigger}"
    if not store.acquire_lease("upload", owner, LEASE_SECONDS):
        return finish("busy_other_run")
    try:
        now = _iso(_now())
        with store.transaction() as tx:  # 죽은 실행이 남긴 in_flight(만료 lease)만 되돌린다 — attempt 는 그대로
            tx.execute("UPDATE outbox SET state='retry_wait', next_retry_at=?, lease_owner=NULL, lease_until=NULL"
                       " WHERE state='in_flight' AND (lease_until IS NULL OR lease_until < ?)", (now, now))
        if trigger in ("manual", "midnight", "recovery"):
            try:
                _enqueue_missing(trigger, data_dir)
            except Exception:
                _log.info("[community] enqueue 실패", exc_info=True)
        probing = control == "probe"
        if probing:
            _control_probing(store, scopes)
        return _drain(store, run_id, owner, trigger, ctx, scopes, probing, counts, request_ids, state, finish, report)
    finally:
        try:
            store.release_lease("upload", owner)
        except Exception:
            pass


def _drain(store, run_id, owner, trigger, ctx, scopes, probing, counts, request_ids, state, finish, report) -> dict:
    conn = store.connect()
    started = _monotonic()
    run = {"owner": owner,
           "queue": [],          # 이분한 배치(같은 event_id·payload 로 다시 보냄)
           "succeeded": False,   # 이번 실행에서 형식이 유효한 ACK 를 받았는가(= envelope·연결은 정상)
           "suspects": [],       # 모호한 400/422 를 단건으로 받은 행(대조 요청 결과로 판정)
           "single_next": False}
    tried: set[str] = set()  # 이번 실행에서 결과가 정해진 행(다시 고르지 않음)
    refreshed = False
    had_problem = False
    budget_hit = False
    last_request_at: float | None = None
    while True:
        if counts["requests"] >= RUN_MAX_REQUESTS or _monotonic() - started >= RUN_MAX_SECONDS:
            budget_hit = True
            wake()  # 예산을 다 썼다 — 남은 것은 곧바로 이어서(요청 간격은 다음 실행도 지킨다)
            break
        if run["queue"]:
            batch = run["queue"].pop(0)
        else:
            rows = _front_rows(conn, store.active_context() or ctx, _iso(_now()), PAGE_ROWS, tried)
            if not rows:
                break
            single = probing or run["single_next"]
            batch, oversize = _build_batch(store, rows, ctx, trigger, 1 if single else MAX_EVENTS_PER_REQUEST)
            if oversize:
                for row in oversize:
                    _dead_letter(store, [row], row["_reason"])
                    tried.add(row["event_id"])
                counts["dead"] += len(oversize)
                had_problem = True
            if not batch:
                if oversize:
                    continue
                break
        if last_request_at is not None:
            _sleep(MIN_REQUEST_INTERVAL - (_monotonic() - last_request_at))
        if not store.renew_lease("upload", owner, LEASE_SECONDS):  # 요청 직전 heartbeat — 소유권을 잃었으면 보내지 않는다
            state["error_code"] = "lease_lost"
            _hold_suspects(store, run, state)
            return finish("busy_other_run")
        response, interp = _send(store, owner, batch, ctx, trigger, counts, request_ids)
        last_request_at = _monotonic()
        if interp is not None and interp.error_class == "auth_required" and response.http_status == 401 \
                and not refreshed and response.token:
            refreshed = True
            token = _force_refresh(response.token)
            if isinstance(token, str):
                # 재전송도 새 요청이다: 요청 간격을 지킨 뒤 요청 직전에 소유권 heartbeat
                _sleep(MIN_REQUEST_INTERVAL - (_monotonic() - last_request_at))
                if not store.renew_lease("upload", owner, LEASE_SECONDS):
                    with store.transaction() as tx:  # 아직 내 것인 행만 되돌린다
                        _retry_rows(tx, batch, "lease_lost", None, owner=owner)
                    state["error_code"] = "lease_lost"
                    _hold_suspects(store, run, state)
                    return finish("busy_other_run")
                response, interp = _send(store, owner, batch, ctx, trigger, counts, request_ids, token=token)
                last_request_at = _monotonic()
            else:
                interp = token  # 갱신 결과(offline 또는 auth_required)
        for row in batch:
            tried.add(row["event_id"])
        if interp is None:  # 형식이 유효한 200 → 이벤트별 판정
            ack = policy.interpret_ack([r["event_id"] for r in batch], _ack_input(response))
            if ack.kind == "error":
                interp = ack
            else:
                _apply_ack(store, batch, ack, counts)
                report(f"진행: 전송 {counts['sent']}건, 확인 {counts['acked']}건, 재시도 {counts['retry']}건")
                for scope in scopes.values():
                    _control_mark(store, scope, state="ready")
                probing = False
                run["succeeded"], run["single_next"] = True, False
                if run["suspects"]:  # 같은 envelope 로 다른 이벤트가 저장됐다 → 앞서 거절된 단건은 그 이벤트 문제
                    for row, code in run["suspects"]:
                        _dead_letter(store, [row], code)
                    counts["dead"] += len(run["suspects"])
                    run["suspects"] = []
                if ack.missing or counts["dead"] or counts["blocked"]:
                    had_problem = True
                continue
        outcome = _apply_error(store, batch, interp, scopes, counts, state, run)
        report(f"진행: 전송 {counts['sent']}건, 확인 {counts['acked']}건, 재시도 {counts['retry']}건"
               + (f" ({state['error_code']})" if state['error_code'] else ""))
        if outcome == "continue":
            continue  # 이분·대조는 문제가 아니다 — 최종 결과는 격리(dead)·차단·재시도 집계로 정한다
        _hold_suspects(store, run, state)
        return finish(outcome)
    if run["suspects"]:  # 대조할 다른 이벤트가 없었다 — 원인을 모르므로 버리지 않고 보류
        _hold_suspects(store, run, state)
        return finish("failed")
    if counts["requests"] == 0 and not counts["dead"]:
        return finish("not_due" if _sendable_count(conn, ctx) else "no_pending")
    remaining = _sendable_count(conn, ctx)
    if remaining and (budget_hit or _front_rows(conn, ctx, _iso(_now()), 1, set())):
        return finish("more_pending")  # 자정 key 를 끝났다고 적지 않는다(Sol 계획 검토 4)
    if had_problem or counts["retry"] or counts["dead"] or counts["blocked"]:
        return finish("partial")
    return finish("sent" if not remaining else "not_due")


def _hold_suspects(store, run, state) -> None:
    """모호한 400/422 단건들: 원인이 envelope(공통)인지 이벤트인지 모르면 버리지 않고 백오프로 보류한다.
    아직 이 실행이 잡고 있는 in_flight 행만 바꾼다(lease 를 잃은 뒤 새 실행이 회수한 행을 덮지 않게)."""
    if not run["suspects"]:
        return
    with store.transaction() as tx:
        for row, code in run["suspects"]:
            tx.execute("UPDATE outbox SET state='retry_wait', next_retry_at=?, last_error_code=?, lease_owner=NULL,"
                       " lease_until=NULL WHERE event_id=? AND state='in_flight' AND lease_owner=?",
                       (_backoff_at(int(row.get("attempt_count") or 1)), code, row["event_id"], run["owner"]))
    state["error_code"] = state.get("error_code") or run["suspects"][0][1]
    run["suspects"] = []


def _send(store, owner, batch, ctx, trigger, counts, request_ids, token=None):
    """실제 HTTP 요청 1회. 요청 전 토큰 확보(실패면 보내지 않음·attempt 미집계) → in_flight+attempt → 전송."""
    from services import community_ingest_client as client
    from services import community_auth_service as cas
    if token is None:
        try:
            token = cas.get_access_token()
        except cas.CommunityAuthError as exc:
            cls = "offline" if exc.code == "auth_unavailable" else "auth_required"
            response = client.IngestResponse(ok=False, http_status=None, code=exc.code, error_class=cls, not_sent=True)
            return response, _classify(response)
        except Exception:
            token = None
    envelope = _envelope(ctx, trigger, [row["_event"] for row in batch])
    if len(client.envelope_bytes(envelope)) > MAX_BODY_BYTES:
        response = client.IngestResponse(ok=False, http_status=None, code="payload_too_large",
                                         error_class="request_too_large", not_sent=True)
        return response, _classify(response)
    _mark_in_flight(store, batch, owner)
    counts["requests"] += 1
    counts["sent"] += len(batch)
    response = client.post_envelope(envelope, token=token, now=_now())
    if response.request_id:
        request_ids.append(response.request_id)
    if getattr(response, "not_sent", False):  # 전송 계층이 보내지 않았다 — 집계를 되돌린다
        counts["requests"] -= 1
        counts["sent"] -= len(batch)
        with store.transaction() as tx:
            for row in batch:
                tx.execute("UPDATE outbox SET attempt_count=MAX(0, attempt_count-1) WHERE event_id=?", (row["event_id"],))
                row["attempt_count"] = max(0, int(row.get("attempt_count") or 1) - 1)
    return response, _classify(response)


def _force_refresh(rejected: str):
    """401 을 받은 토큰으로 실제 강제 갱신. 성공이면 새 토큰, 아니면 판정(offline/auth_required)."""
    from services import community_auth_service as cas
    try:
        token = cas.get_access_token(rejected=rejected)
    except cas.CommunityAuthError as exc:
        cls = "offline" if exc.code == "auth_unavailable" else "auth_required"
        return policy._error(cls, None, code=exc.code)
    except TypeError:  # rejected 인자를 모르는 대체 구현(테스트)
        return policy._error("auth_required", 401, code="auth_required")
    if not token or token == rejected:
        return policy._error("auth_required", 401, code="auth_required")
    return token


def _apply_error(store, batch, interp, scopes, counts, state, run) -> str:
    """요청 단위 오류 → 행 상태·전송 제어. 반환: 'continue'(이분·대조 계속) 또는 실행 결과 코드."""
    cls, code = interp.error_class, interp.code or interp.error_class
    state["error_code"] = code
    if cls in ("request_too_large", "payload_invalid"):
        ambiguous = cls == "payload_invalid" and code in policy.AMBIGUOUS_PAYLOAD_CODES
        if len(batch) > 1:  # 같은 event_id·payload 로 반씩 다시(단건만 격리)
            half = len(batch) // 2
            with store.transaction() as tx:
                for row in batch:
                    tx.execute("UPDATE outbox SET state='pending', lease_owner=NULL, lease_until=NULL WHERE event_id=?",
                               (row["event_id"],))
            run["queue"].insert(0, batch[half:])
            run["queue"].insert(0, batch[:half])
            return "continue"
        if ambiguous and not run["succeeded"]:
            # 단건 schema_invalid/invalid_request 인데 이번 실행에서 정상 저장된 것이 없다 → envelope 공통 문제일 수 있다.
            run["suspects"].append((batch[0], code))
            if len(run["suspects"]) >= 2:  # 다른 이벤트도 같은 거절 → 공통 문제: 버리지 않고 모두 보류(_hold_suspects)
                return "failed"
            run["single_next"] = True  # 다음 이벤트 하나로 대조
            return "continue"
        _dead_letter(store, batch, code if cls == "payload_invalid" else "payload_too_large")
        counts["dead"] += len(batch)
        return "continue"
    if cls in policy.RETRYABLE_CLASSES:
        with store.transaction() as tx:
            _retry_rows(tx, batch, code, interp.hint, interp.request_id)
        counts["retry"] += len(batch)
        until = _control_mark(store, scopes[policy.RETRYABLE_CLASSES[cls]], state="cooling_down", error_code=code,
                              hint=interp.hint)
        state["next_attempt_at"] = _iso(until) if until else None
        return "failed" if cls == "request_rejected" else "cooldown"  # 남은 배치는 보내지 않는다
    if cls == "auth_required":
        with store.transaction() as tx:
            for row in batch:
                tx.execute("UPDATE outbox SET state='auth_required', last_error_code=?, next_retry_at=?,"
                           " lease_owner=NULL, lease_until=NULL WHERE event_id=?",
                           (code, _backoff_at(int(row.get("attempt_count") or 1)), row["event_id"]))
        counts["retry"] += len(batch)
        _gate_invalidate(code)
        return "needs_auth"
    # consent_rejected / connection_rejected: 명시적 거절 → blocked(보존), 게이트 무효화
    with store.transaction() as tx:
        for row in batch:
            tx.execute("UPDATE outbox SET state='blocked', last_error_code=?, lease_owner=NULL, lease_until=NULL"
                       " WHERE event_id=?", (code, row["event_id"]))
            tx.execute("UPDATE source_journal SET blocked_reason=? WHERE event_id=?", (f"blocked:{code}", row["event_id"]))
    counts["blocked"] += len(batch)
    _gate_invalidate(code)
    return "needs_consent" if cls == "consent_rejected" else "blocked_gate"


# ── 재시도 실행기 ────────────────────────────────────────────────────────────

def next_due_at(data_dir=None) -> datetime | None:
    """다음에 깨어날 시각: 신고별 가장 앞 대기 행의 next_retry_at 과 전송 제어 next_attempt_at 중 가장 이른 것.
    전송 제어가 cooling_down 이면 그 시각 전에는 깨지 않는다."""
    store = _store(data_dir)
    ctx = store.active_context()
    if ctx is None:
        return None
    conn = store.connect()
    control, wait_until, _ = _control_gate(store, _scopes(ctx))
    if control == "cooldown":
        return wait_until
    row = conn.execute(
        "SELECT MIN(COALESCE(o.next_retry_at, '')) FROM source_journal j JOIN outbox o ON o.event_id=j.event_id"
        " JOIN (SELECT j2.source_report_id AS rid, MIN(j2.source_revision) AS rev FROM source_journal j2"
        "       JOIN outbox o2 ON o2.event_id=j2.event_id WHERE o2.state IN ('pending','retry_wait','in_flight','auth_required')"
        + _CTX_FILTER.replace("j.", "j2.") + " GROUP BY j2.source_report_id) f"
        " ON f.rid=j.source_report_id AND f.rev=j.source_revision"
        " WHERE o.state IN ('pending','retry_wait','auth_required')" + _CTX_FILTER,
        (*_ctx_args(ctx), *_ctx_args(ctx))).fetchone()
    value = row[0] if row else None
    if value is None:
        return None
    return _now() if value == "" else _parse(value)


def _refresh_next_due(data_dir, result: dict | None) -> None:
    """실행 뒤 다음 깨울 시각을 계산한다. 게이트·인증 대기는 시각으로 깨우지 않는다(게이트가 바뀌면 data_version 이 깨운다).
    다른 실행이 잡고 있으면 5초 뒤, 실패면 60초 뒤 다시 본다."""
    global _next_due
    outcome = (result or {}).get("result")
    if outcome in ("needs_auth", "needs_consent", "blocked_gate"):
        _next_due = None
        return
    if outcome == "busy_other_run":
        _next_due = _now() + timedelta(seconds=5)
        return
    due = next_due_at(data_dir)
    if outcome == "failed":
        fallback = _now() + timedelta(seconds=60)
        due = fallback if due is None or due < fallback else due
    _next_due = due


def wake() -> None:
    _wake_event.set()


def start_background(data_dir=None) -> None:
    """수집·게이트가 community.db 를 바꾸면(data_version) 실시간 업로드, 가장 이른 재시도 시각이 되면 복구 업로드.
    행마다 타이머를 두지 않고 1초 주기 확인 하나로 처리한다."""
    global _bg_thread, _next_due
    if _bg_thread is not None and _bg_thread.is_alive():
        return
    _bg_stop.clear()
    try:
        _next_due = next_due_at(data_dir)
    except Exception:
        _next_due = None

    def loop() -> None:
        global _last_data_version
        store = _store(data_dir)
        try:
            _last_data_version = store.data_version()
        except Exception:
            _last_data_version = None
        while not _bg_stop.is_set():
            woke = _wake_event.wait(timeout=1.0)
            if _bg_stop.is_set():
                break  # 종료 중에는 새 실행을 시작하지 않는다
            trigger = None
            if woke:
                _wake_event.clear()
                trigger = "realtime"
            else:
                try:
                    version = store.data_version()
                except Exception:
                    version = _last_data_version
                if _last_data_version is not None and version != _last_data_version:
                    trigger = "realtime"
                elif _next_due is not None and _now() >= _next_due:
                    trigger = "recovery"
            if trigger is None:
                continue
            try:
                from services.community_crawl_upload import log_background_progress
                log_background_progress("실시간 공유 자료 업로드 중...")
                request_upload(trigger, data_dir, progress=log_background_progress)
            except Exception:
                _log.info("[community] background upload 실패", exc_info=True)
            try:
                _last_data_version = store.data_version()  # 자기 기록으로 다시 깨지 않게
            except Exception:
                pass

    _bg_thread = threading.Thread(target=loop, name="community-upload-watch", daemon=True)
    _bg_thread.start()


def stop_background(timeout: float = 5.0) -> None:
    global _bg_thread
    _bg_stop.set()
    _wake_event.set()
    thread = _bg_thread
    if thread is not None and thread is not threading.current_thread():
        thread.join(timeout)
    _bg_thread = None


# ── 상태(지도 패널) ──────────────────────────────────────────────────────────

legacy_result = policy.legacy_result


def _kst(value) -> str | None:
    dt = _parse(value)
    return (dt + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M") if dt else None


def upload_status(data_dir=None) -> dict:
    """지도 패널용. 현재 context 계정의 것만 집계한다(UC-1 §1-7). 원문·토큰 없음."""
    store = _store(data_dir)
    conn = store.connect()
    ctx = store.active_context()
    fingerprint = (ctx or {}).get("contributor_fingerprint")
    last = conn.execute("SELECT * FROM upload_runs ORDER BY started_at DESC LIMIT 1").fetchone()
    states = {s: 0 for s in ("pending", "retry_wait", "in_flight", "auth_required", "blocked", "dead_letter")}
    blocked: dict[str, int] = {}
    oldest = next_retry = last_ack = None
    projections: dict[str, int] = {}
    quarantined = stored = 0
    if fingerprint:
        for row in conn.execute("SELECT o.state AS s, COUNT(*) AS n FROM outbox o JOIN source_journal j ON j.event_id=o.event_id"
                                " WHERE j.contributor_fingerprint IS ? GROUP BY o.state", (fingerprint,)):
            states[row["s"]] = row["n"]
        for row in conn.execute(
                "SELECT COALESCE(j.blocked_reason, o.last_error_code, 'unknown') AS reason, COUNT(*) AS n"
                " FROM outbox o JOIN source_journal j ON j.event_id=o.event_id"
                " WHERE o.state IN ('blocked','dead_letter') AND j.contributor_fingerprint IS ?"
                " GROUP BY reason", (fingerprint,)).fetchall():
            blocked[row["reason"]] = row["n"]
        row = conn.execute("SELECT MIN(j.captured_at), MIN(o.next_retry_at) FROM outbox o JOIN source_journal j"
                           " ON j.event_id=o.event_id WHERE o.state IN ('pending','retry_wait','in_flight','auth_required')"
                           " AND j.contributor_fingerprint IS ?", (fingerprint,)).fetchone()
        oldest, next_retry = row[0], row[1]
        acked = conn.execute("SELECT MAX(acked_at), SUM(ack_status='quarantined'), SUM(ack_status IS NOT NULL)"
                             " FROM source_journal WHERE contributor_fingerprint IS ?", (fingerprint,)).fetchone()
        last_ack, quarantined, stored = acked[0], int(acked[1] or 0), int(acked[2] or 0)
        for row in conn.execute(
                "SELECT j.projection_status AS p, COUNT(*) AS n FROM source_journal j"
                " WHERE j.contributor_fingerprint IS ? AND j.projection_status IS NOT NULL"
                " GROUP BY p", (fingerprint,)).fetchall():
            projections[row["p"]] = row["n"]
    control = {"state": "ready", "until_kst": None, "reason": None}
    if ctx:
        mode, until, reason = _control_gate(store, _scopes(ctx))
        if mode == "cooldown":
            control = {"state": "cooling_down", "until_kst": _kst(_iso(until)), "reason": reason}
        elif mode == "probe":
            control = {"state": "probing", "until_kst": None, "reason": None}
    candidates = reshare_candidates(data_dir) if fingerprint else 0
    last_upload_kst = _kst(last["finished_at"]) if last else None
    try:
        request_ids = json.loads(last["request_ids"]) if last and last["request_ids"] else []
    except ValueError:
        request_ids = []
    return {
        "last_upload_kst": last_upload_kst,
        "last_result": legacy_result(last["result"]) if last else None,
        "last_outcome": (last["result"] if last else None),
        "last_request": (request_ids[-1][:8] if request_ids else None),
        "pending": states["pending"] + states["retry_wait"] + states["in_flight"],
        "states": states,
        "auth_required": states["auth_required"],
        "needs_attention": states["blocked"] + states["dead_letter"],
        "blocked": blocked,
        "quarantined": quarantined,
        "stored": stored,
        "oldest_unsent_kst": _kst(oldest),
        "next_retry_kst": _kst(next_retry),
        "last_central_ack_kst": _kst(last_ack),
        "control": control,
        "projections": projections,
        "reshare_candidates": candidates,
        "next_midnight_kst": _next_midnight_label(),
        "has_journal": bool(conn.execute(
            "SELECT 1 FROM source_journal WHERE contributor_fingerprint IS ? LIMIT 1",
            (fingerprint,)).fetchone()) if fingerprint else False,
    }


def _next_midnight_label() -> str:
    from services.community_schedule import next_due_at as _next_midnight
    due = _next_midnight(_now())
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
    from services import community_capture as _cap
    if _cap.deletion_cleanup_pending(data_dir):
        return {"result": "blocked", "count": 0, "error_code": "deletion_cleanup_pending"}
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
    """manifest 전 페이지를 받아 검증 후 한 트랜잭션으로 server_completed 를 교체한다. 실패면 False(fail-closed).

    - 받는 동안 upload lease 를 잡아 자기 업로드로 세대(manifest_token)가 바뀌지 않게 한다.
    - 모든 페이지의 manifest_token 이 같고, 받은 개수 = total, 중복 없음, 페이지의 dataset_key·writer_epoch 가
      현재 연결과 같을 때만 교체. 토큰이 바뀌면 처음부터 다시(최대 3회).
    """
    import uuid as _uuid

    from services import community_ingest_client as client
    store = _store(data_dir)
    ctx = store.active_context()
    if ctx is None or not ctx.get("dataset_key") or not ctx.get("connection_id"):
        return False
    dataset_key = ctx["dataset_key"]
    owner = f"manifest:{_uuid.uuid4()}"
    if not store.acquire_lease("upload", owner, LEASE_SECONDS):
        return False
    try:
        seen: list[str] | None = None
        for _ in range(3):
            keys: list[str] = []
            token: str | None = None
            total: int | None = None
            after: str | None = None
            consistent = True
            while True:
                ok, body = client.post_manifest(connection_id=ctx["connection_id"], after=after, limit=limit)
                if not ok:
                    return False
                if body.get("dataset_key") != dataset_key or body.get("writer_epoch") != ctx.get("writer_epoch"):
                    return False
                if token is None:
                    token, total = body["manifest_token"], body["total"]
                elif body["manifest_token"] != token or body["total"] != total:
                    consistent = False
                    break
                keys.extend(body["key_prefixes"])
                after = body.get("next_after")
                if after is None:
                    break
            if consistent:
                if len(keys) != total or len(set(keys)) != len(keys):
                    return False
                seen = keys
                break
        if seen is None:
            return False
        now = _iso(_now())
        with store.transaction() as tx:
            tx.execute("DELETE FROM server_completed WHERE dataset_key=?", (dataset_key,))
            for prefix in seen:
                tx.execute("INSERT INTO server_completed(dataset_key, key_prefix, fetched_at) VALUES (?, ?, ?)",
                           (dataset_key, prefix, now))
            store.set_meta("manifest_scope", f"{dataset_key}:{ctx.get('writer_epoch')}", tx)
        return True
    finally:
        try:
            store.release_lease("upload", owner)
        except Exception:
            pass


def on_contributions_deleted(data_dir=None, deletion_id: str | None = None) -> None:
    from services import community_capture as _cap
    _cap.on_contributions_deleted(data_dir, deletion_id=deletion_id)


def outbox_size_warning(data_dir=None, limit_bytes: int = 200 * 1024 * 1024) -> bool:
    """community.db 파일이 limit 초과면 True (자동 삭제는 하지 않음)."""
    store = _store(data_dir)
    try:
        return store.path and __import__("os").path.getsize(store.path) > limit_bytes
    except OSError:
        return False
