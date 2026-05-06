from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from services import media_proxy_service


router = APIRouter(prefix="/media")


@router.get("/proxy")
async def proxy_media(request: Request, url: str = Query(...)):
    try:
        status_code, media_type, headers, stream = media_proxy_service.open_media_stream(
            url,
            range_header=request.headers.get("range", ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"media proxy failed: {exc}") from exc

    return StreamingResponse(stream, status_code=status_code, media_type=media_type, headers=headers)
