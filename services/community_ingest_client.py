"""community-ingest HTTP 클라이언트 (PC).

POST {supabase_url}/functions/v1/community-ingest — 헤더 apikey(publishable) +
Authorization: Bearer <get_access_token()>, connect 5s / read 30s, 리다이렉트 금지.
envelope 는 envelope.schema.json 그대로 (client_version=VERSION, parser_version=pc-parser-1).
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
    ok: bool  # HTTP 2xx (+ ack 본문 검증 통과)
    http_status: int | None
    code: str | None  # 요청 단위 오류 코드 (ok 면 None)
    retryable: bool = False
    retry_after: int | None = None
    request_id: str | None = None
    results: list[EventResult] = field(default_factory=list)
    raw_error_message: str | None = None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _http_post(url: str, headers: dict, body: bytes, timeout: float) -> tuple[int, bytes, dict]:
    opener = urllib.request.build_opener(_NoRedirect)
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, response.read(), dict(response.headers.items())
    except urllib.error.HTTPError as exc:
        try:
            payload = exc.read()
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


def validate_ack(body: dict) -> IngestResponse:
    """ack.schema.json 핵심(서버 계약) 검증. 실패하면 ok=False/code=schema_invalid."""
    if not isinstance(body, dict):
        return IngestResponse(ok=False, http_status=200, code="schema_invalid")
    if "error" in body:
        err = body.get("error") or {}
        retry_after = err.get("retry_after_seconds")
        return IngestResponse(ok=False, http_status=None, code=str(err.get("code") or "server_error"),
                              retryable=bool(err.get("retryable")),
                              retry_after=int(retry_after) if isinstance(retry_after, int) else None,
                              request_id=err.get("request_id"))
    if not isinstance(body.get("request_id"), str) or not isinstance(body.get("results"), list):
        return IngestResponse(ok=False, http_status=200, code="schema_invalid")
    if body.get("protocol") != 1:
        return IngestResponse(ok=False, http_status=200, code="schema_invalid")
    results = []
    for item in body["results"]:
        if not isinstance(item, dict) or item.get("status") not in _ACK_STATUSES:
            return IngestResponse(ok=False, http_status=200, code="schema_invalid")
        if not isinstance(item.get("event_id"), str) or not isinstance(item.get("durable"), bool):
            return IngestResponse(ok=False, http_status=200, code="schema_invalid")
        projection = item.get("projection_status")
        if projection is not None and projection not in _PROJECTION:
            return IngestResponse(ok=False, http_status=200, code="schema_invalid")
        err = item.get("error")
        results.append(EventResult(
            event_id=item["event_id"], status=item["status"], durable=item["durable"],
            receipt_id=item.get("receipt_id"), projection_status=projection,
            error_code=(err or {}).get("code") if isinstance(err, dict) else None,
            error_retryable=bool((err or {}).get("retryable")) if isinstance(err, dict) else False))
    return IngestResponse(ok=True, http_status=200, code=None,
                          request_id=body["request_id"], results=results)


def _config():
    from services import community_auth_service as cas
    cfg = cas.load_config_from_settings()
    if not cfg.configured:
        raise cas.CommunityAuthError("community_unconfigured")
    return cfg


def post_envelope(envelope: dict, *, timeout: float = READ_TIMEOUT) -> IngestResponse:
    """envelope 전송 1회. 401 갱신 재시도는 uploader 가 담당한다."""
    from services import community_auth_service as cas
    try:
        cfg = _config()
    except cas.CommunityAuthError as exc:
        return IngestResponse(ok=False, http_status=None, code="auth_required",
                              raw_error_message=exc.code)
    body = json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(body) > MAX_BODY_BYTES:
        return IngestResponse(ok=False, http_status=None, code="payload_too_large")
    try:
        token = cas.get_access_token()
    except cas.CommunityAuthError as exc:
        return IngestResponse(ok=False, http_status=None, code="auth_required",
                              raw_error_message=exc.code)
    url = cfg.supabase_url.rstrip("/") + INGEST_PATH
    headers = {"apikey": cfg.publishable_key, "Authorization": f"Bearer {token}",
               "Content-Type": "application/json"}
    try:
        status, raw, resp_headers = _http_post(url, headers, body, CONNECT_TIMEOUT + READ_TIMEOUT)
    except Exception as exc:
        _log.info("[community] ingest 연결 실패: %s", type(exc).__name__)
        return IngestResponse(ok=False, http_status=None, code="offline", retryable=True)
    if status in (301, 302, 303, 307, 308):
        return IngestResponse(ok=False, http_status=status, code="server_error", retryable=True)
    try:
        parsed = json.loads(raw.decode("utf-8")) if raw else {}
    except ValueError:
        return IngestResponse(ok=False, http_status=status, code="server_error", retryable=True)
    if status == 429:
        err = parsed.get("error") if isinstance(parsed, dict) else None
        retry_after = (err or {}).get("retry_after_seconds") if isinstance(err, dict) else None
        header_after = resp_headers.get("Retry-After") or resp_headers.get("retry-after")
        try:
            header_after = int(str(header_after).strip())
        except (TypeError, ValueError):
            header_after = None
        return IngestResponse(ok=False, http_status=429, code="rate_limited", retryable=True,
                              retry_after=retry_after if isinstance(retry_after, int) else header_after,
                              raw_error_message=None)
    if status == 401:
        return IngestResponse(ok=False, http_status=401, code="auth_required", retryable=True)
    if status == 413:
        return IngestResponse(ok=False, http_status=413, code="payload_too_large")
    if status in (400, 422):
        code = (parsed.get("error") or {}).get("code") if isinstance(parsed, dict) else None
        return IngestResponse(ok=False, http_status=status, code=str(code or "schema_invalid"))
    if status == 403:
        code = (parsed.get("error") or {}).get("code") if isinstance(parsed, dict) else None
        return IngestResponse(ok=False, http_status=403, code=str(code or "connection_unknown"))
    if status in (500, 502, 503, 504):
        code = (parsed.get("error") or {}).get("code") if isinstance(parsed, dict) else None
        return IngestResponse(ok=False, http_status=status, code=str(code or "server_error"), retryable=True,
                              retry_after=((parsed.get("error") or {}).get("retry_after_seconds")
                                           if isinstance(parsed, dict) and isinstance(parsed.get("error"), dict) else None))
    if status != 200:
        return IngestResponse(ok=False, http_status=status, code="server_error", retryable=True)
    ack = validate_ack(parsed)
    ack.http_status = status
    return ack


def post_manifest(*, after: str | None = None, limit: int = 5000) -> tuple[bool, dict]:
    """manifest 페이지 1회 조회. (성공, 본문) — 실패면 (False, {}). 응답 본문은 로그에 남기지 않는다."""
    from services import community_auth_service as cas
    cfg = _config()
    try:
        token = cas.get_access_token()
    except cas.CommunityAuthError:
        return False, {}
    url = cfg.supabase_url.rstrip("/") + MANIFEST_PATH
    body = json.dumps({"after": after, "limit": min(limit, 5000)}, separators=(",", ":")).encode()
    headers = {"apikey": cfg.publishable_key, "Authorization": f"Bearer {token}",
               "Content-Type": "application/json"}
    try:
        status, raw, _ = _http_post(url, headers, body, CONNECT_TIMEOUT + READ_TIMEOUT)
    except Exception as exc:
        _log.info("[community] manifest 연결 실패: %s", type(exc).__name__)
        return False, {}
    if status != 200:
        return False, {}
    try:
        parsed = json.loads(raw.decode("utf-8")) if raw else {}
    except ValueError:
        return False, {}
    if not isinstance(parsed, dict) or not isinstance(parsed.get("keys"), list):
        return False, {}
    return True, parsed
