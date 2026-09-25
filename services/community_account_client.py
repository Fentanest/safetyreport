"""`community-account` 사용자 전용 API 클라이언트 (계약: contracts/community-ingest/account-api.md).

- 헤더: apikey = Publishable Key, Authorization = 이 서버의 커뮤니티 세션 access token(community_auth_service 가 공급).
- 리다이렉트를 따르지 않는다. 타임아웃 10초. 토큰·연결 비밀·응답 원문을 로그에 남기지 않는다.
- 예외에는 계약 오류 코드와 HTTP 상태만 담는다.
"""
from __future__ import annotations

from urllib.parse import urlsplit

import requests

from core.utils import runtime_mode

PROTOCOL_VERSION = 1
FUNCTION = "community-account"
TIMEOUT_SECONDS = 10


class AccountApiError(RuntimeError):
    def __init__(self, code: str, status: int | None = None, retry_after: float | None = None, extra: dict | None = None):
        super().__init__(code)
        self.code = code
        self.status = status
        self.retry_after = retry_after
        self.extra = extra or {}

    @property
    def transient(self) -> bool:
        return self.code in ("network_error", "busy", "rate_limited", "server_error", "service_disabled") or (
            self.status is not None and self.status >= 500)


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
        try:
            data = resp.json()
        except ValueError:
            raise AccountApiError("server_error", resp.status_code) from None
        if resp.status_code >= 400 or not isinstance(data, dict) or "error" in data:
            err = data.get("error") if isinstance(data, dict) and isinstance(data.get("error"), dict) else {}
            code = err.get("code") if isinstance(err.get("code"), str) else "server_error"
            retry = err.get("retryAfterSeconds")
            extra = {k: v for k, v in err.items() if k in ("active_writer", "required_version")}
            raise AccountApiError(code, resp.status_code, float(retry) if isinstance(retry, (int, float)) else None, extra)
        return data

    def status(self, access_token: str, connection_id: str | None = None) -> dict:
        return self._post("status", access_token, {"connection_id": connection_id} if connection_id else {})

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

    def delete_contributions(self, access_token: str) -> dict:
        return self._post("contributions-delete", access_token, {"confirm": "DELETE_MY_SHARED_REPORTS"})
