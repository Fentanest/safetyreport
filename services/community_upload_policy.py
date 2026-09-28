"""커뮤니티 업로드 공통 판정 규칙 UC-1 (모바일 `lib/community/upload/upload_policy.dart` 와 같은 뜻).

순수 함수만 둔다(네트워크·DB 없음). 두 앱이 `contracts/upload-control/vectors.json` 으로 같은 결과를 확인한다.
- 응답 해석: 전송 계층(실제 HTTP 상태·헤더·본문 바이트)과 서버 JSON 을 분리한다. 본문 필드가 상태를 덮지 않는다.
- ACK 확정: HTTP 200 ∧ protocol==1 ∧ request_id ∧ results ∧ 보낸 event_id ∧ 중복 없음 ∧ durable is True ∧ 허용 상태 ∧ receipt_id(UUID).
- 오류 분류: offline / rate_limited / server_busy / invalid_ack / auth_required / consent_rejected / connection_rejected /
  request_too_large / payload_invalid. 오류 envelope 가 없는 4xx 는 payload 오류로 보지 않는다(서버 이상 → 재시도).
- 대기: 로컬 백오프 min(300, 5·2^(n-1))·(0.5+0.5u), 서버 지시(Retry-After 초·HTTP-date, 본문 retry_after_seconds)는 최댓값,
  실제 대기 = max(서버 지시, 로컬 백오프). HTTP-date 는 응답 `Date` 헤더가 있으면 그 시각 기준(기기 시계가 틀려도 조기 재전송 없음).
  24시간을 넘는 지시는 비정상 값으로 보고 24시간으로 제한한다(중앙 계약값은 60초 — 유일한 예외, 문서화).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

BACKOFF_BASE_SECONDS = 5.0
BACKOFF_CAP_SECONDS = 300.0
SERVER_HINT_CAP_SECONDS = 24 * 3600

DURABLE_STATUSES = frozenset({"accepted", "transferred", "duplicate", "no_change", "stale_ignored", "quarantined"})
NON_DURABLE_STATUSES = frozenset({"rejected", "conflict"})
PROJECTIONS = frozenset({"published", "removed", "held", "not_public", "not_applicable"})
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

# 오류 분류 → 전송 제어 범위. None 이면 제어기를 건드리지 않는다(행 상태만).
RETRYABLE_CLASSES = {"offline": "service", "server_busy": "service", "invalid_ack": "service", "rate_limited": "account",
                     "request_rejected": "service"}
AUTH_CODES = frozenset({"auth_required", "kakao_required", "session_revoked"})
CONSENT_CODES = frozenset({"consent_missing", "consent_revoked", "consent_outdated", "consent_grant_unknown"})
CONNECTION_CODES = frozenset({"connection_unknown", "connection_revoked", "connection_suspended", "connection_session_mismatch",
                              "connection_mode_mismatch", "writer_superseded", "contributor_suspended"})
# 이벤트 하나가 원인인 것이 확실한 코드(배치 이분 → 단건 격리)
EVENT_PAYLOAD_CODES = frozenset({"payload_hash_mismatch", "event_type_mismatch"})
# envelope 전체 또는 이벤트 어느 쪽일 수도 있는 코드(대조 요청으로 좁힌다)
AMBIGUOUS_PAYLOAD_CODES = frozenset({"invalid_request", "schema_invalid"})
# 요청 공통(메서드·Content-Type): 클라이언트 설정·버그 — 이벤트를 버리지 않고 일괄 보류
REQUEST_CODES = frozenset({"method_not_allowed", "unsupported_media_type"})
PAYLOAD_CODES = EVENT_PAYLOAD_CODES | AMBIGUOUS_PAYLOAD_CODES


def backoff_seconds(n: int, u: float) -> float:
    """n 번째 연속 실패(1부터)의 로컬 대기. u∈[0,1) 난수(테스트에서 주입). 지터 뒤에도 상한 300초."""
    n = max(1, int(n))
    base = min(BACKOFF_CAP_SECONDS, BACKOFF_BASE_SECONDS * (2 ** min(n - 1, 30)))
    u = min(max(float(u), 0.0), 1.0)
    return base * (0.5 + 0.5 * u)


def retry_delay_seconds(n: int, u: float, hint: int | None) -> float:
    """서버 지시보다 일찍 보내지 않고, 서버 지시를 로컬 상한으로 줄이지 않는다."""
    local = backoff_seconds(n, u)
    return float(max(local, hint)) if hint else local


def _header(headers: dict | None, name: str) -> str | None:
    if not headers:
        return None
    wanted = name.lower()
    for key, value in headers.items():
        if str(key).lower() == wanted:
            return None if value is None else str(value)
    return None


def parse_retry_after(headers: dict | None, body_obj, now: datetime) -> int | None:
    """유효한 서버 지시(초) 중 최댓값. 과거·0·음수·파싱 불가는 무시, 24시간 초과는 24시간(비정상 값 방어)."""
    hints: list[int] = []
    raw = _header(headers, "Retry-After")
    if raw is not None:
        text = raw.strip()
        if re.fullmatch(r"\d+", text):
            seconds = int(text)
            if seconds > 0:
                hints.append(seconds)
        elif text and not text.lstrip().startswith("-"):
            try:
                when = parsedate_to_datetime(text)
            except (TypeError, ValueError, IndexError):
                when = None
            if when is not None:
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
                base = now
                server_date = _header(headers, "Date")
                if server_date:
                    try:
                        parsed = parsedate_to_datetime(server_date.strip())
                        base = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
                    except (TypeError, ValueError, IndexError):
                        base = now
                seconds = int((when - base).total_seconds())
                if seconds > 0:
                    hints.append(seconds)
    if isinstance(body_obj, dict) and isinstance(body_obj.get("error"), dict):
        value = body_obj["error"].get("retry_after_seconds")
        if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
            hints.append(value)
    if not hints:
        return None
    return min(max(hints), SERVER_HINT_CAP_SECONDS)


@dataclass
class EventOutcome:
    event_id: str
    outcome: str  # done | dead | blocked
    status: str
    receipt_id: str | None = None
    projection_status: str | None = None
    error_code: str | None = None


@dataclass
class Interpretation:
    kind: str  # ack | error
    http_status: int | None = None
    request_id: str | None = None
    events: dict[str, EventOutcome] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    error_class: str | None = None
    code: str | None = None
    scope: str | None = None
    hint: int | None = None


def _error(cls: str, status: int | None, *, code: str | None = None, hint: int | None = None,
           request_id: str | None = None) -> Interpretation:
    return Interpretation(kind="error", http_status=status, error_class=cls, code=code or cls,
                          scope=RETRYABLE_CLASSES.get(cls), hint=hint, request_id=request_id)


def _decode(body: bytes | str | None):
    if body is None:
        return None
    try:
        text = body.decode("utf-8") if isinstance(body, (bytes, bytearray)) else str(body)
        return json.loads(text) if text.strip() else None
    except (UnicodeDecodeError, ValueError):
        return None


def _error_envelope(obj) -> tuple[str | None, str | None]:
    """유효한 오류 envelope 면 (code, request_id). 아니면 (None, None)."""
    if not isinstance(obj, dict) or not isinstance(obj.get("error"), dict):
        return None, None
    err = obj["error"]
    code = err.get("code")
    if not isinstance(code, str) or not code:
        return None, None
    request_id = err.get("request_id") if isinstance(err.get("request_id"), str) else None
    return code, request_id


def _valid_result(item) -> bool:
    if not isinstance(item, dict) or not isinstance(item.get("event_id"), str) or not item["event_id"]:
        return False
    status, durable = item.get("status"), item.get("durable")
    if not isinstance(durable, bool) or not isinstance(status, str):
        return False
    projection = item.get("projection_status")
    if projection is not None and (not isinstance(projection, str) or projection not in PROJECTIONS):
        return False
    if durable:
        receipt = item.get("receipt_id")
        return status in DURABLE_STATUSES and isinstance(receipt, str) and bool(_UUID.fullmatch(receipt))  # 끝 개행도 허용하지 않는다(Dart·SQL GLOB 과 같은 집합)
    err = item.get("error")
    return (status in NON_DURABLE_STATUSES and isinstance(err, dict) and isinstance(err.get("code"), str)
            and isinstance(err.get("retryable"), bool))


def interpret_ack(sent_ids: list[str], obj) -> Interpretation:
    """HTTP 200 본문(이미 JSON 해석) 판정. 형식이 하나라도 틀리면 invalid_ack(보낸 행 전부 재시도)."""
    # protocol 은 JSON 정수 1 만(Python 의 True == 1 을 막는다 — 모바일 Dart 와 같은 판정)
    if not isinstance(obj, dict) or type(obj.get("protocol")) is not int or obj.get("protocol") != 1 \
            or not isinstance(obj.get("request_id"), str) or not isinstance(obj.get("results"), list):
        return _error("invalid_ack", 200)
    sent = set(sent_ids)
    seen: set[str] = set()
    events: dict[str, EventOutcome] = {}
    for item in obj["results"]:
        if not _valid_result(item) or item["event_id"] not in sent or item["event_id"] in seen:
            return _error("invalid_ack", 200, request_id=obj["request_id"])
        seen.add(item["event_id"])
        status = item["status"]
        if item["durable"]:
            outcome = "done"
        elif status == "conflict":
            outcome = "dead"
        else:
            outcome = "blocked"
        err = item.get("error") if isinstance(item.get("error"), dict) else None
        events[item["event_id"]] = EventOutcome(
            event_id=item["event_id"], outcome=outcome, status=status, receipt_id=item.get("receipt_id"),
            projection_status=item.get("projection_status"), error_code=(err or {}).get("code"))
    missing = [event_id for event_id in sent_ids if event_id not in seen]
    return Interpretation(kind="ack", http_status=200, request_id=obj["request_id"], events=events, missing=missing)


def interpret_response(sent_ids: list[str], status: int | None, headers: dict | None, body,
                       now: datetime) -> Interpretation:
    """전송 결과 판정. status=None 은 전송 계층 실패(연결·timeout)."""
    if status is None:
        return _error("offline", None)
    obj = _decode(body)
    if status == 200:
        return interpret_ack(sent_ids, obj)
    hint = parse_retry_after(headers, obj, now)
    code, request_id = _error_envelope(obj)
    if status == 429:
        return _error("rate_limited", status, code=code or "rate_limited", hint=hint, request_id=request_id)
    if status in (405, 415) and code is None:
        return _error("request_rejected", status, code="method_not_allowed" if status == 405 else "unsupported_media_type")
    if status == 413:
        return _error("request_too_large", status, code=code or "payload_too_large", request_id=request_id)
    if status == 401:
        return _error("auth_required", status, code=code or "auth_required", request_id=request_id)
    if code is not None:
        if code in AUTH_CODES:
            return _error("auth_required", status, code=code, request_id=request_id)
        if code in CONSENT_CODES:
            return _error("consent_rejected", status, code=code, request_id=request_id)
        if code in CONNECTION_CODES:
            return _error("connection_rejected", status, code=code, request_id=request_id)
        if code == "payload_too_large":
            return _error("request_too_large", status, code=code, request_id=request_id)
        if code in PAYLOAD_CODES and 400 <= status < 500:
            return _error("payload_invalid", status, code=code, request_id=request_id)
        if code in REQUEST_CODES:
            return _error("request_rejected", status, code=code, request_id=request_id)
        if code == "rate_limited":
            return _error("rate_limited", status, code=code, hint=hint, request_id=request_id)
    # 5xx, 오류 envelope 없는 4xx(HTML 404 등), 모르는 코드: 서버 이상 → 서비스 단위 재시도
    return _error("server_busy", status, code=code or f"http_{status}", hint=hint, request_id=request_id)


# /api/v1 소비자 호환: 예전 결과 값(success/no_change/deferred/…)을 `result` 에 그대로 두고 새 코드는 `outcome` 으로 준다.
LEGACY_RESULT = {"sent": "success", "no_pending": "no_change", "not_due": "no_change", "cooldown": "deferred",
                 "more_pending": "partial",
                 "busy_other_run": "deferred", "blocked_gate": "deferred", "needs_auth": "auth_required",
                 "needs_consent": "consent_required"}


def legacy_result(outcome):
    return LEGACY_RESULT.get(outcome, outcome) if isinstance(outcome, str) else outcome
