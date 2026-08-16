from __future__ import annotations

import asyncio
import re

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from services import media_proxy_service


router = APIRouter(prefix="/media")

_RANGE_RE = re.compile(r"^bytes=(\d+)-(\d*)$")


@router.get("/proxy")
async def proxy_media(request: Request, url: str = Query(...)):
    # 캐시가 완성될 때까지 기다리지 않는다. upstream Content-Length 만 확보되면
    # 진행 중인 .tmp 를 tail-follow 로 흘려보내 첫 바이트 지연을 없앤다.
    try:
        source = await asyncio.to_thread(media_proxy_service.open_stream, url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"media proxy failed: {exc}") from exc

    file_size = source["total"]
    media_type = media_proxy_service.guess_media_type(url)
    range_header = request.headers.get("range", "").strip()

    start = 0
    end = file_size - 1
    status_code = 200
    response_headers: dict[str, str] = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, max-age=86400",
    }

    if range_header:
        match = _RANGE_RE.match(range_header)
        if match:
            start = int(match.group(1))
            end_str = match.group(2)
            end = int(end_str) if end_str else file_size - 1
            end = min(end, file_size - 1)
            if start > end or start >= file_size:
                raise HTTPException(status_code=416, detail="range not satisfiable")
            status_code = 206
            response_headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"

    length = end - start + 1
    response_headers["Content-Length"] = str(length)

    return StreamingResponse(
        media_proxy_service.iter_stream(source, start, end),
        status_code=status_code,
        media_type=media_type,
        headers=response_headers,
    )


@router.post("/prepare")
async def prepare_media(url: str = Query(...)):
    try:
        status = await asyncio.to_thread(media_proxy_service.prime_cache, url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"media prepare failed: {exc}") from exc
    return JSONResponse(status)


@router.get("/status")
async def media_status(url: str = Query(...)):
    try:
        status = await asyncio.to_thread(media_proxy_service.get_cache_status, url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"media status failed: {exc}") from exc
    return JSONResponse(status)
