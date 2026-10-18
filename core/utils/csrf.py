"""세션 기반 CSRF 확인(관리자 웹과 JSON POST).

토큰은 항상 필수이며 JSON 전용 라우트는 application/json 도 요구한다.
Sec-Fetch-Site cross-site 는 거절, same-origin/none 은 프록시와 무관하게
토큰 검사로 진행한다. same-site/알 수 없는 값은 scheme·host·port 를
엄격 비교한다. Fetch Metadata 가 없는 구형 브라우저는 Origin 의 host·port 를
현재/원래 Host 및 원래 X-Forwarded-Host 와 비교하고 scheme 차이는 허용한다.
"""
from __future__ import annotations

import hmac
import secrets
from core.utils.proxy_headers import origin_tuple

from starlette.requests import Request

SESSION_KEY = "csrf_token"
HEADER = "x-csrf-token"


def get_or_create_token(request: Request) -> str:
    token = request.session.get(SESSION_KEY)
    if not isinstance(token, str) or len(token) < 32:
        token = secrets.token_urlsafe(32)
        request.session[SESSION_KEY] = token
    return token


def _origin_allowed(request: Request, origin: str) -> bool:
    supplied = origin_tuple(origin)
    expected = origin_tuple(f'{request.url.scheme}://{request.headers.get("host", "")}')
    return supplied is not None and supplied == expected


def _legacy_origin_allowed(request: Request, origin: str) -> bool:
    supplied = origin_tuple(origin)
    if supplied is None:
        return False

    def authority(parsed):
        scheme, host, port = parsed
        return host, None if port == (443 if scheme == 'https' else 80) else port

    candidates = [request.headers.get('host', '')]
    candidates.extend(request.scope.get('safetyreport.original_host', ()))
    for value in request.scope.get('safetyreport.original_forwarded_host', ()):
        candidates.extend(part.strip() for part in value.split(','))
    for candidate in candidates:
        # Host has no scheme: consider both the internal and external scheme for
        # default-port notation (HTTPS Origin x also matches Host x:443).
        for scheme in (request.url.scheme, supplied[0]):
            parsed = origin_tuple(f'{scheme}://{candidate}')
            if parsed is not None and authority(parsed) == authority(supplied):
                return True
    return False


def verify_json_post(request: Request) -> str | None:
    """통과하면 None, 아니면 거부 사유 코드."""
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if content_type != "application/json":
        return "content_type"
    return verify_token(request, request.headers.get(HEADER))


def verify_token(request: Request, supplied: str | None) -> str | None:
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site is not None:
        fetch_site = fetch_site.lower()
    if fetch_site == "cross-site":
        return "cross_site"
    origin = request.headers.get("origin")
    if fetch_site not in ("same-origin", "none") and origin is not None:
        allowed = _legacy_origin_allowed if fetch_site is None else _origin_allowed
        if not allowed(request, origin):
            return "origin"
    expected = request.session.get(SESSION_KEY)
    if not isinstance(expected, str) or not expected or not supplied:
        return "csrf"
    if not hmac.compare_digest(expected.encode("utf-8"), supplied.encode("utf-8")):
        return "csrf"
    return None
