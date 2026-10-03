from __future__ import annotations

import os
import shutil
import stat
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
    return {os.path.normcase(os.path.realpath(path)) for path in protected}


def _under_root(path: str, root: str) -> str:
    """명시한 루트 안의 경로만 허용한다. 링크/정션을 따라가지 않는다."""
    target, root = os.path.abspath(path), os.path.abspath(root)
    try:
        if os.path.commonpath([os.path.normcase(target), os.path.normcase(root)]) != os.path.normcase(root):
            raise PermissionError("Access denied")
        real_target, real_root = os.path.normcase(os.path.realpath(target)), os.path.normcase(os.path.realpath(root))
        if os.path.commonpath([real_target, real_root]) != real_root:
            raise PermissionError("Access denied")
    except ValueError as exc:
        raise PermissionError("Access denied") from exc
    current = target
    while True:
        if os.path.islink(current) or os.path.isjunction(current):
            raise PermissionError("Access denied")
        if os.path.normcase(current) == os.path.normcase(root):
            break
        current = os.path.dirname(current)
    if os.path.isfile(target) and os.stat(target).st_nlink > 1:
        raise PermissionError("Access denied")
    return target


def _api_path(path: str) -> str:
    if not isinstance(path, str):
        raise PermissionError("접근 불가")
    normalized = path.replace("\\", "/")
    first = normalized.split("/")[0]
    if first not in ALLOWED_API_ROOTS:
        raise PermissionError("접근 불가")
    base = os.path.abspath(settings.datapath)
    return _under_root(os.path.join(base, normalized), os.path.join(base, first))


def _protected(path: str, protected: set[str]) -> bool:
    return os.path.normcase(os.path.realpath(path)) in protected


def _open_file(path: str, root: str):
    """POSIX에서는 각 경로 구성요소를 dirfd/O_NOFOLLOW로 연다."""
    path = _under_root(path, root)
    if os.open in os.supports_dir_fd and hasattr(os, "O_NOFOLLOW"):
        parts = os.path.relpath(path, root).split(os.sep)
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                os.close(directory)
                directory = child
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        finally:
            os.close(directory)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
                raise PermissionError('Access denied')
            return os.fdopen(fd, 'rb')
        except BaseException:
            os.close(fd)
            raise
    # Windows의 reparse/rename 경합은 해당 runner에서 별도로 검증한다.
    handle = open(path, "rb")
    try:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink > 1 or _under_root(path, root) != path:
            raise PermissionError("Access denied")
    except BaseException:
        handle.close()
        raise
    return handle


def open_browser_file(path: str):
    target = ensure_browser_file(path)
    for root in ALLOWED_BROWSER_DIRS.values():
        try:
            _under_root(target, root)
        except PermissionError:
            continue
        return _open_file(target, root)
    raise PermissionError("Access denied")


def open_api_file(path: str):
    target = resolve_api_file(path)
    first = path.replace("\\", "/").split("/")[0]
    return _open_file(target, os.path.join(settings.datapath, first))


def _unlink_file(path):
    for root in ALLOWED_BROWSER_DIRS.values():
        try:
            path = _under_root(path, root)
        except PermissionError:
            continue
        if os.unlink in os.supports_dir_fd and hasattr(os, 'O_NOFOLLOW'):
            parts = os.path.relpath(path, root).split(os.sep)
            directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                for part in parts[:-1]:
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                    os.close(directory)
                    directory = child
                info = os.stat(parts[-1], dir_fd=directory, follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
                    raise PermissionError('Access denied')
                os.unlink(parts[-1], dir_fd=directory)
            finally:
                os.close(directory)
        else:
            _under_root(path, root)
            os.remove(path)
        return
    raise PermissionError('Access denied')


def _format_timestamp(path: str) -> str:
    return datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M:%S")


def list_browser_groups():
    files = {label: [] for label in ALLOWED_BROWSER_DIRS}
    for label, directory in ALLOWED_BROWSER_DIRS.items():
        os.makedirs(directory, exist_ok=True)
        for filename in os.listdir(directory):
            path = os.path.join(directory, filename)
            try:
                path = _under_root(path, directory)
            except PermissionError:
                continue
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
    if not isinstance(path, str):
        raise PermissionError("Access denied")
    abs_path = os.path.abspath(path)
    for root in ALLOWED_BROWSER_DIRS.values():
        try:
            abs_path = _under_root(abs_path, root)
            break
        except PermissionError:
            continue
    else:
        raise PermissionError("Access denied")
    if not os.path.exists(abs_path) or not os.path.isfile(abs_path):
        raise FileNotFoundError("File not found")
    return abs_path


def _build_zip(paths, *, api: bool):
    if not isinstance(paths, list) or not paths or any(not isinstance(p, str) for p in paths):
        raise ValueError("paths must be a non-empty list of strings")
    selected = []
    names = set()
    for path in paths:
        target = resolve_api_file(path) if api else ensure_browser_file(path)
        if api:
            name = os.path.relpath(target, settings.datapath).replace(os.sep, "/")
        else:
            for label, root in ALLOWED_BROWSER_DIRS.items():
                try:
                    _under_root(target, root)
                except PermissionError:
                    continue
                name = label + "/" + os.path.relpath(target, root).replace(os.sep, "/")
                break
        if name in names:
            raise ValueError("duplicate archive filename")
        names.add(name)
        selected.append((path, name))
    fd, archive_path = tempfile.mkstemp(prefix="safetyreport_archive_", suffix=".zip")
    try:
        with os.fdopen(fd, "w+b") as output, zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            for path, name in selected:
                # 경로 검사 뒤 파일이 바뀌어도 ZIP에 링크 대상이 들어가지 않게
                # 검증한 fd로 읽는다. 실패 항목을 조용히 건너뛰지 않는다.
                with (open_api_file(path) if api else open_browser_file(path)) as source:
                    with archive.open(name, "w", force_zip64=True) as member:
                        shutil.copyfileobj(source, member, length=64 * 1024)
        filename = f"safetyreport_files_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        return archive_path, filename
    except BaseException:
        try:
            os.unlink(archive_path)
        except FileNotFoundError:
            pass
        raise


def build_download_zip(paths):
    return _build_zip(paths, api=False)


def build_api_download_zip(paths):
    return _build_zip(paths, api=True)


def delete_file(path: str):
    abs_path = ensure_browser_file(path)
    if _protected(abs_path, get_protected_paths()):
        raise RuntimeError("현재 사용 중인 로그 파일은 삭제할 수 없습니다.")
    _unlink_file(abs_path)


def delete_files(paths):
    deleted_count = 0
    errors = []
    protected = get_protected_paths()

    for path in paths:
        try:
            abs_path = ensure_browser_file(path)
        except (PermissionError, FileNotFoundError) as exc:
            errors.append(f"{os.path.basename(str(path))}: {exc}")
            continue
        if _protected(abs_path, protected):
            errors.append(f"{os.path.basename(path)}: 현재 사용 중인 로그 파일은 삭제 대상에서 제외되었습니다.")
            continue
        if os.path.exists(abs_path):
            try:
                _unlink_file(abs_path)
                deleted_count += 1
            except Exception as exc:
                errors.append(f"{os.path.basename(path)}: {exc}")

    return deleted_count, errors


def delete_api_files(paths):
    deleted_count = 0
    errors = []
    protected = get_protected_paths()

    for path in paths:
        try:
            resolved = resolve_api_file(path)
        except PermissionError:
            errors.append(f"{os.path.basename(path)}: 접근 불가")
            continue
        except FileNotFoundError:
            errors.append(f"{os.path.basename(path)}: 파일을 찾을 수 없습니다")
            continue
        except IsADirectoryError:
            errors.append(f"{os.path.basename(path)}: 디렉토리는 삭제할 수 없습니다")
            continue

        abs_path = os.path.abspath(resolved)
        if _protected(abs_path, protected):
            errors.append(
                f"{os.path.basename(path)}: 현재 사용 중인 로그 파일은 삭제 대상에서 제외되었습니다."
            )
            continue
        try:
            _unlink_file(abs_path)
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
        try:
            abs_path = _under_root(path, directory)
        except PermissionError:
            continue
        if not os.path.isfile(path) or _protected(abs_path, protected):
            continue
        try:
            _unlink_file(abs_path)
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
            try:
                full = _under_root(full, full)
            except PermissionError:
                continue
            if os.path.exists(full):
                items.append({
                    "name": name,
                    "path": name,
                    "is_dir": True,
                    "size": None,
                    "modified": datetime.fromtimestamp(os.path.getmtime(full)).strftime("%Y-%m-%d %H:%M"),
                })
        return "/", items

    target = _api_path(path)
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
        try:
            full = _api_path(os.path.relpath(full, base))
        except PermissionError:
            continue
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
    target = _api_path(path)
    if not os.path.exists(target):
        raise FileNotFoundError("파일을 찾을 수 없습니다")
    if os.path.isdir(target):
        raise IsADirectoryError("디렉토리는 다운로드할 수 없습니다")
    return target


def snapshot_live_log_if_needed(path: str):
    """검증한 fd를 읽어 응답 전용 스냅샷을 만든다(이름은 호출자 호환용)."""
    abs_path = ensure_browser_file(path)
    fd, temporary = tempfile.mkstemp(prefix='safetyreport_download_')
    try:
        with os.fdopen(fd, 'wb') as output, open_browser_file(abs_path) as source:
            info = os.fstat(source.fileno())
            shutil.copyfileobj(source, output, length=64 * 1024)
        os.utime(temporary, ns=(info.st_atime_ns, info.st_mtime_ns))
        return temporary, temporary
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
