from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
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
_ALLOWED_HOST_SUFFIX = ".safetyreport.go.kr"
_ALLOWED_HOSTS = {"safetyreport.go.kr", "www.safetyreport.go.kr"}
_USER_AGENT = "safetyreport-media-proxy/1.0"
_CHUNK_SIZE = 1024 * 256
_CACHE_DIR_NAME = "media_cache"
_CACHE_MAX_AGE_SECONDS = 7 * 86400
_PRIME_WORKERS = 4

from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

_session = requests.Session()
_retry_strategy = Retry(
    total=settings.max_retry_attemps,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["HEAD", "GET", "OPTIONS"],
    backoff_factor=1,
)
_adapter = HTTPAdapter(max_retries=_retry_strategy)
_session.mount("https://", _adapter)
_session.mount("http://", _adapter)

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_prime_executor = ThreadPoolExecutor(max_workers=_PRIME_WORKERS, thread_name_prefix="media-cache")
_prime_futures: dict[str, Future[Path]] = {}
_prime_errors: dict[str, str] = {}
_prime_guard = threading.Lock()


def _validate_remote_url(url: str) -> str:
    normalized = str(url or "").strip()
    parsed = urlparse(normalized)
    if parsed.scheme not in _ALLOWED_SCHEMES or not parsed.netloc:
        raise ValueError("invalid remote media url")
    hostname = (parsed.hostname or "").lower()
    if hostname not in _ALLOWED_HOSTS and not hostname.endswith(_ALLOWED_HOST_SUFFIX):
        raise ValueError("unsupported remote media host")
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


def _is_cached_file(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 0


def guess_media_type(url: str) -> str:
    parsed_path = urlparse(url).path
    return mimetypes.guess_type(parsed_path)[0] or "application/octet-stream"


def ensure_cached(url: str) -> Path:
    """Download `url` fully into local cache (idempotent). Returns cache file path."""
    normalized = _validate_remote_url(url)
    path = _cache_path(normalized)
    if _is_cached_file(path):
        return path

    lock = _get_lock(normalized)
    with lock:
        if _is_cached_file(path):
            return path
        tmp = path.with_suffix(".tmp")
        try:
            with _session.get(
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


def _finish_prime(url: str, future: Future[Path]) -> None:
    error = None
    if future.cancelled():
        error = "cancelled"
    else:
        exc = future.exception()
        if exc:
            error = str(exc)

    with _prime_guard:
        if _prime_futures.get(url) is future:
            _prime_futures.pop(url, None)
        if error:
            _prime_errors[url] = error
        else:
            _prime_errors.pop(url, None)


def get_cache_status(url: str) -> dict[str, object]:
    normalized = _validate_remote_url(url)
    path = _cache_path(normalized)
    if _is_cached_file(path):
        return {
            "status": "ready",
            "ready": True,
            "bytes": path.stat().st_size,
        }

    with _prime_guard:
        future = _prime_futures.get(normalized)
        error = _prime_errors.get(normalized)

    if future is not None and not future.done():
        return {"status": "pending", "ready": False}
    if error:
        return {"status": "error", "ready": False, "error": error}
    return {"status": "missing", "ready": False}


def prime_cache(url: str) -> dict[str, object]:
    normalized = _validate_remote_url(url)
    status = get_cache_status(normalized)
    if status["ready"]:
        return status
    if status["status"] == "pending":
        return status

    with _prime_guard:
        future = _prime_futures.get(normalized)
        if future is None or future.done():
            _prime_errors.pop(normalized, None)
            future = _prime_executor.submit(ensure_cached, normalized)
            _prime_futures[normalized] = future
            future.add_done_callback(lambda done, key=normalized: _finish_prime(key, done))

    return {"status": "pending", "ready": False}


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
