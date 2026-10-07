"""community-ingest HTTP 클라이언트 (PC).

POST {supabase_url}/functions/v1/community-ingest — 헤더 apikey(publishable) +
Authorization: Bearer <get_access_token()>, connect 5s / read 30s, 리다이렉트 금지.
envelope 는 envelope.schema.json 그대로 (client_version=VERSION, parser_version=pc-parser-2).
응답은 ack.schema.json 형태로 검증하고 errors.md 코드로 분류한 결과 객체를 돌린다.
토큰·payload 원문은 로그에 남기지 않는다.
"""
from __future__ import annotations

import json
import logging
import re
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from pathlib import Path

from services.community_capture import PARSER_VERSION

_log = logging.getLogger("safetyreport.community.ingest")

INGEST_PATH = "/functions/v1/community-ingest"
MANIFEST_PATH = "/functions/v1/community-ingest/manifest"
CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 30.0
MAX_BODY_BYTES = 256 * 1024

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_ACK_STATUSES = {"accepted", "duplicate", "no_change", "stale_ignored", "quarantined", "rejected", "conflict"}
_PROJECTION = {"published", "removed", "held", "not_public", "not_applicable"}

ROOT = Path(__file__).resolve().parents[1]


def _client_version() -> str:
    try:
        return (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"


@dataclass
class EventResult:
    event_id: str
    status: str
    durable: bool = False
    receipt_id: str | None = None
    projection_status: str | None = None
    error_code: str | None = None
    error_retryable: bool = False


@dataclass
class IngestResponse:
    """전송 1회 결과. ok=True 는 ACK 형식이 유효한 200 응답(각 이벤트 확정 여부는 uploader 가 UC-1 로 다시 판정).
    http_status 는 **실제** HTTP 상태(본문 필드가 덮지 않는다). not_sent=True 면 HTTP 요청을 보내지 않았다(attempt 미집계)."""
    ok: bool
    http_status: int | None
    code: str | None  # 요청 단위 오류 코드 (ok 면 None)
    retryable: bool = False
    retry_after: int | None = None
    request_id: str | None = None
    results: list[EventResult] = field(default_factory=list)
    raw_error_message: str | None = None
    error_class: str | None = None  # UC-1 분류(offline, rate_limited, server_busy, invalid_ack, auth_required, ...)
    not_sent: bool = False
    token: str | None = None  # 이 요청에 쓴 access token(401 강제 갱신용, 로그·저장 금지)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


MAX_RESPONSE_BYTES = 1024 * 1024


def _http_post(url: str, headers: dict, body: bytes, timeout: float) -> tuple[int, bytes, dict]:
    opener = urllib.request.build_opener(_NoRedirect)
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, response.read(MAX_RESPONSE_BYTES), dict(response.headers.items())
    except urllib.error.HTTPError as exc:
        try:
            payload = exc.read(MAX_RESPONSE_BYTES)
        except Exception:
            payload = b""
        return exc.code, payload, dict((exc.headers or {}).items())


def build_envelope(*, connection_id: str, consent_grant_id: str, policy_version: str,
                   source_mode: str, trigger: str, events: list[dict]) -> dict:
    return {
        "protocol": 1,
        "contract": "community-ingest-v1",
        "source_app": "safetyreport",
        "source_mode": source_mode,
        "connection_id": connection_id,
        "consent_grant_id": consent_grant_id,
        "policy_version": policy_version,
        "client_version": _client_version(),
        "parser_version": PARSER_VERSION,
        "trigger": trigger,
        "events": events,
    }


def envelope_bytes(envelope: dict) -> bytes:
    """보내는 바이트 그대로(크기 계산·전송 공통)."""
    return json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _from_interpretation(interp, *, token: str | None) -> IngestResponse:
    if interp.kind == "ack":
        results = [EventResult(event_id=e.event_id, status=e.status, durable=e.outcome == "done",
                               receipt_id=e.receipt_id, projection_status=e.projection_status,
                               error_code=e.error_code, error_retryable=False) for e in interp.events.values()]
        return IngestResponse(ok=True, http_status=200, code=None, request_id=interp.request_id,
                              results=results, error_class=None, token=token)
    return IngestResponse(ok=False, http_status=interp.http_status, code=interp.code,
                          retryable=interp.scope is not None, retry_after=interp.hint, request_id=interp.request_id,
                          error_class=interp.error_class, token=token)


def post_envelope(envelope: dict, *, token: str | None = None, now=None) -> IngestResponse:
    """envelope 전송 1회(재시도 없음 — 재시도는 outbox·전송 제어가 맡는다). 판정은 UC-1(community_upload_policy).
    token 을 주지 않으면 여기서 받는다(받지 못하면 요청을 보내지 않고 not_sent)."""
    from datetime import datetime, timezone

    from services import community_auth_service as cas
    from services import community_upload_policy as policy
    try:
        cfg = _config()
    except cas.CommunityAuthError as exc:
        return IngestResponse(ok=False, http_status=None, code=exc.code, error_class="auth_required",
                              raw_error_message=exc.code, not_sent=True)
    body = envelope_bytes(envelope)
    if len(body) > MAX_BODY_BYTES:
        return IngestResponse(ok=False, http_status=None, code="payload_too_large", error_class="request_too_large",
                              not_sent=True)
    if token is None:
        try:
            token = cas.get_access_token()
        except cas.CommunityAuthError as exc:
            cls = "offline" if exc.code == "auth_unavailable" else "auth_required"
            return IngestResponse(ok=False, http_status=None, code=exc.code, error_class=cls,
                                  retryable=cls == "offline", raw_error_message=exc.code, not_sent=True)
    from services import community_cloud as cloud
    url = cfg.supabase_url.rstrip("/") + INGEST_PATH
    headers = {"apikey": cfg.publishable_key, "Authorization": f"Bearer {token}",
               "Content-Type": "application/json"}
    sent_ids = [str(e.get("event_id")) for e in envelope.get("events") or []]
    moment = now or datetime.now(timezone.utc)
    try:
        status, raw, resp_headers = cloud.run(cfg.supabase_url, lambda: _http_post(url, headers, body, CONNECT_TIMEOUT + READ_TIMEOUT))
    except Exception as exc:
        _log.info("[community] ingest 연결 실패: %s", type(exc).__name__)
        return _from_interpretation(policy.interpret_response(sent_ids, None, {}, None, moment), token=token)
    if status in (301, 302, 303, 307, 308):
        status_for_policy = 502  # 리다이렉트는 따르지 않는다 → 서버 이상으로 재시도
    else:
        status_for_policy = status
    interp = policy.interpret_response(sent_ids, status_for_policy, resp_headers, raw, moment)
    response = _from_interpretation(interp, token=token)
    response.http_status = status
    return response


def validate_ack(body: dict, sent_ids: list[str] | None = None) -> IngestResponse:
    """(호환) 200 본문 검증. sent_ids 가 없으면 본문의 id 를 보낸 것으로 본다."""
    from services import community_upload_policy as policy
    ids = sent_ids if sent_ids is not None else [
        str(r.get("event_id")) for r in (body.get("results") or []) if isinstance(r, dict)] if isinstance(body, dict) else []
    return _from_interpretation(policy.interpret_ack(ids, body), token=None)


def _config():
    from services import community_auth_service as cas
    cfg = cas.load_config_from_settings()
    if not cfg.configured:
        raise cas.CommunityAuthError("community_unconfigured")
    return cfg


_HEX24 = re.compile(r"^[0-9a-f]{24}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_TOKEN = re.compile(r"^[0-9]+$")


def _valid_manifest_page(parsed) -> bool:
    """account-api.md manifest 응답: dataset_key, writer_epoch, total, manifest_token("^[0-9]+$"),
    key_prefixes(24hex 목록), next_after(null|64hex). 형식이 하나라도 틀리면 페이지 전체를 거부한다."""
    if not isinstance(parsed, dict) or parsed.get("protocol") != 1:
        return False
    total, token, keys, nxt = parsed.get("total"), parsed.get("manifest_token"), parsed.get("key_prefixes"), parsed.get("next_after")
    if not isinstance(total, int) or isinstance(total, bool) or total < 0:
        return False
    if not isinstance(token, str) or not _TOKEN.match(token):
        return False
    if not isinstance(keys, list) or not all(isinstance(k, str) and _HEX24.match(k) for k in keys):
        return False
    return nxt is None or (isinstance(nxt, str) and bool(_HEX64.match(nxt)))


def post_manifest(*, connection_id: str, after: str | None = None, limit: int = 5000) -> tuple[bool, dict]:
    """manifest 페이지 1회 조회(POST {protocol, connection_id, after, limit}). (성공, 본문) — 실패·형식 오류면 (False, {}).
    응답 본문은 로그에 남기지 않는다."""
    from services import community_auth_service as cas
    cfg = _config()
    try:
        token = cas.get_access_token()
    except cas.CommunityAuthError:
        return False, {}
    from services import community_cloud as cloud
    url = cfg.supabase_url.rstrip("/") + MANIFEST_PATH
    body = json.dumps({"protocol": 1, "connection_id": connection_id, "after": after, "limit": max(1, min(limit, 5000))},
                      separators=(",", ":")).encode()
    headers = {"apikey": cfg.publishable_key, "Authorization": f"Bearer {token}",
               "Content-Type": "application/json"}
    try:
        status, raw, _ = cloud.run(cfg.supabase_url, lambda: _http_post(url, headers, body, CONNECT_TIMEOUT + READ_TIMEOUT))
    except Exception as exc:
        _log.info("[community] manifest 연결 실패: %s", type(exc).__name__)
        return False, {}
    if status != 200:
        return False, {}
    try:
        parsed = json.loads(raw.decode("utf-8")) if raw else {}
    except ValueError:
        return False, {}
    if not _valid_manifest_page(parsed):
        return False, {}
    return True, parsed
