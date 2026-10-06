"""`community-account` 사용자 전용 API 클라이언트 (계약: contracts/community-ingest/account-api.md).

- 헤더: apikey = Publishable Key, Authorization = 이 서버의 커뮤니티 세션 access token(community_auth_service 가 공급).
- 리다이렉트를 따르지 않는다. 타임아웃 25초. 토큰·연결 비밀·응답 원문을 로그에 남기지 않는다.
- 예외에는 계약 오류 코드와 HTTP 상태만 담는다.
"""
from __future__ import annotations

import hashlib

from urllib.parse import urlsplit

import requests

from core.utils import runtime_mode
from services.community_client_rules import classify_response

PROTOCOL_VERSION = 1
FUNCTION = "community-account"
TIMEOUT_SECONDS = 25


class AccountApiError(RuntimeError):
    def __init__(self, code: str, status: int | None = None, retry_after: float | None = None, extra: dict | None = None,
                 *, transient: bool | None = None, auth: bool | None = None):
        super().__init__(code)
        self.code = code
        self.status = status
        self.retry_after = retry_after
        self.extra = extra or {}
        self._transient = transient
        self._auth = auth

    @property
    def transient(self) -> bool:
        """일시 오류(client-rules §2): 응답을 분류할 때 정한 값. 응답 없이 만든 오류는 코드·상태로 판단한다."""
        if self._transient is not None:
            return self._transient
        return self.code in ("network_error", "busy", "rate_limited", "server_error") or (
            self.status is not None and self.status >= 500)

    @property
    def auth(self) -> bool:
        return self._auth if self._auth is not None else (self.code == "auth_required" or self.status == 401)


class CommunityAccountClient:
    def __init__(self, supabase_url: str, publishable_key: str, session: requests.Session | None = None):
        self.base = f"{supabase_url.rstrip('/')}/functions/v1/{FUNCTION}"
        self.publishable_key = publishable_key
        self.http = session or requests.Session()

    def _post(self, action: str, access_token: str, body: dict) -> dict:
        payload = {"protocol": PROTOCOL_VERSION, **body}
        if urlsplit(self.base).hostname != "127.0.0.1":  # fixture 서버는 loopback 로컬 스택만
            try:
                runtime_mode.block_if_fixture("community account network")
            except runtime_mode.ExternalSideEffectBlocked:
                raise AccountApiError("fixture_blocked") from None
        try:
            resp = self.http.post(f"{self.base}/{action}", json=payload, timeout=TIMEOUT_SECONDS, allow_redirects=False,
                                  headers={"apikey": self.publishable_key, "Authorization": f"Bearer {access_token}"})
        except requests.RequestException:
            raise AccountApiError("network_error") from None
        result = classify_response(resp.status_code, resp.text, resp.headers)
        if not result.success:
            raise AccountApiError(result.code, resp.status_code, result.retry_after, result.extra,
                                  transient=result.transient, auth=result.auth)
        return result.data

    def status(self, access_token: str, connection_id: str | None = None) -> dict:
        return self._post("status", access_token, {"connection_id": connection_id} if connection_id else {})

    def policy(self, access_token: str) -> dict:
        """지금 필수 동의 정책 {version, consent_text_sha256, consent_text} (2026-09-27, 계약 account-api.md `policy`).
        동의문은 앱에 넣어 두지 않고 중앙에서 받는다. 본문의 sha256(UTF-8)이 해시와 같을 때만 돌려준다(보여 줄 본문 = 동의할 해시)."""
        res = self._post("policy", access_token, {})
        p = res.get("policy") if isinstance(res, dict) else None
        text = p.get("consent_text") if isinstance(p, dict) else None
        if (not isinstance(text, str) or not isinstance(p.get("version"), str)
                or hashlib.sha256(text.encode("utf-8")).hexdigest() != p.get("consent_text_sha256")):
            raise AccountApiError("server_error")
        return {"version": p["version"], "consent_text_sha256": p["consent_text_sha256"], "consent_text": text}

    def consent(self, access_token: str, policy_version: str, consent_text_sha256: str) -> dict:
        return self._post("consent", access_token, {"policy_version": policy_version, "consent_text_sha256": consent_text_sha256,
                                                    "via": "safetyreport_server", "accepted": True})

    def consent_revoke(self, access_token: str, grant_id: str) -> dict:
        return self._post("consent-revoke", access_token, {"grant_id": grant_id})

    def register_connection(self, access_token: str, *, platform: str, device_label: str, dataset_key: str,
                            connection_secret: str, takeover: bool) -> dict:
        return self._post("connections", access_token, {
            "source_app": "safetyreport", "source_mode": "server", "platform": platform, "device_label": device_label,
            "dataset_key": dataset_key, "connection_secret": connection_secret, "takeover": bool(takeover)})

    def rebind_connection(self, access_token: str, connection_id: str, connection_secret: str) -> dict:
        return self._post("connections-rebind", access_token, {"connection_id": connection_id,
                                                               "connection_secret": connection_secret})

    def revoke_connection(self, access_token: str, connection_id: str) -> dict:
        return self._post("connections-revoke", access_token, {"connection_id": connection_id})

    # 공식 계정 변경 시 기존 공유자료 삭제 및 바인딩 해제.
    def delete_contributions(self, access_token: str) -> dict:
        return self._post("contributions-delete", access_token, {"confirm": "DELETE_MY_SHARED_REPORTS"})
