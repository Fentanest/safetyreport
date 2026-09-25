"""safeauth 중계 + Supabase Auth(GoTrue) HTTP 클라이언트 (프로토콜 1).

정본: safetyreport-community-auth `docs/protocol.md`, `server/protocol.ts`·`relay.ts`.
- 비밀값(verifier, device_secret, delivery_key, auth_code, 토큰)은 요청 본문·헤더로만 보내고 URL·로그에 넣지 않는다.
- 리다이렉트를 따르지 않는다. 타임아웃 10초.
- 예외는 원문 응답을 담지 않는다(오류 코드와 HTTP 상태만).
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import unicodedata
from urllib.parse import parse_qsl

import requests

PROTOCOL_VERSION = 1
RELAY_FUNCTION = "community-auth-relay"
TIMEOUT_SECONDS = 10

SECRET_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
AUTH_CODE_RE = re.compile(r"^[A-Za-z0-9._~-]{8,512}$")
DISPLAY_CODE_RE = re.compile(r"^[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}$")
DEVICE_LABEL_MAX = 40
_LABEL_FORBIDDEN = re.compile("[\u0000-\u001f\u007f-\u009f​-‏‪-‮⁦-⁩<>\"'`\\\\]")
_LABEL_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*:", re.IGNORECASE)

# Supabase Auth 가 refresh 토큰을 더 이상 받지 않는다는 뜻의 오류 코드 → "다시 로그인 필요"
REAUTH_ERROR_CODES = frozenset({
    "refresh_token_not_found", "refresh_token_already_used", "session_not_found", "session_expired",
    "invalid_grant", "user_not_found", "user_banned",
})


# ── 오류 ──────────────────────────────────────────────────────────────────────

class CommunityHttpError(RuntimeError):
    """중계/Auth 호출 실패. code 는 프로토콜 오류 코드 또는 로컬 분류."""

    def __init__(self, code: str, status: int | None = None, retry_after: float | None = None):
        super().__init__(code)
        self.code = code
        self.status = status
        self.retry_after = retry_after

    @property
    def transient(self) -> bool:
        return self.code in ("network_error", "server_error", "rate_limited", "capacity") or (
            self.status is not None and self.status >= 500
        )


class RelayError(CommunityHttpError):
    pass


class AuthError(CommunityHttpError):
    @property
    def reauth_required(self) -> bool:
        return self.code in REAUTH_ERROR_CODES


# ── 도우미 ────────────────────────────────────────────────────────────────────

def pkce_challenge(verifier: str) -> str:
    """RFC 7636 S256: base64url(SHA-256(ascii(verifier))) 패딩 없이."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def normalize_device_label(value) -> str | None:
    """protocol.ts normalizeDeviceLabel 과 같은 규칙. 통과 못 하면 None."""
    if not isinstance(value, str):
        return None
    label = re.sub(r"\s+", " ", unicodedata.normalize("NFC", value)).strip()
    if not label or len(label) > DEVICE_LABEL_MAX:
        return None
    if _LABEL_FORBIDDEN.search(label) or _LABEL_SCHEME.match(label):
        return None
    return label


def parse_bootstrap_url(url: str, site_url: str) -> tuple[str, str] | None:
    """`<site_url>#r=<uuid>&t=<ticket>` 만 받아들인다. (request_id, ticket) 또는 None."""
    if not isinstance(url, str) or len(url) > 2048 or not url.startswith(site_url + "#"):
        return None
    fragment = url[len(site_url) + 1:]
    if not fragment or len(fragment) > 256:
        return None
    pairs = parse_qsl(fragment, keep_blank_values=True, strict_parsing=False)
    keys = [k for k, _ in pairs]
    if sorted(keys) != ["r", "t"]:
        return None
    values = dict(pairs)
    if not UUID_RE.match(values["r"]) or not SECRET_RE.match(values["t"]):
        return None
    return values["r"], values["t"]


def _json_or_empty(response) -> dict:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _retry_after(response, body_error: dict | None) -> float | None:
    raw = response.headers.get("Retry-After") if response is not None else None
    for candidate in (raw, (body_error or {}).get("retryAfterSeconds")):
        try:
            if candidate is not None:
                return max(0.0, float(candidate))
        except (TypeError, ValueError):
            continue
    return None


# ── 클라이언트 ────────────────────────────────────────────────────────────────

class CommunityAuthClient:
    def __init__(self, supabase_url: str, publishable_key: str, session: requests.Session | None = None):
        self.base = supabase_url.rstrip("/")
        self.publishable_key = publishable_key
        self.http = session or requests.Session()

    # 중계 -------------------------------------------------------------------
    def _relay(self, action: str, body: dict, headers: dict | None = None) -> dict:
        url = f"{self.base}/functions/v1/{RELAY_FUNCTION}/{action}"
        try:
            response = self.http.post(
                url, data=json.dumps({"protocol": PROTOCOL_VERSION, **body}),
                headers={"Content-Type": "application/json", "Accept": "application/json",
                         "apikey": self.publishable_key, **(headers or {})},
                timeout=TIMEOUT_SECONDS, allow_redirects=False)
        except requests.RequestException:
            raise RelayError("network_error") from None
        data = _json_or_empty(response)
        if 200 <= response.status_code < 300:
            if data.get("protocol") != PROTOCOL_VERSION:
                raise RelayError("server_error", response.status_code)
            return data
        err = data.get("error") if isinstance(data.get("error"), dict) else {}
        code = err.get("code") if isinstance(err.get("code"), str) else None
        if response.status_code == 429:
            retry_after = _retry_after(response, err)
            raise RelayError(code or "rate_limited", 429, 30.0 if retry_after is None else retry_after)
        if code is None or not re.fullmatch(r"[a-z_]{2,40}", code):
            code = "server_error" if response.status_code >= 500 or response.status_code < 400 else "invalid_response"
        raise RelayError(code, response.status_code, _retry_after(response, err))

    def create_request(self, *, client_kind: str, device_label: str, code_challenge: str,
                       device_secret: str, installation_id: str | None) -> dict:
        body = {"client_kind": client_kind, "device_label": device_label, "code_challenge": code_challenge,
                "code_challenge_method": "s256", "device_secret": device_secret}
        if installation_id:
            body["installation_id"] = installation_id
        return self._relay("requests", body)

    def poll(self, *, request_id: str, device_secret: str, delivery_key: str) -> dict:
        return self._relay("poll", {"request_id": request_id, "device_secret": device_secret,
                                    "delivery_key": delivery_key})

    def complete(self, *, request_id: str, device_secret: str, access_token: str) -> dict:
        return self._relay("complete", {"request_id": request_id, "device_secret": device_secret},
                           headers={"Authorization": f"Bearer {access_token}"})

    def cancel(self, *, request_id: str, device_secret: str) -> dict:
        return self._relay("cancel", {"request_id": request_id, "actor": "device", "secret": device_secret})

    # Supabase Auth ---------------------------------------------------------
    def _auth(self, method: str, path: str, *, body: dict | None = None, access_token: str | None = None):
        url = f"{self.base}/auth/v1/{path}"
        headers = {"Accept": "application/json", "apikey": self.publishable_key}
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        try:
            if method == "GET":
                response = self.http.get(url, headers=headers, timeout=TIMEOUT_SECONDS, allow_redirects=False)
            else:
                headers["Content-Type"] = "application/json"
                response = self.http.post(url, data=json.dumps(body or {}), headers=headers,
                                          timeout=TIMEOUT_SECONDS, allow_redirects=False)
        except requests.RequestException:
            raise AuthError("network_error") from None
        return response

    @staticmethod
    def _auth_error(response) -> AuthError:
        data = _json_or_empty(response)
        code = data.get("error_code") or data.get("error")
        if not isinstance(code, str) or not re.fullmatch(r"[a-z_]{2,60}", code):
            code = "server_error" if response.status_code >= 500 else f"http_{response.status_code}"
        if response.status_code == 429:
            return AuthError("rate_limited", 429, _retry_after(response, None))
        return AuthError(code, response.status_code)

    def exchange_code(self, *, auth_code: str, code_verifier: str) -> dict:
        response = self._auth("POST", "token?grant_type=pkce",
                              body={"auth_code": auth_code, "code_verifier": code_verifier})
        if response.status_code != 200:
            raise self._auth_error(response)
        return _session_from(response)

    def refresh(self, *, refresh_token: str) -> dict:
        response = self._auth("POST", "token?grant_type=refresh_token", body={"refresh_token": refresh_token})
        if response.status_code != 200:
            raise self._auth_error(response)
        return _session_from(response)

    def get_user(self, *, access_token: str) -> dict:
        response = self._auth("GET", "user", access_token=access_token)
        if response.status_code != 200:
            raise self._auth_error(response)
        data = _json_or_empty(response)
        if not isinstance(data.get("id"), str) or not data["id"]:
            raise AuthError("invalid_response", response.status_code)
        return data

    def logout_local(self, *, access_token: str) -> bool:
        """이 세션만 끝낸다. scope 를 빼면 GoTrue 기본값이 global(모든 기기)이라 늘 scope=local."""
        response = self._auth("POST", "logout?scope=local", access_token=access_token)
        if response.status_code in (200, 204):
            return True
        raise self._auth_error(response)


def _session_from(response) -> dict:
    data = _json_or_empty(response)
    access = data.get("access_token")
    refresh = data.get("refresh_token")
    if not isinstance(access, str) or not access or not isinstance(refresh, str) or not refresh:
        raise AuthError("invalid_response", response.status_code)
    return data


def jwt_claims_unverified(token: str) -> dict:
    """표시·형식 확인용으로만 JWT payload 를 읽는다(서명 검증 아님). 실패하면 빈 dict."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

