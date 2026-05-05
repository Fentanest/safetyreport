from __future__ import annotations

import io
import os
import tempfile
import zipfile
from datetime import datetime

import settings.settings as settings
from core.utils import logger


ALLOWED_BROWSER_DIRS = {
    "logs": settings.logpath,
    "results": settings.resultpath,
}
ALLOWED_API_ROOTS = frozenset(ALLOWED_BROWSER_DIRS.keys())


def get_protected_paths() -> set[str]:
    protected = set(logger.LoggerFactory._active_log_paths)
    protected.add(os.path.abspath(os.path.join(settings.logpath, "current_crawl.log")))
    protected.add(os.path.abspath(os.path.join(settings.logpath, "current_rating.log")))
    return protected


def _format_timestamp(path: str) -> str:
    return datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M:%S")


def list_browser_groups():
    files = {label: [] for label in ALLOWED_BROWSER_DIRS}
    for label, directory in ALLOWED_BROWSER_DIRS.items():
        os.makedirs(directory, exist_ok=True)
        for filename in os.listdir(directory):
            path = os.path.join(directory, filename)
            if not os.path.isfile(path):
                continue
            files[label].append({
                "name": filename,
                "dir": label,
                "path": path,
                "size": os.path.getsize(path),
                "mtime": _format_timestamp(path),
            })
        files[label].sort(key=lambda item: item["mtime"], reverse=True)
    return files


def ensure_browser_file(path: str) -> str:
    abs_path = os.path.abspath(path)
    if not any(abs_path.startswith(os.path.abspath(root)) for root in ALLOWED_BROWSER_DIRS.values()):
        raise PermissionError("Access denied")
    if not os.path.exists(abs_path) or not os.path.isfile(abs_path):
        raise FileNotFoundError("File not found")
    return abs_path


def build_download_zip(paths):
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "a", zipfile.ZIP_DEFLATED, False) as archive:
        for path in paths:
            try:
                resolved = ensure_browser_file(path)
            except Exception:
                continue
            archive.write(resolved, os.path.basename(resolved))
    zip_buffer.seek(0)
    filename = f"safetyreport_files_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    return zip_buffer, filename


def delete_file(path: str):
    abs_path = ensure_browser_file(path)
    if abs_path in get_protected_paths():
        raise RuntimeError("현재 사용 중인 로그 파일은 삭제할 수 없습니다.")
    os.remove(abs_path)


def delete_files(paths):
    deleted_count = 0
    errors = []
    protected = get_protected_paths()

    for path in paths:
        abs_path = os.path.abspath(path)
        if not any(abs_path.startswith(os.path.abspath(root)) for root in ALLOWED_BROWSER_DIRS.values()):
            errors.append(f"{os.path.basename(path)}: Access denied")
            continue
        if abs_path in protected:
            errors.append(f"{os.path.basename(path)}: 현재 사용 중인 로그 파일은 삭제 대상에서 제외되었습니다.")
            continue
        if os.path.exists(abs_path):
            try:
                os.remove(abs_path)
                deleted_count += 1
            except Exception as exc:
                errors.append(f"{os.path.basename(path)}: {exc}")

    return deleted_count, errors


def delete_all_in_target(target: str):
    directory = ALLOWED_BROWSER_DIRS.get(target)
    if not directory:
        raise ValueError("Invalid target")
    if not os.path.exists(directory):
        return 0

    deleted_count = 0
    protected = get_protected_paths()
    for filename in os.listdir(directory):
        path = os.path.join(directory, filename)
        abs_path = os.path.abspath(path)
        if not os.path.isfile(path) or abs_path in protected:
            continue
        try:
            os.remove(abs_path)
            deleted_count += 1
        except Exception:
            pass
    return deleted_count


def list_api_entries(path: str = ""):
    base = os.path.abspath(settings.datapath)
    if not path:
        items = []
        for name in sorted(ALLOWED_API_ROOTS):
            full = os.path.join(base, name)
            if os.path.exists(full):
                items.append({
                    "name": name,
                    "path": name,
                    "is_dir": True,
                    "size": None,
                    "modified": datetime.fromtimestamp(os.path.getmtime(full)).strftime("%Y-%m-%d %H:%M"),
                })
        return "/", items

    first = path.replace("\\", "/").split("/")[0]
    if first not in ALLOWED_API_ROOTS:
        raise PermissionError("접근 불가")

    target = os.path.normpath(os.path.join(base, path))
    if not target.startswith(base):
        raise PermissionError("접근 불가")
    if not os.path.exists(target):
        raise FileNotFoundError("경로를 찾을 수 없습니다")
    if not os.path.isdir(target):
        raise NotADirectoryError("파일 경로는 지원하지 않습니다")

    entries = sorted(
        os.listdir(target),
        key=lambda name: (not os.path.isdir(os.path.join(target, name)), name.lower()),
    )
    items = []
    for name in entries:
        full = os.path.join(target, name)
        rel = os.path.relpath(full, base)
        is_dir = os.path.isdir(full)
        items.append({
            "name": name,
            "path": rel,
            "is_dir": is_dir,
            "size": None if is_dir else os.path.getsize(full),
            "modified": datetime.fromtimestamp(os.path.getmtime(full)).strftime("%Y-%m-%d %H:%M"),
        })
    return path, items


def resolve_api_file(path: str):
    base = os.path.abspath(settings.datapath)
    first = path.replace("\\", "/").split("/")[0]
    if first not in ALLOWED_API_ROOTS:
        raise PermissionError("접근 불가")

    target = os.path.normpath(os.path.join(base, path))
    if not target.startswith(base):
        raise PermissionError("접근 불가")
    if not os.path.exists(target):
        raise FileNotFoundError("파일을 찾을 수 없습니다")
    if os.path.isdir(target):
        raise IsADirectoryError("디렉토리는 다운로드할 수 없습니다")
    return target


def snapshot_live_log_if_needed(path: str):
    live_logs = {
        os.path.abspath(os.path.join(settings.logpath, "current_crawl.log")),
        os.path.abspath(os.path.join(settings.logpath, "current_rating.log")),
    }
    abs_path = os.path.abspath(path)
    if abs_path not in live_logs:
        return abs_path, None

    fd, tmp_path = tempfile.mkstemp(
        prefix="safetyreport_log_snapshot_",
        suffix=os.path.splitext(abs_path)[1] or ".log",
    )
    os.close(fd)
    from shutil import copy2

    copy2(abs_path, tmp_path)
    return tmp_path, tmp_path
