import tempfile
import unittest
from pathlib import Path

from core.utils.temporary_response import TemporaryFileResponse


class TemporaryResponseTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_and_range_preserve_bytes_filename_and_remove_snapshot(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'snapshot'
            app = FastAPI()
            @app.get('/download')
            def download():
                path.write_bytes(b'0123456789')
                return TemporaryFileResponse(path, filename='한글 보고.txt')
            with TestClient(app) as client:
                response = client.get('/download')
                self.assertEqual(response.content, b'0123456789')
                self.assertIn('filename*=utf-8', response.headers['content-disposition'])
                self.assertFalse(path.exists())
                response = client.get('/download', headers={'Range': 'bytes=2-6'})
                self.assertEqual(response.status_code, 206)
                self.assertEqual(response.content, b'23456')
                self.assertEqual(response.headers['content-range'], 'bytes 2-6/10')
                self.assertFalse(path.exists())

    async def test_send_failure_removes_download_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'archive.zip'
            path.write_bytes(b'archive')
            response = TemporaryFileResponse(path, filename='archive.zip')

            async def receive():
                return {'type': 'http.disconnect'}

            async def send(message):
                raise OSError('disconnected')

            scope = {'type': 'http', 'method': 'GET', 'headers': [], 'extensions': {}}
            with self.assertRaises(OSError):
                await response(scope, receive, send)
            self.assertFalse(path.exists())
