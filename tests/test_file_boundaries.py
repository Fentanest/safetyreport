"""파일 목록/다운로드/삭제의 명시 루트 경계."""
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from services import file_service as files


class FileBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.roots = {name: str(self.base / name) for name in ('logs', 'results')}
        for path in self.roots.values():
            Path(path).mkdir()
        self.allowed = self.base / 'logs' / '정상.log'
        self.allowed.write_bytes(b'normal\n')
        self.secret = self.base / 'auth' / 'secret'
        self.secret.parent.mkdir()
        self.secret.write_bytes(b'private')
        for patch in [mock.patch.object(files, 'ALLOWED_BROWSER_DIRS', self.roots),
                      mock.patch.object(files.settings, 'datapath', str(self.base)),
                      mock.patch.object(files.settings, 'logpath', self.roots['logs']),
                      mock.patch.object(files.settings, 'resultpath', self.roots['results'])]:
            patch.start()
            self.addCleanup(patch.stop)

    def test_api_cannot_leave_the_selected_root(self):
        for path in ('logs/../auth/secret', 'logs\\..\\auth\\secret', 'logs/../results/other',
                     '../auth/secret', '/logs/정상.log', 'C:\\logs\\정상.log'):
            with self.subTest(path=path):
                with self.assertRaises(PermissionError):
                    files.resolve_api_file(path)
                with self.assertRaises(PermissionError):
                    files.list_api_entries(path)
        self.assertEqual(files.resolve_api_file('logs/정상.log'), str(self.allowed))

    def test_browser_sibling_prefix_and_bulk_delete_are_denied(self):
        sibling = self.base / 'logs-private'
        sibling.mkdir()
        private = sibling / 'private'
        private.write_bytes(b'private')
        with self.assertRaises(PermissionError):
            files.ensure_browser_file(str(private))
        count, errors = files.delete_files([str(private), str(self.allowed)])
        self.assertEqual(count, 1)
        self.assertEqual(len(errors), 1)
        self.assertTrue(private.exists())
        self.assertFalse(self.allowed.exists())

    def test_links_are_not_listed_downloaded_or_deleted(self):
        link = self.allowed.parent / 'link'
        link.symlink_to(self.secret)
        hardlink = self.allowed.parent / 'hardlink'
        os.link(self.secret, hardlink)
        for path in (link, hardlink):
            with self.subTest(path=path.name):
                with self.assertRaises(PermissionError):
                    files.ensure_browser_file(str(path))
                with self.assertRaises(PermissionError):
                    files.resolve_api_file('logs/' + path.name)
        _, listed = files.list_api_entries('logs')
        self.assertEqual([r['name'] for r in listed], ['정상.log'])
        self.assertEqual([r['name'] for r in files.list_browser_groups()['logs']], ['정상.log'])
        self.assertEqual(files.delete_all_in_target('logs'), 1)
        self.assertTrue(link.is_symlink())
        self.assertTrue(self.secret.exists())

    def test_symlink_directory_and_active_log_are_protected(self):
        linked_dir = self.allowed.parent / 'elsewhere'
        linked_dir.symlink_to(self.secret.parent, target_is_directory=True)
        with self.assertRaises(PermissionError):
            files.list_api_entries('logs/elsewhere')
        live = self.allowed.parent / 'current_crawl.log'
        live.write_bytes(b'live')
        with self.assertRaises(RuntimeError):
            files.delete_file(str(live))
        count, errors = files.delete_api_files(['logs/current_crawl.log'])
        self.assertEqual((count, len(errors)), (0, 1))
        self.assertEqual(live.read_bytes(), b'live')

    def test_archive_has_distinct_relative_names_and_exact_bytes(self):
        other = self.base / 'results' / '정상.log'
        other.write_bytes(b'results')
        archive, filename = files.build_download_zip([str(self.allowed), str(other)])
        self.addCleanup(lambda: Path(archive).unlink(missing_ok=True))
        self.assertTrue(filename.endswith('.zip'))
        with zipfile.ZipFile(archive) as zipped:
            self.assertEqual(zipped.namelist(), ['logs/정상.log', 'results/정상.log'])
            self.assertEqual(zipped.read('logs/정상.log'), b'normal\n')
            self.assertEqual(zipped.read('results/정상.log'), b'results')

    def test_archive_failure_does_not_return_a_partial_download(self):
        with self.assertRaises(PermissionError):
            files.build_download_zip([str(self.allowed), str(self.secret)])
        original = files.tempfile.mkstemp
        created = []

        def create(*args, **kwargs):
            result = original(*args, **kwargs)
            created.append(result[1])
            return result

        with mock.patch.object(files, 'open_browser_file', side_effect=OSError('read failed')), \
             mock.patch.object(files.tempfile, 'mkstemp', side_effect=create):
            with self.assertRaises(OSError):
                files.build_download_zip([str(self.allowed)])
            self.assertEqual(len(created), 1)
            self.assertFalse(Path(created[0]).exists())

    @unittest.skipUnless(os.open in os.supports_dir_fd, 'POSIX dirfd required')
    def test_directory_swap_after_validation_cannot_read_external_file(self):
        inside = self.allowed.parent / 'nested'
        inside.mkdir()
        target = inside / 'secret'
        target.write_bytes(b'inside')
        original = files._under_root
        calls = 0

        def swap(path, root):
            nonlocal calls
            result = original(path, root)
            calls += 1
            if calls == 1:
                inside.rename(inside.with_name('saved'))
                inside.symlink_to(self.secret.parent, target_is_directory=True)
            return result

        with mock.patch.object(files, '_under_root', side_effect=swap):
            with self.assertRaises(OSError):
                files._open_file(str(target), self.roots['logs'])
