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

from core.utils.fallback import note_fallback
from services import fine_estimate, report_category
from services.law_values import primary_law

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
PARSER_VERSION = "pc-parser-13"  # 2026-10-17 상세 원본 기반 재파싱. 기존 journal amount는 자동 재파싱하지 않음
SCHEMA_VERSION = 1

_AMOUNT_RE = re.compile(rf"^(과태료|범칙금):\s*({fine_estimate.AMOUNT_TOKEN_PATTERN})\s*원$")
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


def _parse_amount(raw, record: dict | None = None) -> tuple[int | None, str]:
    """앞머리/전체 문법은 기존 계약, 숫자 해석은 fine_estimate 정본."""
    text = raw if isinstance(raw, str) else ""
    stripped = text.strip()
    kind = "penalty" if stripped.startswith("범칙금") else "fine" if stripped.startswith("과태료") else "unknown"
    match = _AMOUNT_RE.match(stripped)
    if not match:
        return None, kind
    legal = fine_estimate.legal_fine_amounts(record) if kind == "fine" else ()
    amount = fine_estimate.parse_amount_number(match.group(2), legal)
    return (amount if amount is not None and amount <= 100_000_000 else None), kind


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
                        progress_status: str | None = None, *,
                        ssnpc_cd: str | None = None, ssnpc_type: str | None = None,
                        report_name: str | None = None,
                        photo_first_at: str | None = None, photo_last_at: str | None = None) -> dict:
    """observation.md 2절 PC 열 → 플랫폼 중립 입력. geo 는 _prefetch_derived() 값만.

    report_name/photo_first_at/photo_last_at(fine-estimate-contract.md, v6): payload 에는 싣지 않고
    fine_estimate 판정 입력에만 쓴다(로컬 통계와 같은 열 — services/fine_estimate.FINE_ESTIMATE_FIELDS).
    report_name·사진 촬영 시각은 detail/title_fields 에 없을 수 있어(목록만 갱신된 상세 등) 호출자가
    기존 값을 넘길 수 있게 명시 인자로 뒀다 — 없으면 title_fields['신고명']으로 떨어진다.
    """
    detail = detail or {}
    title_fields = title_fields or {}
    geo = geo or {}
    report_date = title_fields.get("신고일")
    if report_date is None:
        report_date = detail.get("신고일")
    if report_name is None:
        report_name = title_fields.get("신고명")
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
        # observation-v5(2026-10-11): 안전신문고 분류 코드. category 는 코드가 있으면 코드로 정한다.
        "ssnpc_cd": ssnpc_cd,
        "ssnpc_type": ssnpc_type,
        "penalty_points": detail.get("벌점"),
        # observation-v2(2026-09-28): 파서가 처리내용에서 뽑은 법 이름·조항(처리내용 원문은 보내지 않는다)
        "violation_law": primary_law(detail.get("위반법규")),
        "rating": title_fields.get("별점"),
        "geocode": {
            "status": geo.get("지오코딩상태"),
            "lat": geo.get("위도"),
            "lng": geo.get("경도"),
        },
        # v6(2026-10-12): fine_estimate 판정 입력만(payload 로는 나가지 않음).
        "report_name": report_name,
        "occurrence_time": detail.get("발생시각"),
        "photo_first_at": photo_first_at,
        "photo_last_at": photo_last_at,
        # payload 에는 넣지 않고 detail_status 기록용
        "progress_status": progress_status,
    }


def _fine_record(adapter_input: dict, category: str) -> dict:
    """추정·금액 판정 입력(services/fine_estimate.FINE_ESTIMATE_FIELDS, 로컬 통계와 같은 열).

    일반 캡처(build_payload)와 재전송·사진 보충(_backfill_payload)이 같은 어댑터 입력에서 같은 레코드를 만든다.
    """
    return {
        "category": category,
        "entry_value": adapter_input.get("entry_value") if isinstance(adapter_input.get("entry_value"), str) else "",
        "ssnpc_cd": adapter_input.get("ssnpc_cd"),
        "ssnpc_type": adapter_input.get("ssnpc_type"),
        "신고명": adapter_input.get("report_name"),
        "위반법규": adapter_input.get("violation_law"),
        "차량번호": adapter_input.get("car_number"),
        "사진_첫촬영": adapter_input.get("photo_first_at"),
        "사진_끝촬영": adapter_input.get("photo_last_at"),
        "발생시각": adapter_input.get("occurrence_time"),
    }


def _category(adapter_input: dict) -> str:
    entry_value = adapter_input.get("entry_value") if isinstance(adapter_input.get("entry_value"), str) else ""
    # observation-v5: 5분류(contracts/report-categories.json). 코드가 있으면 코드, 없으면 접수 메뉴 문구.
    return report_category.category_of(adapter_input.get("ssnpc_cd"), adapter_input.get("ssnpc_type"), entry_value)


def _disposition(status: str, raw_amount) -> str:
    amount_head = (raw_amount if isinstance(raw_amount, str) else "").strip()
    if status == "rejected":
        return "none"
    if amount_head.startswith("범칙금"):
        return "penalty"
    if amount_head.startswith("과태료"):
        return "fine"
    if amount_head.startswith("경고"):
        return "warning"
    return "unknown"


def _fine_estimate_field(adapter_input: dict, fine_record: dict) -> dict | None:
    """v6(2026-10-12, fine-estimate-contract.md): 로컬 통계가 '추정 과태료'로 세는 신고만, 같은 입력·같은 판정
    (services/fine_estimate.estimate_if_unknown = services/stats/metrics.py `_metric_unknown_fine` 조건 + estimate()).
    대상이 아니면 None(호출자가 키 자체를 넣지 않는다 — null 금지)."""
    fine = fine_estimate.estimate_if_unknown(adapter_input.get("penalty_amount"), fine_record)
    if fine is None:
        return None
    return {"won": fine["amount"], "rule": fine["rule"], "rule_version": fine_estimate.RULE_VERSION}


def build_payload(adapter_input: dict) -> dict:
    """observation.md 3절 그대로. 입력 외 값을 읽지 않는 순수 함수."""
    status_raw = _clean(adapter_input.get("processing_status"), 40)
    status = STATUS_MAP.get(status_raw or "", "other")
    eligible = status in ELIGIBLE
    category = _category(adapter_input)
    report_date = _parse_day(adapter_input.get("report_date"))
    response_day = _parse_day(adapter_input.get("response_date"))
    completed_date = response_day if eligible else None
    fine_record = _fine_record(adapter_input, category)
    confirmed_won, kind = _parse_amount(adapter_input.get("penalty_amount"), fine_record)
    penalty_points = _parse_points(adapter_input.get("penalty_points"))
    payload = {
        "address": _clean(adapter_input.get("violation_location"), 200),
        "agency_name": _clean(adapter_input.get("processing_agency"), 200),
        "amount": {"confirmed_won": confirmed_won, "kind": kind, "penalty_points": penalty_points},
        "category": category,
        "completed_date": completed_date,
        "disposition": _disposition(status, adapter_input.get("penalty_amount")),
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
    fine = _fine_estimate_field(adapter_input, fine_record)
    if fine is not None:
        payload["fine_estimate"] = fine
    return payload


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


def read_account_context(store) -> tuple:
    """capture() 가 쓰는 (계정 문맥, 동의 상태). 계정 상태 파일을 읽으므로 반복 호출자는 한 번만 부른다."""
    from services import community_cloud
    try:
        return community_cloud.capture_context(store.context())
    except Exception as exc:
        note_fallback("community_capture.read_account_context", exc)
        return None, 'unverified_owner'


def _is_legacy_combined_kind(journal_kind, raw_amount) -> bool:
    """예전 모바일 파서가 같은 원문에서 낸 `combined`(두 낱말이 함께 있는 원문)인지(모바일 isLegacyCombinedKind 와 같음).

    계약상 combined 는 예약값이라 지금은 어느 쪽도 만들지 않는다. 그런 journal 을 보충이 금액 판정 불일치로 보지 않게
    한다 — journal amount 는 보존하고 다음 실시간 수집 때만 kind 가 바뀐다. PC 는 combined 를 만든 적이 없지만
    서버·모바일 판정 함수를 같게 둔다.
    """
    text = (raw_amount if isinstance(raw_amount, str) else "").strip()
    return journal_kind == "combined" and "범칙금" in text and "과태료" in text


def _backfill_payload(latest_payload: dict, adapter_input: dict) -> dict:
    """journal 최신 payload 를 보존하고 fine_estimate 만 일반 캡처와 같은 판정으로 다시 정한다.

    adapter_input 은 개인 DB 현재 행으로 만든 어댑터 입력(_resend_adapter_input)이다. 추정 판정은 build_payload 와
    같은 함수(_fine_estimate_field = estimate_if_unknown)를 같은 입력(범칙금_과태료 원문·위반법규·분류·사진 시각 등)에
    적용한다. 단, 그 원문을 지금 규칙으로 읽은 금액 판정(amount.kind·confirmed_won, disposition)이 journal 의 것과
    같을 때만 적용한다 — 다르면 개인 DB 가 journal 과 어긋났거나(개인 저장 지연) 금액표 보정으로 확정 금액이 새로
    읽힌 것이므로 추정을 싣지 않는다(PROJECT_RULES 3-2). journal 의 amount 는 바꾸지 않는다(확정 금액 갱신은 다음
    실시간 상세 수집이 맡는다).
    """
    payload = dict(latest_payload)
    payload.pop("fine_estimate", None)
    record = _fine_record(adapter_input, _category(adapter_input))
    raw = adapter_input.get("penalty_amount")
    confirmed_won, kind = _parse_amount(raw, record)
    amount = payload.get("amount") or {}
    journal_kind = amount.get("kind")
    if _is_legacy_combined_kind(journal_kind, raw):
        journal_kind = kind
    current = (confirmed_won, kind, _disposition(payload.get("status"), raw))
    if current != (amount.get("confirmed_won"), journal_kind, payload.get("disposition")):
        return payload
    fine = _fine_estimate_field(adapter_input, record)
    if fine is not None:
        payload["fine_estimate"] = fine
    return payload


def capture(adapter_input: dict, *, source_report_id: str, trigger: str,
            rebuild_run_id: str | None = None, data_dir: str | None = None,
            account_context: tuple | None = None) -> CaptureResult:
    """한 트랜잭션으로 detail_status → journal/outbox → report_latest 를 기록한다.

    account_context: 여러 신고를 한 번에 보충하는 호출자(resend_fine_estimate_updates)가 실행 시작 때 한 번 읽은
    (context, authorization) — 신고마다 계정 상태 파일을 다시 읽지 않는다. None 이면 여기서 읽는다.
    """
    store = _store(data_dir)
    backfill = trigger == "fine_estimate_backfill"
    payload = {} if backfill else build_payload(adapter_input)
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
    if account_context is not None:
        captured_context, authorization = account_context
    else:
        captured_context, authorization = read_account_context(store)

    with store.transaction() as tx:
        if run_id and os.environ.get('SAFETYREPORT_CRAWL_RUN_ID'):
            from services.community_rebuild import assert_current_attempt
            assert_current_attempt(tx, run_id)
        if trigger == "reparse" or trigger.startswith("reparse"):
            # 재파싱은 옛 관측의 재해석이지 새 수집이 아니다 — 사용자가 지운 관측(deleted_by_user)을
            # 되살리지 않는다(보충 경로와 같은 검사). 발급 트랜잭션 안에서 확인한다.
            deleted = tx.execute(
                "SELECT 1 FROM report_latest l JOIN source_journal j ON j.event_id=l.event_id"
                " WHERE l.local_dataset_id=? AND l.source_report_id=? AND j.blocked_reason='deleted_by_user'",
                (local_dataset_id, source_report_id)).fetchone()
            if deleted is not None:
                return CaptureResult(event_id=None, event_type=None, eligible=False, payload_sha256=sha)
            latest_deleted = tx.execute(
                "SELECT blocked_reason FROM source_journal WHERE local_dataset_id=? AND source_report_id=?"
                " ORDER BY source_revision DESC LIMIT 1",
                (local_dataset_id, source_report_id)).fetchone()
            if latest_deleted is not None and latest_deleted["blocked_reason"] == "deleted_by_user":
                return CaptureResult(event_id=None, event_type=None, eligible=False, payload_sha256=sha)
        if backfill:
            # 최신 관측 조회와 변경 발급은 같은 journal 쓰기 트랜잭션에서 수행한다.
            # personal_save_state는 보충 조건으로 사용하지 않는다.
            # 보충은 옛 관측의 재해석이지 새 수집이 아니다 — 사용자가 지운 관측(deleted_by_user)을 되살리지 않는다
            # (BL-1). 후보 조회 뒤 삭제가 끼어들 수 있어 발급 트랜잭션 안에서 다시 확인한다(모바일 capture 와 같음).
            deleted = tx.execute(
                "SELECT 1 FROM report_latest l JOIN source_journal j ON j.event_id=l.event_id"
                " WHERE l.local_dataset_id=? AND l.source_report_id=? AND j.blocked_reason='deleted_by_user'",
                (local_dataset_id, source_report_id)).fetchone()
            if deleted is not None:
                return CaptureResult(event_id=None, event_type=None, eligible=False, payload_sha256=sha)
            latest = tx.execute(
                "SELECT payload_json, report_number, blocked_reason"
                " FROM source_journal WHERE local_dataset_id=? AND source_report_id=?"
                " ORDER BY source_revision DESC LIMIT 1",
                (local_dataset_id, source_report_id)).fetchone()
            if latest is None or latest["blocked_reason"] == "deleted_by_user":
                return CaptureResult(event_id=None, event_type=None, eligible=False, payload_sha256=sha)
            original = json.loads(latest["payload_json"])
            payload = _backfill_payload(original, adapter_input)
            sha = payload_sha256(payload)
            eligible = is_eligible(payload)
            if payload == original:
                return CaptureResult(event_id=None, event_type=None, eligible=eligible, payload_sha256=sha)
            report_number = latest["report_number"]
            code_blocked = latest["blocked_reason"] == f"blocked:{AGENCY_CODE_TOO_LONG}"
            progress_label = None
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
            ctx_row = captured_context
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

        ctx = captured_context
        active = ctx is not None and ctx.get("state") == "active"
        capture_origin = trigger + ":consent=" + authorization
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
             ctx.get("dataset_key") if ctx else None, source_report_id, report_number, revision, event_type, now, capture_origin, run_id,
             SCHEMA_VERSION, PARSER_VERSION, payload_text, sha, 1 if eligible else 0,
             ctx.get("contributor_fingerprint") if ctx else None,
             ctx.get("connection_id") if ctx else None,
             ctx.get("writer_epoch") if ctx else None,
             ctx.get("consent_grant_id") if ctx else None,
             f"blocked:{AGENCY_CODE_TOO_LONG}" if code_blocked
             else (None if active else "consent:" + authorization)))
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
    except Exception as exc:
        note_fallback("community_capture.deletion_state", exc)
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
    except Exception as exc:
        note_fallback("community_capture.apply_pending_deletion", exc)
        return True


# ── 기존 신고 다시 보내기 (fine-estimate-contract.md "기존 신고 다시 보내기") ──────────────
# 앱 업데이트 뒤 한 번, journal 최신 payload의 fine_estimate만 재계산해 바뀐 것만 보낸다. 크롤링·사용자 조작 없음.
# capture() 가 이미 하는 일(동의·계정 문맥 검사, sha 비교, outbox 적재)을 그대로 쓴다 — 여기서 다시 판정하지 않는다.

RESEND_MARKER_KEY = "fine_estimate_resend_done"


def _resend_marker(data_dir: str | None = None) -> dict | None:
    try:
        store = _store(data_dir)
        raw = store.meta(RESEND_MARKER_KEY)
    except Exception as exc:
        note_fallback("community_capture.resend_marker", exc)
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if data.get("local_dataset_id") != store.local_dataset_id():
        return None
    return {"rule_version": data.get("rule_version"), "parser_version": data.get("parser_version")}


def _set_resend_marker(data_dir: str | None, current: dict, *, local_dataset_id: str | None = None) -> None:
    store = _store(data_dir)
    with store.transaction() as tx:
        marker = {**current, "local_dataset_id": local_dataset_id or store.local_dataset_id(),
                  "done_at": _now_iso()}
        store.set_meta(RESEND_MARKER_KEY, json.dumps(marker, ensure_ascii=False), tx)


# 개인 DB에서 읽는 열: 일반 캡처와 같은 추정 판정 입력(범칙금_과태료 원문·위반법규 포함).
# 상태·주소·기관 등 나머지 payload 필드는 journal 최신 payload 를 그대로 보존한다(_backfill_payload).
BACKFILL_PERSONAL_FIELDS = (
    "ID", "entry_value", "ssnpc_cd", "ssnpc_type", "차량번호", "사진_첫촬영", "사진_끝촬영", "발생시각", "신고명",
    "범칙금_과태료", "위반법규",
)


def _resend_candidate_ids(data_dir: str | None, local_dataset_id: str) -> list[str]:
    """한 번이라도 캡처된 신고(report_latest) 중 사용자가 지우지 않은 것만, ID 순(모바일 resendFineEstimateIfNeeded 와 같은 후보 규칙).

    개인 금액 원문으로 거르지 않는다 — 추정 판정은 capture() 의 발급 트랜잭션에서 한다.
    """
    rows = _store(data_dir).connect().execute(
        "SELECT l.source_report_id FROM report_latest l JOIN source_journal j ON j.event_id=l.event_id"
        " WHERE l.local_dataset_id=? AND (j.blocked_reason IS NULL OR j.blocked_reason!='deleted_by_user')"
        " ORDER BY l.source_report_id", (local_dataset_id,)).fetchall()
    return [str(row["source_report_id"]) for row in rows]


def _resend_adapter_input(row: dict) -> dict:
    """저장된 신고 행(mysafety_report, 사이트 원문)을 build_adapter_input 입력으로 옮긴다. 크롤링 없음."""
    from core.database import models

    detail = {c: row.get(c) for c in models.DETAIL_COLUMN_NAMES}
    title_fields = {c: row.get(c) for c in models.TITLE_COLUMN_NAMES}
    geo = {"지오코딩상태": row.get("지오코딩상태"), "위도": row.get("위도"), "경도": row.get("경도")}
    return build_adapter_input(
        detail, title_fields, row.get("entry_value"), geo, None,
        ssnpc_cd=row.get("ssnpc_cd"), ssnpc_type=row.get("ssnpc_type"),
        report_name=row.get("신고명"),
        photo_first_at=row.get("사진_첫촬영"), photo_last_at=row.get("사진_끝촬영"))


def resend_fine_estimate_updates(engine, data_dir: str | None = None) -> dict:
    """앱 업데이트 뒤 한 번: journal 최신 payload의 fine_estimate만 재계산해 바뀐 것만
    capture() 로 다시 보낸다. 완료 표시는 데이터셋·(RULE_VERSION, PARSER_VERSION) 기준이다.
    동의·계정 문맥이 없거나 철회됐으면 capture() 가 journal 만 쓰고 outbox 에 넣지 않는다(기존 규칙, 보내지 않음).
    삭제 정리가 아직 안 끝났으면 이번 실행은 건너뛰고 완료로 표시하지 않는다(다음 실행에서 다시 본다).
    한 건이라도 실패하면 완료로 표시하지 않는다 — capture() 는 이미 보낸 건을 다시 보내지 않으므로(no-op)
    다음 실행이 전체를 다시 훑어도 실패한 건만 실제로 새로 처리된다("실패해도 다음 실행에서 이어서")."""
    current = {"rule_version": fine_estimate.RULE_VERSION, "parser_version": PARSER_VERSION}
    if _resend_marker(data_dir) == current:
        return {"state": "already_done", "candidates": 0, "resent": 0, "errors": 0, **current}
    if deletion_cleanup_pending(data_dir):
        return {"state": "deletion_pending", "candidates": 0, "resent": 0, "errors": 0, **current}

    from sqlalchemy import select
    from core.database import models

    store = _store(data_dir)
    dataset_id = store.local_dataset_id()
    # 계정 상태 파일은 실행마다 한 번만 읽는다(신고마다 다시 읽지 않음). 업로더는 전송 때 현재 문맥과 다시 대조한다.
    account_context = read_account_context(store)
    table = models.report_table
    candidates = resent = errors = 0
    for source_report_id in _resend_candidate_ids(data_dir, dataset_id):
        candidates += 1
        try:
            with engine.begin() as conn:
                # 후보 스냅샷은 ID만 신뢰한다. 재조회부터 캡처까지 크롤러 쓰기를 막고,
                # 연결을 유지하여 DB 복원(연결 장벽/데이터셋 회전)과도 직렬화한다.
                conn.exec_driver_sql("BEGIN IMMEDIATE")
                if _store(data_dir).local_dataset_id() != dataset_id:
                    return {"state": "dataset_changed", "candidates": candidates, "resent": resent,
                            "errors": errors, **current}
                fresh = conn.execute(select(*(table.c[key] for key in BACKFILL_PERSONAL_FIELDS))
                                     .where(table.c.ID == source_report_id)).mappings().first()
                if fresh is None:
                    continue
                result = capture(_resend_adapter_input(dict(fresh)), source_report_id=source_report_id,
                                 trigger="fine_estimate_backfill", data_dir=data_dir,
                                 account_context=account_context)
                mark_personal_save(result.event_id, True, data_dir=data_dir)
                if result.event_id:
                    resent += 1
        except CaptureStoreUnavailable:
            raise
        except Exception as exc:
            errors += 1
            note_fallback("community_capture.resend_fine_estimate_updates", exc)
    if errors == 0:
        _set_resend_marker(data_dir, current, local_dataset_id=dataset_id)
    return {"state": "done" if errors == 0 else "partial", "candidates": candidates,
            "resent": resent, "errors": errors, **current}
