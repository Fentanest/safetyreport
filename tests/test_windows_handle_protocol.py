import unittest
from core.utils import windows_file_handle as handles


class Api:
    def __init__(self, final, attributes=0, links=1):
        self.final, self.attributes, self.links = final, attributes, links
        self.closed, self.opened, self.deleted = [], [], []

    def CreateFileW(self, *args):
        self.opened.append(args)
        return 123

    def GetFileInformationByHandle(self, handle, info):
        info._obj.attributes, info._obj.links = self.attributes, self.links
        return True

    def GetFinalPathNameByHandleW(self, handle, buffer, size, flags):
        if buffer is not None:
            buffer.value = self.final
        return len(self.final)

    def CloseHandle(self, handle):
        self.closed.append(handle)

    def SetFileInformationByHandle(self, handle, kind, value, size):
        self.deleted.append((handle, kind, value._obj.value, size))
        return True


class WindowsHandleProtocolTests(unittest.TestCase):
    # These tests validate API ownership/flags/decisions with a fake DLL.
    # They do not certify the actual Windows ABI or filesystem.
    def test_final_identity_root_reparse_hardlink_and_other_drive_are_rejected(self):
        for api in [Api(r'\\?\C:\outside\file'), Api(r'\\?\D:\data\logs\file'),
                    Api(r'\\?\C:\data\logs\file', attributes=0x400),
                    Api(r'\\?\C:\data\logs\file', attributes=0x10),
                    Api(r'\\?\C:\data\logs\file', links=2)]:
            with self.subTest(final=api.final, attrs=api.attributes, links=api.links):
                with self.assertRaises(PermissionError):
                    with handles.validated_handle(r'C:\data\logs\file', r'C:\data\logs', api=api):
                        self.fail('untrusted handle accepted')
                self.assertEqual(api.closed, [123])

    def test_same_handle_is_verified_and_share_mode_pins_identity(self):
        api = Api(r'\\?\C:\data\logs\한글 file')
        with handles.validated_handle(r'C:\data\logs\한글 file', r'C:\data\logs', api=api) as (_, handle, _):
            self.assertEqual(handle, 123)
            self.assertFalse(api.closed)
        self.assertEqual(api.closed, [123])
        self.assertEqual(api.opened[0][2], 3)  # read/write sharing, no delete sharing
        self.assertEqual(api.opened[0][5], 0x00200000)  # OPEN_REPARSE_POINT

    def test_context_closes_on_failure_and_transfer_preserves_crt_ownership(self):
        api = Api(r'\\?\C:\data\logs\file')
        with self.assertRaisesRegex(RuntimeError, 'reader failed'):
            with handles.validated_handle(r'C:\data\logs\file', r'C:\data\logs', api=api):
                raise RuntimeError('reader failed')
        self.assertEqual(api.closed, [123])
        api.closed.clear()
        with handles.validated_handle(r'C:\data\logs\file', r'C:\data\logs', api=api) as (_, _, owned):
            owned[0] = False
        self.assertFalse(api.closed)

    def test_delete_requests_permission_and_uses_boolean_disposition_of_same_handle(self):
        api = Api(r'\\?\C:\data\logs\file')
        from unittest import mock
        with mock.patch.object(handles, '_api', return_value=api):
            handles.delete_file(r'C:\data\logs\file', r'C:\data\logs')
        self.assertTrue(api.opened[0][1] & 0x00010000)
        self.assertEqual(api.deleted, [(123, 4, 1, 1)])
        self.assertEqual(api.closed, [123])

    def test_unc_extended_prefix_and_case_are_normalized_without_sibling_prefix_acceptance(self):
        self.assertEqual(handles._normal(r'\\?\UNC\SERVER\share\logs\file'), handles._normal(r'\\server\share\logs\file'))
        api = Api(r'\\?\C:\data\logs-other\file')
        with self.assertRaises(PermissionError):
            with handles.validated_handle(r'C:\data\logs-other\file', r'C:\data\logs', api=api):
                self.fail('sibling prefix accepted')
