"""모바일 API 의 신고 분류 호환(스키마 6, 2026-10-11).

서버는 5분류(traffic·parking·safety·living·other)를 쓰지만 이 기능을 모르는 앱(구앱·크롬 확장)은 3분류만 안다.
5분류를 아는 클라이언트는 `X-SR-Taxonomy: 5` 헤더(또는 `?taxonomy=5`)를 보낸다. 보내지 않은 요청의 `/api/v1/*` JSON 응답은
모든 `category`·`previous_category` 값의 safety·living 을 other 로 접어 보낸다(예전 '기타'에 들어 있던 신고들).
`/api/v1/reports/other` 처럼 분류로 고르는 요청은 라우터가 expand_legacy 로 묶음을 고른다(want_five 참고).
"""
from __future__ import annotations

import json

from fastapi import Request
from starlette.responses import Response

from services import report_category

HEADER = "X-SR-Taxonomy"
FIVE = "5"
_FOLD_KEYS = ("category", "previous_category")


def wants_five(request: Request) -> bool:
    return (request.headers.get(HEADER) or request.query_params.get("taxonomy") or "").strip() == FIVE


def fold(value):
    """JSON 값 안의 category 값을 3분류로. 다른 값은 그대로."""
    if isinstance(value, dict):
        return {k: (report_category.legacy_category(v) if k in _FOLD_KEYS and isinstance(v, str)
                    and v in report_category.CATEGORIES else fold(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [fold(v) for v in value]
    return value


async def taxonomy_compat_middleware(request: Request, call_next):
    response = await call_next(request)
    if not request.url.path.startswith("/api/v1/") or wants_five(request):
        return response
    if not (response.headers.get("content-type") or "").startswith("application/json"):
        return response
    body = b"".join([chunk async for chunk in response.body_iterator])
    try:
        payload = json.loads(body)
    except ValueError:
        return Response(content=body, status_code=response.status_code, headers=dict(response.headers),
                        media_type=response.media_type)
    folded = fold(payload)
    content = body if folded == payload else json.dumps(folded, ensure_ascii=False, allow_nan=False,
                                                         separators=(",", ":")).encode("utf-8")
    headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
    return Response(content=content, status_code=response.status_code, headers=headers, media_type="application/json")
