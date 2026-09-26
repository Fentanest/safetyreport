"""PC 커뮤니티 공유 DTO 확정 + community.db 불변 저장 (contracts/community-ingest/observation-v1).

규칙 정본: contracts/community-ingest/observation.md 2~4절, canonical-json.md.
순수 함수(build_adapter_input/build_payload/canonical_json/payload_sha256/is_eligible/decide_event)는
vectors/observations.json 전 case/event_decisions 와 일치해야 한다.

capture()는 CommunityStore.transaction() 한 번 안에서 detail_status UPSERT → prev 조회 →
이벤트 결정 → journal INSERT → (context active 면) outbox INSERT → report_latest UPSERT →
revision 증가를 수행한다. 이벤트가 없으면 journal 을 쓰지 않는다.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone

STATUS_MAP = {
    "수용": "accepted",
    "일부수용": "partial",
    "불수용": "rejected",
    "답변완료": "completed_unknown",
    "기타": "completed_unknown",
    "취하": "withdrawn",
    "이송": "transferred",
    "보완요청": "supplement",
    "처리중": "processing",
}
ELIGIBLE = {"accepted", "partial", "rejected", "completed_unknown"}
PARSER_VERSION = "pc-parser-1"
SCHEMA_VERSION = 1

_AMOUNT_RE = re.compile(r"^(과태료|범칙금):\s*(.+?)\s*원$")
_POINTS_RE = re.compile(r"^벌점:\s*([0-9]{1,4})\s*점$")
_WS_RE = re.compile(r"\s+", re.UNICODE)


class CaptureStoreUnavailable(RuntimeError):
    """community.db 를 쓸 수 없어 수집을 멈춰야 한다는 신호 (한 실행 연속 3회 실패)."""


@dataclass(frozen=True)
class CaptureResult:
    event_id: str | None
    event_type: str | None
    eligible: bool
    payload_sha256: str


# ── 순수 함수 ────────────────────────────────────────────────────────────────

def _clean(value, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = "".join(" " if (ord(ch) < 0x20 or ord(ch) == 0x7F) else ch for ch in value)
    text = _WS_RE.sub(" ", text).strip()
    if not text:
        return None
    return text[:limit]


def _parse_day(value) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()[:10]
    candidate = text
    if re.fullmatch(r"\d{4}\.\d{2}\.\d{2}", text):
        candidate = text.replace(".", "-")
    elif re.fullmatch(r"\d{8}", text):
        candidate = f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    elif not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        # 시각이 붙은 값("YYYY-MM-DD HH:MM" 등)은 앞 날짜만
        head = text.split()[0] if text.split() else ""
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", head):
            candidate = head
        elif re.fullmatch(r"\d{4}\.\d{2}\.\d{2}", head):
            candidate = head.replace(".", "-")
        else:
            return None
    try:
        year, month, day = int(candidate[0:4]), int(candidate[5:7]), int(candidate[8:10])
        date(year, month, day)
    except (ValueError, IndexError):
        return None
    return candidate


def _parse_amount(raw) -> tuple[int | None, str]:
    """(confirmed_won, kind). kind 는 앞머리로 판정, 금액은 문법 일치 때만."""
    text = raw if isinstance(raw, str) else ""
    stripped = text.strip()
    kind = "unknown"
    if stripped.startswith("범칙금"):
        kind = "penalty"
    elif stripped.startswith("과태료"):
        kind = "fine"
    match = _AMOUNT_RE.match(text.strip())
    if not match:
        return None, kind
    number = match.group(2)
    if re.fullmatch(r"[0-9]+", number):
        digits = number
    elif re.fullmatch(r"[0-9]{1,3}(?:[,.][0-9]{3})+", number):
        digits = re.sub(r"[,.]", "", number)
    else:
        return None, kind
    try:
        amount = int(digits)
    except ValueError:
        return None, kind
    if amount > 100_000_000:
        return None, kind
    return amount, kind


def _parse_points(raw) -> int | None:
    text = raw if isinstance(raw, str) else ""
    match = _POINTS_RE.match(text.strip())
    if not match:
        return None
    try:
        points = int(match.group(1))
    except ValueError:
        return None
    return points if points <= 1000 else None


def _canonical_double(value) -> str | None:
    """double 최단 왕복 10진 문자열 (지수 표기 없음, 소수점 없으면 .0)."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    import math as _math
    if _math.isnan(number) or _math.isinf(number):
        return None
    text = repr(number)
    if "e" in text or "E" in text:
        text = format(number, ".17f").rstrip("0")
        if text.endswith("."):
            text += "0"
    if "." not in text:
        text += ".0"
    return text


def _parse_location(geocode) -> dict:
    if not isinstance(geocode, dict) or geocode.get("status") != "ok":
        return {"lat": None, "lng": None, "source": "none"}
    lat = _canonical_double(geocode.get("lat"))
    lng = _canonical_double(geocode.get("lng"))
    if lat is None or lng is None:
        return {"lat": None, "lng": None, "source": "none"}
    try:
        flat, flng = float(lat), float(lng)
    except ValueError:
        return {"lat": None, "lng": None, "source": "none"}
    if not (32 <= flat <= 39.5 and 124 <= flng <= 132):
        return {"lat": None, "lng": None, "source": "none"}
    # 왕복 검증: 정규형으로 다시 해석해도 같은 값
    if _canonical_double(lat) != lat or _canonical_double(lng) != lng:
        return {"lat": None, "lng": None, "source": "none"}
    return {"lat": lat, "lng": lng, "source": "geocode"}


def build_adapter_input(detail: dict, title_fields: dict | None = None,
                        entry_value: str | None = None, geo: dict | None = None,
                        progress_status: str | None = None) -> dict:
    """observation.md 2절 PC 열 → 플랫폼 중립 입력. geo 는 _prefetch_derived() 값만."""
    detail = detail or {}
    title_fields = title_fields or {}
    geo = geo or {}
    report_date = title_fields.get("신고일")
    if report_date is None:
        report_date = detail.get("신고일")
    return {
        "processing_status": detail.get("처리상태"),
        "penalty_amount": detail.get("범칙금_과태료"),
        "report_date": report_date,
        "response_date": detail.get("답변일"),
        "processing_agency": detail.get("처리기관"),
        "person_in_charge": detail.get("담당자"),
        "car_number": detail.get("차량번호"),
        "violation_location": detail.get("위반장소"),
        "entry_value": entry_value,
        "penalty_points": detail.get("벌점"),
        "geocode": {
            "status": geo.get("지오코딩상태"),
            "lat": geo.get("위도"),
            "lng": geo.get("경도"),
        },
        # payload 에는 넣지 않고 detail_status 기록용
        "progress_status": progress_status,
    }


def build_payload(adapter_input: dict) -> dict:
    """observation.md 3절 그대로. 입력 외 값을 읽지 않는 순수 함수."""
    status_raw = _clean(adapter_input.get("processing_status"), 40)
    status = STATUS_MAP.get(status_raw or "", "other")
    eligible = status in ELIGIBLE
    entry_value = adapter_input.get("entry_value") if isinstance(adapter_input.get("entry_value"), str) else ""
    if "자동차·교통위반" in entry_value:
        category = "traffic"
    elif "불법주정차신고" in entry_value:
        category = "parking"
    else:
        category = "other"
    report_date = _parse_day(adapter_input.get("report_date"))
    response_day = _parse_day(adapter_input.get("response_date"))
    completed_date = response_day if eligible else None
    confirmed_won, kind = _parse_amount(adapter_input.get("penalty_amount"))
    penalty_points = _parse_points(adapter_input.get("penalty_points"))
    raw_amount = adapter_input.get("penalty_amount") if isinstance(adapter_input.get("penalty_amount"), str) else ""
    amount_head = raw_amount.strip()
    if status == "rejected":
        disposition = "none"
    elif amount_head.startswith("범칙금"):
        disposition = "penalty"
    elif amount_head.startswith("과태료"):
        disposition = "fine"
    elif amount_head.startswith("경고"):
        disposition = "warning"
    else:
        disposition = "unknown"
    return {
        "address": _clean(adapter_input.get("violation_location"), 200),
        "agency_name": _clean(adapter_input.get("processing_agency"), 200),
        "amount": {"confirmed_won": confirmed_won, "kind": kind, "penalty_points": penalty_points},
        "category": category,
        "completed_date": completed_date,
        "disposition": disposition,
        "location": _parse_location(adapter_input.get("geocode")),
        "manager_name": _clean(adapter_input.get("person_in_charge"), 160),
        "report_date": report_date,
        "status": status,
        "status_raw": status_raw,
        "vehicle_raw": _clean(adapter_input.get("car_number"), 64),
    }


def canonical_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(payload: dict) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def is_eligible(payload: dict) -> bool:
    return payload.get("status") in ELIGIBLE


def decide_event(prev: dict | None, payload: dict) -> str | None:
    """prev = {'payload_sha256': str, 'eligible': bool} 또는 None.

    eligible 이면 prev 가 없고(첫 관측) payload 가 같지 않은 한 completed_observation,
    not eligible 이면 prev 가 eligible 일 때만 status_correction.
    """
    eligible = is_eligible(payload)
    if prev is None:
        return "completed_observation" if eligible else None
    if eligible:
        if prev.get("eligible") and prev.get("payload_sha256") == payload_sha256(payload):
            return None
        return "completed_observation"
    if prev.get("eligible"):
        return "status_correction"
    return None


# ── 저장 ─────────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _store(data_dir: str | None = None):
    from services.community_store import CommunityStore
    return CommunityStore.open(data_dir)


def _project_namespace() -> str:
    from services.community_store import project_namespace
    try:
        from services import community_auth_service as cas
        url = cas.load_config_from_settings().supabase_url
    except Exception:
        url = ""
    return project_namespace(url)


def _server_key_prefix(source_report_id: str) -> str:
    return hashlib.sha256(f"safetyreport|{source_report_id}".encode("utf-8")).hexdigest()[:24]


def _rebuild_run_id(explicit: str | None) -> str | None:
    if explicit:
        return explicit
    return os.environ.get("SAFETYREPORT_REBUILD_RUN_ID") or None


def capture(adapter_input: dict, *, source_report_id: str, trigger: str,
            rebuild_run_id: str | None = None, data_dir: str | None = None) -> CaptureResult:
    """한 트랜잭션으로 detail_status → journal/outbox → report_latest 를 기록한다."""
    store = _store(data_dir)
    payload = build_payload(adapter_input)
    sha = payload_sha256(payload)
    eligible = is_eligible(payload)
    progress = adapter_input.get("progress_status")
    progress_label = progress if isinstance(progress, str) and progress else None
    run_id = _rebuild_run_id(rebuild_run_id)
    local_dataset_id = store.local_dataset_id()
    now = _now_iso()

    with store.transaction() as tx:
        if progress_label:
            tx.execute(
                "INSERT INTO detail_status(local_dataset_id, source_report_id, c_now_label, observed_at)"
                " VALUES (?, ?, ?, ?) ON CONFLICT(local_dataset_id, source_report_id)"
                " DO UPDATE SET c_now_label=excluded.c_now_label, observed_at=excluded.observed_at",
                (local_dataset_id, source_report_id, progress_label, now))
        prev = None
        if run_id:
            row = tx.execute(
                "SELECT event_id, payload_sha256, eligible FROM report_latest_staging"
                " WHERE run_id=? AND source_report_id=?", (run_id, source_report_id)).fetchone()
            if row is not None:
                journal = tx.execute(
                    "SELECT payload_sha256, eligible FROM source_journal WHERE event_id=?",
                    (row["event_id"],)).fetchone()
                if journal is not None:
                    prev = {"payload_sha256": journal["payload_sha256"], "eligible": bool(journal["eligible"])}
                else:
                    prev = {"payload_sha256": row["payload_sha256"], "eligible": bool(row["eligible"])}
        if prev is None:
            row = tx.execute(
                "SELECT event_id, payload_sha256, eligible FROM report_latest"
                " WHERE local_dataset_id=? AND source_report_id=?",
                (local_dataset_id, source_report_id)).fetchone()
            if row is not None:
                prev = {"payload_sha256": row["payload_sha256"], "eligible": bool(row["eligible"])}
        if prev is None:
            ctx_row = tx.execute("SELECT dataset_key FROM context WHERE id=1").fetchone()
            dataset_key = ctx_row["dataset_key"] if ctx_row and ctx_row["dataset_key"] else None
            if dataset_key:
                hit = tx.execute(
                    "SELECT 1 FROM server_completed WHERE dataset_key=? AND key_prefix=?",
                    (dataset_key, _server_key_prefix(source_report_id))).fetchone()
                if hit is not None:
                    prev = {"payload_sha256": None, "eligible": True}
        event_type = decide_event(prev, payload)
        if event_type is None:
            return CaptureResult(event_id=None, event_type=None, eligible=eligible, payload_sha256=sha)

        ctx = tx.execute("SELECT * FROM context WHERE id=1").fetchone()
        active = ctx is not None and ctx["state"] == "active"
        revision = store.next_revision(tx)
        event_id = str(uuid.uuid4())
        namespace = _project_namespace()
        payload_text = canonical_json(payload)
        tx.execute(
            "INSERT INTO source_journal(event_id, project_namespace, local_dataset_id, dataset_key,"
            " source_report_id, source_revision, event_type, captured_at, capture_trigger, rebuild_run_id,"
            " schema_version, parser_version, payload_json, payload_sha256, eligible,"
            " contributor_fingerprint, connection_id, writer_epoch, consent_grant_id,"
            " personal_save_state, blocked_reason)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
            (event_id, namespace, local_dataset_id,
             ctx["dataset_key"] if ctx else None, source_report_id, revision, event_type, now, trigger, run_id,
             SCHEMA_VERSION, PARSER_VERSION, payload_text, sha, 1 if eligible else 0,
             ctx["contributor_fingerprint"] if ctx else None,
             ctx["connection_id"] if ctx else None,
             ctx["writer_epoch"] if ctx else None,
             ctx["consent_grant_id"] if ctx else None,
             None if active else "no_active_context"))
        if active:
            tx.execute(
                "INSERT INTO outbox(event_id, state, attempt_count, enqueued_trigger, enqueued_at)"
                " VALUES (?, 'pending', 0, ?, ?)", (event_id, trigger, now))
        if run_id:
            tx.execute(
                "INSERT INTO report_latest_staging(run_id, source_report_id, event_id, payload_sha256, eligible)"
                " VALUES (?, ?, ?, ?, ?) ON CONFLICT(run_id, source_report_id)"
                " DO UPDATE SET event_id=excluded.event_id, payload_sha256=excluded.payload_sha256,"
                " eligible=excluded.eligible",
                (run_id, source_report_id, event_id, sha, 1 if eligible else 0))
        else:
            tx.execute(
                "INSERT INTO report_latest(local_dataset_id, source_report_id, event_id,"
                " payload_sha256, eligible, source_generation)"
                " VALUES (?, ?, ?, ?, ?, 0) ON CONFLICT(local_dataset_id, source_report_id)"
                " DO UPDATE SET event_id=excluded.event_id, payload_sha256=excluded.payload_sha256,"
                " eligible=excluded.eligible",
                (local_dataset_id, source_report_id, event_id, sha, 1 if eligible else 0))
    try:
        from services import community_uploader as _uploader
        _uploader.wake()
    except Exception:
        pass
    return CaptureResult(event_id=event_id, event_type=event_type, eligible=eligible, payload_sha256=sha)


def mark_personal_save(event_id: str | None, ok: bool, data_dir: str | None = None) -> None:
    if not event_id:
        return
    store = _store(data_dir)
    with store.transaction() as tx:
        tx.execute("UPDATE source_journal SET personal_save_state=? WHERE event_id=?",
                   ("saved" if ok else "failed", event_id))


# ── 재시도 의도 파일 (local-store.md S-03) ────────────────────────────────────

def _retry_path(data_dir: str | None = None) -> str:
    from settings import settings as _settings
    base = data_dir or _settings.datapath
    return os.path.join(base, "community_capture_retry.json")


def capture_retry_ids(data_dir: str | None = None) -> set[str]:
    try:
        with open(_retry_path(data_dir), encoding="utf-8") as fh:
            items = json.load(fh)
    except (FileNotFoundError, ValueError, OSError):
        return set()
    return {str(it.get("source_report_id")) for it in items if isinstance(it, dict) and it.get("source_report_id")}


def _write_retry_list(items: list[dict], data_dir: str | None = None) -> None:
    path = _retry_path(data_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(items, fh, ensure_ascii=False)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def add_retry_id(source_report_id: str, reason: str, data_dir: str | None = None) -> None:
    path = _retry_path(data_dir)
    try:
        with open(path, encoding="utf-8") as fh:
            items = json.load(fh)
    except (FileNotFoundError, ValueError, OSError):
        items = []
    items = [it for it in items if isinstance(it, dict) and it.get("source_report_id") != source_report_id]
    items.append({"source_report_id": source_report_id, "reason": reason,
                  "failed_at": _now_iso(), "attempts": 1})
    _write_retry_list(items, data_dir)


def remove_retry_id(source_report_id: str, data_dir: str | None = None) -> None:
    path = _retry_path(data_dir)
    try:
        with open(path, encoding="utf-8") as fh:
            items = json.load(fh)
    except (FileNotFoundError, ValueError, OSError):
        return
    kept = [it for it in items if not (isinstance(it, dict) and it.get("source_report_id") == source_report_id)]
    if len(kept) != len(items):
        _write_retry_list(kept, data_dir)


# ── 삭제 알림 (T3 라우트가 contributions-delete 성공 뒤 호출) ──────────────────

DELETION_MARKER = "community_deletion_pending.json"


def _deletion_marker_path(data_dir: str | None = None) -> str:
    import settings.settings as app_settings

    return os.path.join(data_dir or app_settings.datapath, DELETION_MARKER)


def on_contributions_deleted(data_dir: str | None = None, deletion_id: str | None = None) -> None:
    """중앙 삭제 성공 뒤: 그 시점에 있던 journal 행 전부(행 순번 경계 — 시계와 무관)를 영구 제외하고
    outbox 대기 행을 막고 server_completed 를 비운다. 먼저 영속 표시(파일)를 남기고, 적용이 끝나면 지운다.
    적용이 실패하면 예외를 올리고 표시는 남는다 → 업로드·reshare 는 표시를 먼저 처리할 때까지 보내지 않는다(Sol H-03)."""
    boundary = None
    try:
        row = _store(data_dir).connect().execute("SELECT max(rowid) AS m FROM source_journal").fetchone()
        boundary = row["m"] if row else None
    except Exception:
        boundary = None  # 읽을 수 없으면 적용 시점의 전체 행을 막는다(보수적)
    path = _deletion_marker_path(data_dir)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"deletion_id": deletion_id, "journal_rowid_max": boundary, "recorded_at": _now_iso()}, fh)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    apply_pending_deletion(data_dir)


def apply_pending_deletion(data_dir: str | None = None) -> bool:
    """남은 삭제 표시가 없으면 True. 있으면 적용하고 표시를 지운다. 적용 실패는 예외."""
    path = _deletion_marker_path(data_dir)
    if not os.path.exists(path):
        return True
    try:
        with open(path, encoding="utf-8") as fh:
            marker = json.load(fh)
        if not isinstance(marker, dict):
            raise ValueError
    except ValueError:
        marker = {}  # 손상된 표시: 경계 없이 적용 시점의 전체 행을 막는다(영구 잠김 대신 보수적 복구)
    store = _store(data_dir)
    with store.transaction() as tx:
        boundary = marker.get("journal_rowid_max")
        if boundary is None:
            boundary = (tx.execute("SELECT max(rowid) AS m FROM source_journal").fetchone() or {"m": 0})["m"] or 0
        tx.execute("UPDATE source_journal SET blocked_reason='deleted_by_user'"
                   " WHERE rowid <= ? AND (blocked_reason IS NULL OR blocked_reason != 'deleted_by_user')", (boundary,))
        tx.execute("UPDATE outbox SET state='blocked', last_error_code='deleted_by_user'"
                   " WHERE state != 'dead_letter' AND event_id IN (SELECT event_id FROM source_journal WHERE rowid <= ?)",
                   (boundary,))
        tx.execute("DELETE FROM server_completed")
    os.remove(path)
    return True


def deletion_cleanup_pending(data_dir: str | None = None) -> bool:
    """삭제 뒤 로컬 차단이 아직 끝나지 않았으면 다시 적용해 본다. 여전히 못 하면 True(업로드 금지)."""
    try:
        return not apply_pending_deletion(data_dir)
    except Exception:
        return True
