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
# tail-follow 재생용 파라미터.
# upstream 헤더(Content-Length)만 잡히면 곧바로 서빙을 시작하고, 그 뒤로는
# 다운로더가 써 내려가는 .tmp 를 따라가며 읽는다.
_HEADER_WAIT_TIMEOUT_SECONDS = 30
_STALL_TIMEOUT_SECONDS = 120
_FOLLOW_POLL_SECONDS = 0.1

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
# URL 별 다운로드 진행 상태. tail-follow 리더가 "지금 어디까지 읽어도 되는지" 판단하는 근거.
# 파일 크기(os.stat)가 아니라 이 카운터를 쓴다 — flush 이후에만 올리므로 미완성 버퍼를 읽지 않는다.
_progress: dict[str, dict] = {}
_progress_state_guard = threading.Lock()
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


def _tmp_path(path: Path) -> Path:
    return path.with_suffix(".tmp")


def _progress_reset(url: str) -> None:
    with _progress_state_guard:
        _progress[url] = {"total": None, "downloaded": 0, "done": False, "error": None}


def _progress_set(url: str, **values) -> None:
    with _progress_state_guard:
        info = _progress.get(url)
        if info is None:
            info = {"total": None, "downloaded": 0, "done": False, "error": None}
            _progress[url] = info
        info.update(values)


def _progress_get(url: str) -> dict | None:
    with _progress_state_guard:
        info = _progress.get(url)
        return dict(info) if info else None


def guess_media_type(url: str) -> str:
    parsed_path = urlparse(url).path
    return mimetypes.guess_type(parsed_path)[0] or "application/octet-stream"


def ensure_cached(url: str) -> Path:
    """Download `url` fully into local cache (idempotent). Returns cache file path.

    다운로드 중에도 `_progress` 로 총 크기/누적 바이트를 공개하므로,
    `open_stream()` 리더가 완료를 기다리지 않고 tail-follow 로 붙을 수 있다.
    """
    normalized = _validate_remote_url(url)
    path = _cache_path(normalized)
    if _is_cached_file(path):
        return path

    lock = _get_lock(normalized)
    with lock:
        if _is_cached_file(path):
            return path
        tmp = _tmp_path(path)
        _progress_reset(normalized)
        written = 0
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
                # 압축 전송이면 Content-Length 가 원본 바이트 수와 달라 Range 계산이 깨진다.
                # 그때는 total 을 공개하지 않아 리더가 완료까지 기다리도록 둔다.
                encoding = (response.headers.get("Content-Encoding") or "").strip().lower()
                declared = response.headers.get("Content-Length")
                if declared and encoding in ("", "identity"):
                    try:
                        _progress_set(normalized, total=int(declared))
                    except ValueError:
                        pass
                with open(tmp, "wb") as out:
                    for chunk in response.iter_content(chunk_size=_CHUNK_SIZE):
                        if chunk:
                            out.write(chunk)
                            # flush 이후에만 카운터를 올린다. 리더는 이 카운터까지만 읽는다.
                            out.flush()
                            written += len(chunk)
                            _progress_set(normalized, downloaded=written)
            os.replace(tmp, path)
            _progress_set(normalized, total=written, downloaded=written, done=True)
        except Exception as exc:
            _progress_set(normalized, error=str(exc), done=True)
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass
            raise
    return path


def _open_cache_reader(url: str, path: Path, tmp: Path):
    """tmp 우선으로 열되, 그 사이 완료(os.replace)됐으면 최종 파일로 폴백.

    POSIX 에서는 rename 후에도 이미 열린 fd 가 같은 inode 를 계속 가리키므로,
    tmp 를 먼저 연 리더는 다운로드 완료 뒤에도 그대로 끝까지 읽을 수 있다.
    """
    try:
        return open(tmp, "rb")
    except FileNotFoundError:
        return open(path, "rb")


def _available_bytes(url: str, source: dict) -> int:
    """지금 시점에 안전하게 읽을 수 있는 바이트 수."""
    if source["complete"]:
        return source["total"]
    info = _progress_get(url)
    if info is None:
        # 진행 정보가 없는데 최종 파일이 있으면 이미 완료된 것.
        path = _cache_path(url)
        if _is_cached_file(path):
            return path.stat().st_size
        return 0
    if info.get("error"):
        raise RuntimeError(info["error"])
    return int(info.get("downloaded") or 0)


def open_stream(url: str) -> dict:
    """캐시 완성 여부와 무관하게 재생 가능한 소스를 확보한다.

    - 캐시 완료면 최종 파일을 그대로 사용
    - 아니면 백그라운드 다운로드를 깨우고, upstream Content-Length 만 잡히는 즉시
      진행 중인 .tmp 를 소스로 반환 (첫 바이트까지 전체 다운로드를 기다리지 않음)
    - Content-Length 를 못 얻는 경우에만 기존처럼 완료까지 대기
    """
    normalized = _validate_remote_url(url)
    path = _cache_path(normalized)
    if _is_cached_file(path):
        return {
            "url": normalized,
            "path": path,
            "tmp": _tmp_path(path),
            "total": path.stat().st_size,
            "complete": True,
        }

    prime_cache(normalized)

    deadline = time.monotonic() + _HEADER_WAIT_TIMEOUT_SECONDS
    while True:
        if _is_cached_file(path):
            return {
                "url": normalized,
                "path": path,
                "tmp": _tmp_path(path),
                "total": path.stat().st_size,
                "complete": True,
            }
        info = _progress_get(normalized) or {}
        if info.get("error"):
            raise RuntimeError(info["error"])
        total = info.get("total")
        if total:
            return {
                "url": normalized,
                "path": path,
                "tmp": _tmp_path(path),
                "total": int(total),
                "complete": False,
            }
        if time.monotonic() >= deadline:
            break
        time.sleep(_FOLLOW_POLL_SECONDS)

    # Content-Length 미제공(chunked 등) → 레거시 경로: 전체 다운로드 완료까지 대기
    final = ensure_cached(normalized)
    return {
        "url": normalized,
        "path": final,
        "tmp": _tmp_path(final),
        "total": final.stat().st_size,
        "complete": True,
    }


def iter_stream(source: dict, start: int, end: int):
    """[start, end] 구간을 tail-follow 로 읽어 yield 한다 (end 포함)."""
    url = source["url"]
    remaining = end - start + 1
    if remaining <= 0:
        return

    handle = _open_cache_reader(url, source["path"], source["tmp"])
    position = start
    stall_deadline = time.monotonic() + _STALL_TIMEOUT_SECONDS
    last_available = -1
    try:
        handle.seek(position)
        while remaining > 0:
            available = _available_bytes(url, source)
            if available != last_available:
                last_available = available
                stall_deadline = time.monotonic() + _STALL_TIMEOUT_SECONDS

            if position >= available:
                info = _progress_get(url) or {}
                if source["complete"] or info.get("done"):
                    break  # 더 받을 게 없다
                if time.monotonic() >= stall_deadline:
                    raise RuntimeError("media stream stalled")
                time.sleep(_FOLLOW_POLL_SECONDS)
                continue

            want = min(_CHUNK_SIZE, remaining, available - position)
            chunk = handle.read(want)
            if not chunk:
                if time.monotonic() >= stall_deadline:
                    raise RuntimeError("media stream stalled")
                time.sleep(_FOLLOW_POLL_SECONDS)
                continue
            position += len(chunk)
            remaining -= len(chunk)
            yield chunk
    finally:
        handle.close()


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
        info = _progress_get(normalized) or {}
        return {
            "status": "pending",
            "ready": False,
            "bytes": int(info.get("downloaded") or 0),
            "total": int(info.get("total") or 0),
        }
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
            # 워커가 실제로 시작하기 전에 리더가 옛 진행 상태를 보고
            # 존재하지 않는 .tmp 를 열지 않도록 여기서 먼저 초기화한다.
            _progress_reset(normalized)
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

    # 캐시 파일이 사라진 URL 의 진행 상태는 같이 정리 (무한 누적 방지)
    with _progress_state_guard:
        stale = [
            key
            for key, info in _progress.items()
            if info.get("done") and not _cache_path(key).exists()
        ]
        for key in stale:
            _progress.pop(key, None)

    return removed
