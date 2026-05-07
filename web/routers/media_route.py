from __future__ import annotations

import asyncio
import re

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from services import media_proxy_service


router = APIRouter(prefix="/media")

_RANGE_RE = re.compile(r"^bytes=(\d+)-(\d*)$")
_CHUNK_SIZE = 1024 * 256


@router.get("/proxy")
async def proxy_media(request: Request, url: str = Query(...)):
    try:
        cache_path = await asyncio.to_thread(media_proxy_service.ensure_cached, url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"media proxy failed: {exc}") from exc

    file_size = cache_path.stat().st_size
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

    def iter_file():
        with open(cache_path, "rb") as fh:
            fh.seek(start)
            remaining = length
            while remaining > 0:
                chunk = fh.read(min(_CHUNK_SIZE, remaining))
                if not chunk:
                    break
                yield chunk
                remaining -= len(chunk)

    return StreamingResponse(
        iter_file(),
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
