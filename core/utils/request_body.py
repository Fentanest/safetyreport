"""JSON 객체를 받는 요청의 공통 입력 경계."""
import json

from fastapi import HTTPException
from starlette.requests import Request


async def json_object(request: Request) -> dict:
    try:
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > 1024 * 1024:
                raise HTTPException(status_code=413, detail='JSON body is too large')
            raw.extend(chunk)
        body = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="JSON body must be an object")
    for name in ('report_number', 'action', 'crawl_mode', 'queue_list', 'representative_id',
                 'duplicate_status', 'representative_mode', 'note', 'target', 'cause'):
        if name in body and body[name] is not None and not isinstance(body[name], str):
            raise HTTPException(status_code=400, detail=f'{name} must be a string')
    if 'values' in body and not isinstance(body['values'], dict):
        raise HTTPException(status_code=400, detail='values must be an object')
    return body


def string_list(body, name, *, limit=10000):
    values = body.get(name, [])
    if not isinstance(values, list) or len(values) > limit or any(not isinstance(value, str) or not value.strip() for value in values):
        raise HTTPException(status_code=400, detail=f'{name} must be a list of nonempty strings')
    return values
