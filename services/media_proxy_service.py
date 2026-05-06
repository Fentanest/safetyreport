from __future__ import annotations

import mimetypes
from collections.abc import Iterator
from urllib.parse import urlparse

import requests


_ALLOWED_SCHEMES = {"http", "https"}
_USER_AGENT = "safetyreport-media-proxy/1.0"


def _validate_remote_url(url: str) -> str:
    normalized = str(url or "").strip()
    parsed = urlparse(normalized)
    if parsed.scheme not in _ALLOWED_SCHEMES or not parsed.netloc:
        raise ValueError("invalid remote media url")
    return normalized


def _iter_stream(response: requests.Response) -> Iterator[bytes]:
    try:
        for chunk in response.iter_content(chunk_size=1024 * 256):
            if chunk:
                yield chunk
    finally:
        response.close()


def open_media_stream(url: str, *, range_header: str = "") -> tuple[int, str, dict[str, str], Iterator[bytes]]:
    normalized_url = _validate_remote_url(url)
    headers = {"User-Agent": _USER_AGENT}
    if range_header:
        headers["Range"] = range_header

    response = requests.get(normalized_url, headers=headers, stream=True, timeout=(10, 120), allow_redirects=True)
    if response.status_code >= 400:
        response.close()
        raise RuntimeError(f"upstream returned {response.status_code}")

    parsed_path = urlparse(normalized_url).path
    media_type = response.headers.get("Content-Type") or mimetypes.guess_type(parsed_path)[0] or "application/octet-stream"
    passthrough_headers: dict[str, str] = {}
    for header_name in ("Accept-Ranges", "Content-Length", "Content-Range", "Cache-Control", "ETag", "Last-Modified"):
        value = response.headers.get(header_name)
        if value:
            passthrough_headers[header_name] = value
    passthrough_headers.setdefault("Accept-Ranges", "bytes")

    return response.status_code, media_type, passthrough_headers, _iter_stream(response)
