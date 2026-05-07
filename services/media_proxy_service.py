from __future__ import annotations

import hashlib
import mimetypes
import os
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

import settings.settings as settings


_ALLOWED_SCHEMES = {"http", "https"}
_USER_AGENT = "safetyreport-media-proxy/1.0"
_CHUNK_SIZE = 1024 * 256
_CACHE_DIR_NAME = "media_cache"
_CACHE_MAX_AGE_SECONDS = 7 * 86400

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _validate_remote_url(url: str) -> str:
    normalized = str(url or "").strip()
    parsed = urlparse(normalized)
    if parsed.scheme not in _ALLOWED_SCHEMES or not parsed.netloc:
        raise ValueError("invalid remote media url")
    return normalized


def _cache_dir() -> Path:
    base = Path(settings.datapath) / _CACHE_DIR_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


def _cache_path(url: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return _cache_dir() / digest


def _get_lock(url: str) -> threading.Lock:
    with _locks_guard:
        lock = _locks.get(url)
        if lock is None:
            lock = threading.Lock()
            _locks[url] = lock
        return lock


def guess_media_type(url: str) -> str:
    parsed_path = urlparse(url).path
    return mimetypes.guess_type(parsed_path)[0] or "application/octet-stream"


def ensure_cached(url: str) -> Path:
    """Download `url` fully into local cache (idempotent). Returns cache file path."""
    normalized = _validate_remote_url(url)
    path = _cache_path(normalized)
    if path.exists() and path.stat().st_size > 0:
        return path

    lock = _get_lock(normalized)
    with lock:
        if path.exists() and path.stat().st_size > 0:
            return path
        tmp = path.with_suffix(".tmp")
        try:
            with requests.get(
                normalized,
                headers={"User-Agent": _USER_AGENT},
                stream=True,
                timeout=(10, 600),
                allow_redirects=True,
            ) as response:
                if response.status_code >= 400:
                    raise RuntimeError(f"upstream returned {response.status_code}")
                with open(tmp, "wb") as out:
                    for chunk in response.iter_content(chunk_size=_CHUNK_SIZE):
                        if chunk:
                            out.write(chunk)
            os.replace(tmp, path)
        except Exception:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass
            raise
    return path


def cleanup_cache(max_age_seconds: int = _CACHE_MAX_AGE_SECONDS) -> int:
    """Remove cache files older than `max_age_seconds`. Returns count removed."""
    removed = 0
    cutoff = time.time() - max_age_seconds
    try:
        entries = list(_cache_dir().iterdir())
    except FileNotFoundError:
        return 0
    for entry in entries:
        try:
            if entry.is_file() and entry.stat().st_mtime < cutoff:
                entry.unlink()
                removed += 1
        except Exception:
            pass
    return removed
