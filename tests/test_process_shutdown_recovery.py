import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from services import download_artifacts
from services.crawl_manager import CrawlManager


class ShutdownRecoveryTests(unittest.TestCase):
    def test_forced_exit_artifacts_are_reclaimed_without_touching_live_or_other_files(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(download_artifacts.settings, 'datapath', directory):
            env = dict(os.environ, SAFETYREPORT_DATA_DIR=directory, SAFETYREPORT_FIXTURE_MODE='1')
            child = subprocess.run([sys.executable, '-c',
                "import os; from services.download_artifacts import create; fd,p=create('safetyreport_archive_', '.zip'); os.write(fd,b'fixture'); print(p,flush=True); os._exit(37)"],
                env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(child.returncode, 37)
            abandoned = Path(child.stdout.strip().splitlines()[-1])
            self.assertTrue(abandoned.is_file())
            fd, live = download_artifacts.create('safetyreport_download_')
            os.close(fd)
            other = Path(directory) / '.download-artifacts' / 'user-file.txt'
            other.write_text('preserve')
            self.assertEqual(download_artifacts.recover(), 1)
            self.assertFalse(abandoned.exists())
            self.assertTrue(Path(live).exists())
            self.assertEqual(other.read_text(), 'preserve')

    def test_shutdown_terminates_only_owned_synthetic_child_preserves_pending_and_stops_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = object.__new__(CrawlManager)
            manager._state_lock = threading.Lock()
            manager._active_process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
            self.addCleanup(lambda: manager._active_process and manager._active_process.kill())
            proc = manager._active_process
            manager._retry_timer = mock.Mock()
            timer = manager._retry_timer
            manager._preparing = False
            manager._request_again = True
            manager._pending_queue = ['fixture-pending']
            self.assertTrue(manager.shutdown(timeout=1))
            self.assertIsNotNone(proc.poll())
            timer.cancel.assert_called_once()
            self.assertEqual(manager._pending_queue, ['fixture-pending'])
            self.assertFalse(manager.start_crawl([], directory, str(Path(directory) / 'crawl.log')))
            manager._schedule_retry()
            self.assertIsNone(manager._retry_timer)

    def test_shutdown_during_prepare_fences_late_spawn(self):
        manager = object.__new__(CrawlManager)
        manager._state_lock = threading.Lock()
        manager._shutting_down = False
        manager._rating_token = manager._active_process = manager._retry_timer = None
        manager._restore_hold = manager._preparing = manager._post_upload_active = False
        manager._restore_generation = 0
        manager._pending_queue = []
        entered, release = threading.Event(), threading.Event()
        failures = []
        def prepare():
            entered.set()
            if not release.wait(3):
                raise AssertionError('test barrier expired')
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(download_artifacts.settings, 'datapath', directory), \
             mock.patch('services.crawl_manager.block_if_fixture'), \
             mock.patch('services.crawl_manager.subprocess.Popen') as launch:
            def start():
                try:
                    manager.start_crawl(['fixture'], directory, str(Path(directory) / 'crawl.log'), prepare=prepare)
                except RuntimeError as exc:
                    failures.append(str(exc))
            worker = threading.Thread(target=start)
            worker.start()
            try:
                self.assertTrue(entered.wait(3))
                self.assertFalse(manager.shutdown(timeout=1), 'prepare has not completed yet')
            finally:
                release.set()
                worker.join(3)
            self.assertFalse(worker.is_alive())
            self.assertEqual(failures, ['서버가 종료 중입니다.'])
        launch.assert_not_called()
