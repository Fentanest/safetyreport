"""Win32에서 최종 파일 핸들을 검증하고 같은 핸들로 읽거나 삭제한다."""
import ctypes
import ntpath
import os
from contextlib import contextmanager
from ctypes import wintypes


class _FileInfo(ctypes.Structure):
    _fields_ = [('attributes', wintypes.DWORD), ('created', wintypes.FILETIME),
                ('accessed', wintypes.FILETIME), ('written', wintypes.FILETIME),
                ('volume', wintypes.DWORD), ('size_high', wintypes.DWORD),
                ('size_low', wintypes.DWORD), ('links', wintypes.DWORD),
                ('index_high', wintypes.DWORD), ('index_low', wintypes.DWORD)]


def _api():
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                              wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    api.CreateFileW.restype = wintypes.HANDLE
    api.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    api.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    api.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(_FileInfo)]
    api.GetFileInformationByHandle.restype = wintypes.BOOL
    api.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    api.SetFileInformationByHandle.restype = wintypes.BOOL
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    return api


def _normal(path):
    if path.startswith('\\\\?\\UNC\\'):
        path = '\\\\' + path[8:]
    elif path.startswith('\\\\?\\'):
        path = path[4:]
    return ntpath.normcase(ntpath.abspath(path))


@contextmanager
def validated_handle(path, root, *, delete=False, api=None):
    api = api or _api()
    allowed, expected = _normal(root), _normal(path)
    # No FILE_SHARE_DELETE: hold this leaf identity during validation/use.
    access = 0x00010000 | 0x80 if delete else 0x80000000
    handle = api.CreateFileW(path, access, 3, None, 3, 0x00200000, None)
    if handle is None or handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    owned = [True]
    try:
        info = _FileInfo()
        if not api.GetFileInformationByHandle(handle, ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        if info.attributes & (0x400 | 0x10) or info.links > 1:
            raise PermissionError('Access denied')
        size = api.GetFinalPathNameByHandleW(handle, None, 0, 0)
        if not size:
            raise ctypes.WinError(ctypes.get_last_error())
        buffer = ctypes.create_unicode_buffer(size + 1)
        length = api.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
        if not length or length >= len(buffer):
            raise PermissionError('Cannot verify file identity')
        actual = _normal(buffer.value)
        try:
            inside = ntpath.commonpath([actual, allowed]) == allowed
        except ValueError:
            inside = False
        if actual != expected or not inside:
            raise PermissionError('Access denied')
        yield api, handle, owned
    finally:
        if owned[0]:
            api.CloseHandle(handle)


def read_file(path, root):
    import msvcrt
    with validated_handle(path, root) as (_, handle, owned):
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        owned[0] = False  # CRT/file object now owns the same verified handle.
        try:
            return os.fdopen(descriptor, 'rb')
        except BaseException:
            os.close(descriptor)
            raise


def delete_file(path, root):
    with validated_handle(path, root, delete=True) as (api, handle, _):
        disposition = ctypes.c_ubyte(1)  # FILE_DISPOSITION_INFO.DeleteFile BOOLEAN
        if not api.SetFileInformationByHandle(handle, 4, ctypes.byref(disposition), ctypes.sizeof(disposition)):
            raise ctypes.WinError(ctypes.get_last_error())
