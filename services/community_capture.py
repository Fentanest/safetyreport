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
PARSER_VERSION = "pc-parser-4"  # 2026-09-28 observation-v4(rating)
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


# observation.md §3(서버 edge·DB·JSON 스키마와 동일 상한, 세 층 1:1).
AGENCY_CODE_LIMIT = 32
#: 길이 초과 원문 기관코드의 명시적 거절 사유(조용히 null 로 버리지 않음 — REVIEW3 낮음-1).
AGENCY_CODE_TOO_LONG = "source_agency_code_too_long"


def _clean_code(value) -> str | None:
    """원문 기관코드 정리: 공백 정리만 하고 자르지 않는다.

    길이 상한 초과여도 여기서 null 로 버리지 않는다(조용한 손실 금지).
    전송 payload 에는 상한 이내일 때만 싣고(build_payload), 초과분은
    capture() 가 journal/outbox 에 명시적 사유로 기록한다.
    일반 텍스트 필드는 _clean() 절단을 그대로 쓴다.
    """
    if not isinstance(value, str):
        return None
    text = "".join(" " if (ord(ch) < 0x20 or ord(ch) == 0x7F) else ch for ch in value)
    text = _WS_RE.sub(" ", text).strip()
    if not text:
        return None
    return text


def agency_code_too_long(value) -> bool:
    """정리된 원문 기관코드가 계약 상한을 초과하는지."""
    text = _clean_code(value)
    return text is not None and len(text) > AGENCY_CODE_LIMIT


def _payload_agency_code(value) -> str | None:
    """전송 payload 용 기관코드: 상한 이내의 정리 원문, 초과·없음은 None.

    초과분을 None 으로 두는 것은 전송 형태 안전장치이며, 명시적 거절 기록은
    capture() 가 담당한다(조용한 손실이 아님).
    """
    text = _clean_code(value)
    if text is None or len(text) > AGENCY_CODE_LIMIT:
        return None
    return text


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
        "report_number": title_fields.get("신고번호") or detail.get("신고번호"),
        "penalty_amount": detail.get("범칙금_과태료"),
        "report_date": report_date,
        "response_date": detail.get("답변일"),
        "processing_agency": detail.get("처리기관"),
        # observation-v3(2026-09-28): 선택 답변의 C_MANAGE_ORG 원문(TEXT). 없으면 None(명시적 NULL).
        "agency_code": detail.get("처리기관코드"),
        "person_in_charge": detail.get("담당자"),
        "car_number": detail.get("차량번호"),
        "violation_location": detail.get("위반장소"),
        "entry_value": entry_value,
        "penalty_points": detail.get("벌점"),
        # observation-v2(2026-09-28): 파서가 처리내용에서 뽑은 법 이름·조항(처리내용 원문은 보내지 않는다)
        "violation_law": detail.get("위반법규"),
        "rating": title_fields.get("별점"),
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
        "violation_law": _clean(adapter_input.get("violation_law"), 60),
        "rating": adapter_input.get("rating") if type(adapter_input.get("rating")) is int and 1 <= adapter_input["rating"] <= 5 else None,
        # v3: 원문 기관코드 그대로(TEXT·선행 0 보존). 신규 형식도 자르지 않고, 없으면 null.
        # 상한 초과분은 여기서 null 로 두되(전송 형태 안전), capture() 가 명시적
        # 사유(blocked:source_agency_code_too_long)로 기록한다 — 조용히 버리지 않음.
        "source_agency_code": _payload_agency_code(adapter_input.get("agency_code")),
    }


def canonical_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(payload: dict) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def is_eligible(payload: dict) -> bool:
    return payload.get("status") in ELIGIBLE


def decide_event(prev: dict | None, payload: dict) -> str | None:
    """prev = {'payload_sha256': str, 'eligible': bool} 또는 None.

    2026-09-28: 답변 완료(eligible) 관측만 이벤트를 만든다. 적격이 아닌 관측(처리중·보완요청·취하·이송·other)은
    prev 와 무관하게 이벤트 없음(`status_correction` 발급 중단). eligible 이면 prev 가 없고(첫 관측) payload 가
    같지 않은 한 completed_observation.
    """
    eligible = is_eligible(payload)
    if not eligible:
        return None
    if prev is None:
        return "completed_observation"
    if prev.get("eligible") and prev.get("payload_sha256") == payload_sha256(payload):
        return None
    return "completed_observation"


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
    # REVIEW3 낮음-1: 상한 초과 원문 기관코드는 조용히 null 로 버리지 않고 명시적
    # 거절한다. 원문은 크롤 DB(처리기관코드)에 그대로 있고, journal/outbox 에
    # 사유를 기록한다(서버 edge·모바일과 동일 사유 문자열).
    code_blocked = agency_code_too_long(adapter_input.get("agency_code"))
    report_number = adapter_input.get("report_number")
    report_number = report_number.strip() if isinstance(report_number, str) and report_number.strip() else None
    eligible = is_eligible(payload)
    progress = adapter_input.get("progress_status")
    progress_label = progress if isinstance(progress, str) and progress else None
    run_id = _rebuild_run_id(rebuild_run_id)
    local_dataset_id = store.local_dataset_id()
    now = _now_iso()

    with store.transaction() as tx:
        if run_id and os.environ.get('SAFETYREPORT_CRAWL_RUN_ID'):
            from services.community_rebuild import assert_current_attempt
            assert_current_attempt(tx, run_id)
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
                    "SELECT payload_sha256, eligible, report_number, blocked_reason FROM source_journal WHERE event_id=?",
                    (row["event_id"],)).fetchone()
                if journal is not None:
                    prev = {"payload_sha256": journal["payload_sha256"],
                            "eligible": bool(journal["eligible"]), "report_number": journal["report_number"],
                            "blocked_reason": journal["blocked_reason"]}
                else:
                    prev = {"payload_sha256": row["payload_sha256"], "eligible": bool(row["eligible"])}
        if prev is None:
            # 2026-09-28 계정 규칙: prev 는 현 계정(dataset_key·fingerprint)의 최신 journal 행이다.
            # 파일 단위 report_latest 포인터를 그대로 쓰면 계정 전환 뒤 B의 제출이 건너뛰어진다.
            ctx_row = tx.execute("SELECT dataset_key, contributor_fingerprint FROM context WHERE id=1").fetchone()
            ctx_dataset = ctx_row["dataset_key"] if ctx_row else None
            ctx_fp = ctx_row["contributor_fingerprint"] if ctx_row else None
            row = tx.execute(
                "SELECT event_id, payload_sha256, eligible, report_number, blocked_reason FROM source_journal"
                " WHERE local_dataset_id=? AND source_report_id=? AND dataset_key IS ?"
                " AND contributor_fingerprint IS ? ORDER BY source_revision DESC LIMIT 1",
                (local_dataset_id, source_report_id, ctx_dataset, ctx_fp)).fetchone()
            if row is not None:
                prev = {"payload_sha256": row["payload_sha256"], "eligible": bool(row["eligible"]),
                        "report_number": row["report_number"], "blocked_reason": row["blocked_reason"]}
        # 2026-09-28: server_completed 로 prev 를 합성하지 않는다(비적격 관측은 정정을 발급하지 않음).
        # server_completed 표·manifest 신선도 검사는 그대로 유지한다.
        if prev and report_number and prev.get("report_number") != report_number:
            prev = {**prev, "payload_sha256": None}
        event_type = decide_event(prev, payload)
        # REVIEW4 낮음: 길이 초과 원문 코드는 payload가 직전과 같아도 명시적
        # 거절 이벤트를 만든다. 초과분은 payload에서 None으로 두어 sha가 같아
        # decide_event이 None을 내지만, 사유 없이는 원문 코드가 조용히 버려진
        # 것처럼 보인다. 이미 같은 sha·같은 blocked 사유로 기록됐으면 quiet 유지.
        if event_type is None and eligible:
            prev_blocked = (prev or {}).get("blocked_reason") == f"blocked:{AGENCY_CODE_TOO_LONG}"
            # REVIEW5: 같은 payload 해시라도 차단 원문을 NULL로 고쳤으면
            # 차단된 journal 뒤에 전송 가능한 새 관측을 발급해야 한다.
            if code_blocked != prev_blocked:
                event_type = "completed_observation"
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
            " source_report_id, report_number, source_revision, event_type, captured_at, capture_trigger, rebuild_run_id,"
            " schema_version, parser_version, payload_json, payload_sha256, eligible,"
            " contributor_fingerprint, connection_id, writer_epoch, consent_grant_id,"
            " personal_save_state, blocked_reason)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
            (event_id, namespace, local_dataset_id,
             ctx["dataset_key"] if ctx else None, source_report_id, report_number, revision, event_type, now, trigger, run_id,
             SCHEMA_VERSION, PARSER_VERSION, payload_text, sha, 1 if eligible else 0,
             ctx["contributor_fingerprint"] if ctx else None,
             ctx["connection_id"] if ctx else None,
             ctx["writer_epoch"] if ctx else None,
             ctx["consent_grant_id"] if ctx else None,
             f"blocked:{AGENCY_CODE_TOO_LONG}" if code_blocked
             else (None if active else "no_active_context")))
        if active:
            if code_blocked:
                tx.execute(
                    "INSERT INTO outbox(event_id, state, attempt_count, enqueued_trigger, enqueued_at,"
                    " last_error_code) VALUES (?, 'blocked', 0, ?, ?, ?)",
                    (event_id, trigger, now, AGENCY_CODE_TOO_LONG))
            else:
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
    return CaptureResult(event_id=event_id, event_type=event_type, eligible=eligible, payload_sha256=sha)


def mark_personal_save(event_id: str | None, ok: bool, data_dir: str | None = None) -> None:
    if not event_id:
        return
    store = _store(data_dir)
    with store.transaction() as tx:
        tx.execute("UPDATE source_journal SET personal_save_state=? WHERE event_id=?",
                   ("saved" if ok else "failed", event_id))
    if ok:
        try:
            from services import community_uploader as _uploader
            _uploader.wake()
        except Exception:
            pass


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

DELETION_KEY_PREFIX = "deletion_pending:"


def begin_deletion(data_dir: str | None = None) -> str:
    """중앙 삭제를 요청하기 **전에** community.db 에 'prepared' 표시를 트랜잭션으로 남긴다(journal 과 같은 DB).
    못 쓰면 예외 → 호출자는 중앙 삭제를 요청하지 않는다. 표시마다 고유 id(동시 삭제가 서로를 덮지 않음)."""
    deletion_id = str(uuid.uuid4())
    store = _store(data_dir)
    with store.transaction() as tx:
        store.set_meta(DELETION_KEY_PREFIX + deletion_id, json.dumps({"state": "prepared", "at": _now_iso()}), tx)
    return deletion_id


def cancel_deletion(deletion_id: str, data_dir: str | None = None) -> None:
    """중앙이 삭제를 **확실히 거절**했을 때만(4xx): 그 prepared 표시 하나를 지운다. 응답 불명이면 부르지 않는다."""
    store = _store(data_dir)
    with store.transaction() as tx:
        tx.execute("DELETE FROM meta WHERE key=? AND json_extract(value, '$.state')='prepared'",
                   (DELETION_KEY_PREFIX + deletion_id,))


def confirm_deletion(data_dir: str | None = None) -> None:
    """중앙 삭제 성공 뒤: 모든 prepared 표시를 confirmed 로 바꾸고(삭제는 그 시점까지 전부를 지운다) 곧바로 적용한다.
    적용이 실패해도 confirmed 표시는 남아 업로드·reshare 를 막고, 다음 적용 시도에서 처리된다."""
    store = _store(data_dir)
    with store.transaction() as tx:
        tx.execute("UPDATE meta SET value=json_set(value, '$.state', 'confirmed', '$.confirmed_at', ?) WHERE key LIKE ?",
                   (_now_iso(), DELETION_KEY_PREFIX + "%"))
    apply_pending_deletion(data_dir)


def apply_pending_deletion(data_dir: str | None = None) -> bool:
    """confirmed 표시가 있으면 **한 트랜잭션에서** 적용하고 지운다: 그 시점까지의 journal 전부(행 순번 — 시계 무관)
    deleted_by_user, 그 outbox blocked, server_completed 비움. prepared 표시는 건드리지 않는다(중앙 결과 전 — Sol 3차 H-03c).
    남은 표시가 하나도 없으면 True, prepared 가 남아 있으면 False."""
    store = _store(data_dir)
    with store.transaction() as tx:
        rows = tx.execute("SELECT key, json_extract(value, '$.state') AS state FROM meta WHERE key LIKE ?",
                          (DELETION_KEY_PREFIX + "%",)).fetchall()
        confirmed = [r["key"] for r in rows if r["state"] == "confirmed"]
        if confirmed:
            boundary = (tx.execute("SELECT max(rowid) AS m FROM source_journal").fetchone()["m"]) or 0
            tx.execute("UPDATE source_journal SET blocked_reason='deleted_by_user'"
                       " WHERE rowid <= ? AND (blocked_reason IS NULL OR blocked_reason != 'deleted_by_user')", (boundary,))
            tx.execute("UPDATE outbox SET state='blocked', last_error_code='deleted_by_user'"
                       " WHERE state != 'dead_letter' AND event_id IN (SELECT event_id FROM source_journal WHERE rowid <= ?)",
                       (boundary,))
            tx.execute("DELETE FROM server_completed")
            tx.executemany("DELETE FROM meta WHERE key=?", [(k,) for k in confirmed])
        return len(rows) == len(confirmed)


def deletion_state(data_dir: str | None = None) -> str | None:
    """화면용: None(없음) / 'unconfirmed'(중앙 결과 불명 — 다시 요청 필요) / 'cleanup_pending'(적용 대기)."""
    try:
        rows = _store(data_dir).connect().execute(
            "SELECT json_extract(value, '$.state') AS state FROM meta WHERE key LIKE ?", (DELETION_KEY_PREFIX + "%",)).fetchall()
    except Exception:
        return "cleanup_pending"
    states = {r["state"] for r in rows}
    return "unconfirmed" if "prepared" in states else ("cleanup_pending" if states else None)


def on_contributions_deleted(data_dir: str | None = None, deletion_id: str | None = None) -> None:
    """중앙 삭제 성공 뒤(구 호출 호환). 표시가 없으면 만들고 확정·적용한다."""
    if deletion_id is None:
        begin_deletion(data_dir)
    confirm_deletion(data_dir)


def deletion_cleanup_pending(data_dir: str | None = None) -> bool:
    """삭제 표시가 남아 있으면 True(업로드·reshare 금지). confirmed 는 적용을 시도하고, prepared(중앙 결과 전·불명)는 그대로 둔다."""
    try:
        return not apply_pending_deletion(data_dir)
    except Exception:
        return True
