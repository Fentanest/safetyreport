import json
import tempfile
import unittest
import threading
from pathlib import Path
from unittest import mock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from core.utils import atomic_file
from web.routers import settings_route


class AtomicSettingsTests(unittest.TestCase):
    def test_concurrent_request_drafts_do_not_publish_or_overwrite_each_other(self):
        import settings.settings as settings
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict('os.environ', {'SAFETYREPORT_DATA_DIR': directory}):
            first, second = settings.AppSettings(), settings.AppSettings()
            ready = threading.Barrier(2)
            failures = []
            def save(instance, key, value):
                try:
                    instance.update_config('SETTINGS', key, value)
                    ready.wait(timeout=5)
                    instance.save()
                except Exception as exc:
                    failures.append(exc)
            threads = [threading.Thread(target=save, args=(first, 'retry_interval', 11)),
                       threading.Thread(target=save, args=(second, 'max_retry_attemps', 7))]
            for thread in threads: thread.start()
            for thread in threads: thread.join(5)
            self.assertFalse(any(thread.is_alive() for thread in threads))
            self.assertEqual(failures, [])
            first.load()
            self.assertEqual((first.retry_interval, first.max_retry_attemps), (11, 7))

    def test_failed_validation_and_replace_keep_published_settings_and_discard_draft(self):
        import settings.settings as settings
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict('os.environ', {'SAFETYREPORT_DATA_DIR': directory}):
            instance = settings.AppSettings()
            instance.update_config('SETTINGS', 'retry_interval', 12)
            instance.save()
            path = Path(instance.config_path)
            before = path.read_bytes()
            for value, failure in [('invalid', ValueError), ('13', OSError)]:
                instance.update_config('SETTINGS', 'retry_interval', value)
                with mock.patch.object(atomic_file.os, 'replace', side_effect=OSError):
                    with self.assertRaises(failure): instance.save()
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual(instance.retry_interval, 12)
                self.assertEqual(instance._draft.pending, {})

    def test_replace_failure_keeps_original_and_removes_temporary(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.ini'
            path.write_bytes(b'original')
            with mock.patch.object(atomic_file.os, 'replace', side_effect=OSError):
                with self.assertRaises(OSError):
                    atomic_file.write_bytes(path, b'replacement')
            self.assertEqual(path.read_bytes(), b'original')
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_upload_rejects_invalid_or_oversized_before_replacing_credentials(self):
        app = FastAPI()
        app.include_router(settings_route.router)
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(settings_route.app_settings._instance, 'datapath', directory), \
             mock.patch.object(settings_route.app_settings._instance, 'load') as reload:
            path = Path(directory) / 'auth/gspread.json'
            path.parent.mkdir()
            path.write_bytes(b'original')
            with TestClient(app) as client:
                for body in (b'broken', b'[]', b'{}', b'\xff'):
                    self.assertEqual(client.post('/settings/upload_json',
                        files={'file': ('auth.json', body)}).status_code, 400)
                    self.assertEqual(path.read_bytes(), b'original')
                self.assertEqual(client.post('/settings/upload_json',
                    files={'file': ('auth.json', b'x' * (2 * 1024 * 1024 + 1))}).status_code, 413)
                payload = json.dumps({'type': 'service_account', 'client_email': 'fixture@test',
                                      'private_key': 'fixture-only', 'token_uri': 'https://fixture/token'}).encode()
                self.assertEqual(client.post('/settings/upload_json',
                    files={'file': ('auth.json', payload)}, follow_redirects=False).status_code, 303)
            self.assertEqual(path.read_bytes(), payload)
            reload.assert_called_once()
