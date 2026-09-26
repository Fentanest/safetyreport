"""세션 기반 CSRF 확인(관리자 웹의 JSON POST 용).

세션 쿠키(SameSite=Lax)만으로는 같은 사이트의 다른 포트·하위 도메인 페이지가 보내는 요청을 막지 못하므로,
커뮤니티 계정처럼 계정을 바꾸는 동작은 아래를 모두 요구한다.
- 헤더 X-CSRF-Token == request.session["csrf_token"] (페이지 렌더링 때 발급)
- Content-Type: application/json (단순 form POST 불가 → 교차 출처면 브라우저가 preflight)
- Origin 헤더가 있으면 이 요청의 scheme://host 와 같아야 한다. Sec-Fetch-Site: cross-site 는 거부.
"""
from __future__ import annotations

import hmac
import secrets
from urllib.parse import urlsplit

from starlette.requests import Request

SESSION_KEY = "csrf_token"
HEADER = "x-csrf-token"


def get_or_create_token(request: Request) -> str:
    token = request.session.get(SESSION_KEY)
    if not isinstance(token, str) or len(token) < 32:
        token = secrets.token_urlsafe(32)
        request.session[SESSION_KEY] = token
    return token


def _trusted_proxy_configured() -> bool:
    try:
        import settings.settings as app_settings

        return bool((app_settings.trusted_proxies or "").strip())
    except Exception:
        return False


def _origin_allowed(request: Request, origin: str) -> bool:
    if origin == "null":
        return False
    try:
        parts = urlsplit(origin)
    except ValueError:
        return False
    if parts.scheme not in ("http", "https") or not parts.netloc or parts.path not in ("", "/"):
        return False
    host = (request.headers.get("host") or "").lower()
    hosts = {host} if host else set()
    if _trusted_proxy_configured():
        forwarded = (request.headers.get("x-forwarded-host") or "").split(",")[0].strip().lower()
        if forwarded:
            hosts.add(forwarded)
    if parts.netloc.lower() not in hosts:
        return False
    # 리버스 프록시가 TLS 를 끝내면 내부 scheme 이 http 일 수 있다. 신뢰 프록시가 설정된 경우에만 scheme 차이를 허용.
    return parts.scheme == request.url.scheme or _trusted_proxy_configured()


def verify_json_post(request: Request) -> str | None:
    """통과하면 None, 아니면 거부 사유 코드."""
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if content_type != "application/json":
        return "content_type"
    if (request.headers.get("sec-fetch-site") or "").lower() == "cross-site":
        return "cross_site"
    origin = request.headers.get("origin")
    if origin is not None and not _origin_allowed(request, origin):
        return "origin"
    expected = request.session.get(SESSION_KEY)
    supplied = request.headers.get(HEADER)
    if not isinstance(expected, str) or not expected or not supplied:
        return "csrf"
    if not hmac.compare_digest(expected.encode("utf-8"), supplied.encode("utf-8")):
        return "csrf"
    return None
