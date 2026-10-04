"""커뮤니티 계정 응답을 읽는 클라이언트 규칙(D2-10). 정본 contracts/community-client/client-rules.md,
벡터 contracts/community-client/vectors/*.json(모바일·auth 와 같은 파일).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

_TRANSIENT_CODES = frozenset({"rate_limited", "busy", "server_error"})
_RETRY_AFTER_HEADER = re.compile(r"^\d+$")


def _s(value):
    return value if isinstance(value, str) else None


def normalize_status(raw):
    """status 응답의 게이트 관련 필드를 정해진 형으로 맞춘다(형이 다르면 없는 값). 최상위가 객체가 아니면 None."""
    if not isinstance(raw, dict):
        return None

    def obj(key):
        value = raw.get(key)
        return value if isinstance(value, dict) else {}

    gate, contributor, consent, policy, account = (obj(k) for k in ("gate", "contributor", "consent", "policy", "account"))
    reasons = gate.get("reasons")
    out = dict(raw)
    out["gate"] = {**gate, "kakao": gate.get("kakao") is True,
                   "reasons": [r for r in reasons if isinstance(r, str)] if isinstance(reasons, list) else []}
    out["contributor"] = {**contributor, "status": _s(contributor.get("status"))}
    out["consent"] = {**consent, **{k: _s(consent.get(k))
                                    for k in ("state", "grant_id", "policy_version", "consent_text_sha256")}}
    out["policy"] = {**policy, **{k: _s(policy.get(k)) for k in ("required_version", "consent_text_sha256")}}
    out["account"] = {**account, **{k: _s(account.get(k)) for k in ("fingerprint", "display_name")}}
    out["connection"] = raw.get("connection") if isinstance(raw.get("connection"), dict) else None
    return out


@dataclass(frozen=True)
class AccountResponse:
    success: bool
    data: dict | None = None
    code: str | None = None
    transient: bool = False
    retry_after: float | None = None
    auth: bool = False
    extra: dict = field(default_factory=dict)


def classify_response(http_status: int, text: str | None, headers=None) -> AccountResponse:
    """HTTP 응답을 성공 또는 (코드, 일시 오류 여부, 재시도 대기, 인증 오류)로 나눈다(client-rules §2)."""
    try:
        data = json.loads(text) if text else None
    except ValueError:
        data = None
    if http_status == 200 and isinstance(data, dict) and "error" not in data:
        return AccountResponse(True, data=data)
    err = data.get("error") if isinstance(data, dict) and isinstance(data.get("error"), dict) else {}
    body_code = err.get("code") if isinstance(err.get("code"), str) else None
    code = body_code or "server_error"
    retryable = err.get("retryable")
    # 본문에 코드가 없어 server_error 로 채운 경우는 HTTP 상태로만 판단한다(401·400·깨진 200 을 재시도하지 않게).
    transient = retryable if isinstance(retryable, bool) else (body_code in _TRANSIENT_CODES or http_status >= 500)
    after = err.get("retryAfterSeconds")
    retry_after = None
    if isinstance(after, (int, float)) and not isinstance(after, bool) and after >= 0:
        retry_after = float(after)
    else:
        header = str((headers or {}).get("Retry-After") or "").strip()
        if _RETRY_AFTER_HEADER.match(header):
            retry_after = float(header)
    extra = {k: v for k, v in err.items() if k in ("active_writer", "required_version")}
    return AccountResponse(False, code=code, transient=transient, retry_after=retry_after,
                           auth=code == "auth_required" or http_status == 401, extra=extra)


def is_current_response(started: dict, now: dict) -> bool:
    """status 조회를 시작한 때와 받은 때의 (세대, 모드, 계정)이 같고 세션이 유효해야 그 응답을 쓴다(client-rules §4)."""
    if now.get("session") != "valid":
        return False
    if started.get("generation") != now.get("generation") or started.get("mode") != now.get("mode"):
        return False
    return started.get("user_id") is None or started.get("user_id") == now.get("user_id")
