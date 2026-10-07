"""업로드 상태 조회·재공유·서버 완료 목록(manifest) 갱신(EO R-10에서 community_uploader 에서 분리).

전송 루프·outbox 전이는 services/community_uploader.py 에 있다. 업로더의 이름(_now·_store·request_upload 등)은
호출 시점에 `up.` 으로 찾아 쓴다 — 시험·대체 구현이 업로더 모듈의 이름을 바꿔도 같은 것을 쓰게.
"""
from __future__ import annotations

import json
import uuid
from datetime import timedelta

from services import community_uploader as up


def _kst(value) -> str | None:
    dt = up._parse(value)
    return (dt + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M") if dt else None


def upload_status(data_dir=None) -> dict:
    """지도 패널용. 현재 context 계정의 것만 집계한다(UC-1 §1-7). 원문·토큰 없음."""
    store = up._store(data_dir)
    conn = store.connect()
    ctx = store.context()
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
        mode, until, reason = up._control_gate(store, up._scopes(ctx))
        if mode == "cooldown":
            control = {"state": "cooling_down", "until_kst": _kst(up._iso(until)), "reason": reason}
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
        "last_result": up.legacy_result(last["result"]) if last else None,
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
    due = _next_midnight(up._now())
    kst = due + timedelta(hours=9)
    return kst.strftime("%Y-%m-%d %H:%M")


def reshare_candidates(data_dir=None) -> int:
    store = up._store(data_dir)
    ctx = store.active_context()
    if ctx is None:
        return 0
    conn = store.connect()
    local_id = store.local_dataset_id()
    # 2026-09-28 계정 규칙: reshare 후보는 현 계정의 최신 eligible 행만 본다.
    # 타 계정 행을 현 연결로 rebind하여 전송하지 않는다.
    rows = conn.execute(
        "SELECT source_report_id, MAX(source_revision) AS rev FROM source_journal"
        " WHERE local_dataset_id=? AND eligible=1 AND dataset_key IS ? AND contributor_fingerprint IS ?"
        " GROUP BY source_report_id", (local_id, ctx.get("dataset_key"), ctx.get("contributor_fingerprint"))).fetchall()
    count = 0
    for item in rows:
        row = conn.execute(
            "SELECT consent_grant_id, connection_id, blocked_reason, ack_status FROM source_journal"
            " WHERE local_dataset_id=? AND source_report_id=? AND source_revision=?"
            " AND dataset_key IS ? AND contributor_fingerprint IS ?"
            " ORDER BY source_revision DESC LIMIT 1",
            (local_id, item["source_report_id"], item["rev"],
             ctx.get("dataset_key"), ctx.get("contributor_fingerprint"))).fetchone()
        if row is None or row["blocked_reason"]:
            continue
        if row["consent_grant_id"] == ctx.get("consent_grant_id") \
                and row["connection_id"] == ctx.get("connection_id"):
            continue
        count += 1
    return count


def request_reshare(data_dir=None, *, consent_grant=None, send=True, only_waiting=False) -> dict:
    """최신 eligible 행을 새 event_id·revision·reshare 로 재발급 후 업로드."""
    from services.community_capture import canonical_json as _cj
    from services import community_capture as _cap
    if _cap.deletion_cleanup_pending(data_dir):
        return {"result": "blocked", "count": 0, "error_code": "deletion_cleanup_pending"}
    store = up._store(data_dir)
    ctx = store.active_context()
    if ctx is None:
        return {"result": "auth_required", "count": 0}
    local_id = store.local_dataset_id()
    created: list[str] = []
    with store.transaction() as tx:
        # Explicit grant recovery includes preserved journals before same-account
        # personal DB restoration. Fingerprint and official dataset stay mandatory.
        scope = "dataset_key IS ? AND contributor_fingerprint IS ?"
        params = [ctx.get('dataset_key'), ctx.get('contributor_fingerprint')]
        if not consent_grant:
            scope += " AND local_dataset_id=?"
            params.append(local_id)
        rows = tx.execute(
            "SELECT * FROM (SELECT j.*, ROW_NUMBER() OVER (PARTITION BY source_report_id"
            " ORDER BY source_revision DESC, rowid DESC) AS candidate_rank FROM source_journal j"
            " WHERE eligible=1 AND " + scope + ") WHERE candidate_rank=1", params).fetchall()
        for row in rows:
            # A tombstone belongs to the logical report, across local DB rotations.
            deleted = tx.execute(
                "SELECT 1 FROM source_journal WHERE source_report_id=? AND dataset_key IS ?"
                " AND contributor_fingerprint IS ? AND blocked_reason IN ('deleted_by_user','deleted','blocked:deleted') LIMIT 1",
                (row['source_report_id'], ctx.get('dataset_key'), ctx.get('contributor_fingerprint'))).fetchone()
            if deleted:
                continue
            reason = row["blocked_reason"]
            if only_waiting and (row["ack_status"] is not None or reason not in ('consent:unknown', 'consent:none', 'consent:revoked', 'consent:outdated')):
                continue
            consent_period = reason in ("no_active_context", "consent:revoked", "consent:none", "consent:unknown", "consent:outdated")
            if reason and not (consent_grant and consent_period):
                continue
            origin = "consent:" + consent_grant if consent_grant else "reshare"
            if consent_grant:
                if consent_grant != ctx.get("consent_grant_id") or row["capture_trigger"] == origin:
                    continue
                if not consent_period and row["consent_grant_id"] == consent_grant and row["connection_id"] == ctx.get("connection_id"):
                    continue
            elif row["consent_grant_id"] == ctx.get("consent_grant_id") and row["connection_id"] == ctx.get("connection_id"):
                continue
            revision = store.next_revision(tx)
            event_id = str(uuid.uuid4())
            now = up._iso(up._now())
            tx.execute(
                "INSERT INTO source_journal(event_id, project_namespace, local_dataset_id, dataset_key,"
                " source_report_id, report_number, source_revision, event_type, captured_at, capture_trigger,"
                " schema_version, parser_version, payload_json, payload_sha256, eligible,"
                " contributor_fingerprint, connection_id, writer_epoch, consent_grant_id, personal_save_state)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, 'reshare', ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, 'pending')",
                (event_id, row["project_namespace"], local_id, ctx.get("dataset_key"),
                 row["source_report_id"], row["report_number"], revision, row["captured_at"], origin,
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
    result = up.request_upload("reshare", data_dir) if send else {"result": "queued"}
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
    store = up._store(data_dir)
    ctx = store.active_context()
    if ctx is None or not ctx.get("dataset_key") or not ctx.get("connection_id"):
        return False
    dataset_key = ctx["dataset_key"]
    owner = f"manifest:{_uuid.uuid4()}"
    if not store.acquire_lease("upload", owner, up.LEASE_SECONDS):
        return False
    try:
        seen: list[str] | None = None
        deadline = up._monotonic() + 180
        for _ in range(3):
            keys: list[str] = []
            token: str | None = None
            total: int | None = None
            after: str | None = None
            consistent = True
            cursors = set()
            pages = 0
            while True:
                pages += 1
                if pages > 10000 or up._monotonic() >= deadline or not store.renew_lease('upload', owner, up.LEASE_SECONDS):
                    return False
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
                if after in cursors:
                    return False
                cursors.add(after)
            if consistent:
                if len(keys) != total or len(set(keys)) != len(keys):
                    return False
                seen = keys
                break
        if seen is None:
            return False
        now = up._iso(up._now())
        with store.transaction() as tx:
            lease = tx.execute("SELECT owner, until FROM leases WHERE name='upload'").fetchone()
            active = store.active_context()
            if not lease or lease['owner'] != owner or lease['until'] <= now or active != ctx:
                return False
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


def outbox_size_warning(data_dir=None, limit_bytes: int = 200 * 1024 * 1024) -> bool:
    """community.db 파일이 limit 초과면 True (자동 삭제는 하지 않음)."""
    store = up._store(data_dir)
    try:
        return store.path and __import__("os").path.getsize(store.path) > limit_bytes
    except OSError:
        return False
