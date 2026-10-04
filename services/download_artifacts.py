"""응답 전용 파일의 프로세스 소유권과 강제 종료 후 회수."""
import errno
import os
import re
import tempfile
import threading
import uuid

import settings.settings as settings

_lock = threading.Lock()
_directories = {}
_NAME = re.compile(r'sr-download-([0-9]+)-[a-f0-9]{32}')


def _alive(pid):
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() != 87  # access denied => conservatively alive
        try:
            code = wintypes.DWORD()
            return not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError as exc:
        return exc.errno != errno.ESRCH


def recover():
    root = os.path.join(settings.datapath, '.download-artifacts')
    if not os.path.isdir(root) or os.path.islink(root) or os.path.isjunction(root):
        return 0
    removed = 0
    for entry in os.scandir(root):
        match = _NAME.fullmatch(entry.name)
        if not match or entry.is_symlink() or os.path.isjunction(entry.path) or not entry.is_dir(follow_symlinks=False):
            continue
        if _alive(int(match[1])):
            continue
        for artifact in os.scandir(entry.path):
            if artifact.name.startswith(('safetyreport_archive_', 'safetyreport_download_')) and artifact.is_file(follow_symlinks=False):
                os.unlink(artifact.path)
                removed += 1
        try:
            os.rmdir(entry.path)  # unrelated files prevent removal
        except OSError:
            pass
    return removed


def create(prefix, suffix=''):
    root = os.path.abspath(os.path.join(settings.datapath, '.download-artifacts'))
    with _lock:
        if root not in _directories or not os.path.isdir(_directories[root]):
            os.makedirs(root, mode=0o700, exist_ok=True)
            if os.path.islink(root) or os.path.isjunction(root):
                raise PermissionError('invalid artifact root')
            directory = os.path.join(root, f'sr-download-{os.getpid()}-{uuid.uuid4().hex}')
            os.mkdir(directory, mode=0o700)
            _directories[root] = directory
        return tempfile.mkstemp(prefix=prefix, suffix=suffix, dir=_directories[root])
