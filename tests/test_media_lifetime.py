import tempfile
import threading
import os
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest import mock

from services import media_proxy_service as media


URL = 'https://www.safetyreport.go.kr/fileDown/singo/test.mp4'


class ReentryCheckingLock:
    def __init__(self):
        self.locked = False

    def __enter__(self):
        if self.locked:
            raise RuntimeError('non-reentrant lock reentered')
        self.locked = True

    def __exit__(self, *args):
        self.locked = False


class MediaLifetimeTests(unittest.TestCase):
    def setUp(self):
        media.start()

    def test_cleanup_preserves_open_reader_and_cancellation_releases_pins(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(media.settings, 'datapath', directory):
            path = media._cache_path(URL)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'x' * (media._CHUNK_SIZE + 1))
            os.utime(path, (1, 1))
            source = {'url': URL, 'path': path, 'tmp': media._tmp_path(path), 'total': path.stat().st_size, 'complete': True}
            stream = media.iter_stream(source, 0, source['total'] - 1)
            self.assertEqual(len(next(stream)), media._CHUNK_SIZE)
            self.assertEqual(media.cleanup_cache(1), 0)
            self.assertTrue(path.exists())
            stream.close()
            self.assertNotIn(path, media._active_paths)
            self.assertEqual(media.cleanup_cache(1), 1)

    def test_partial_stream_rejects_a_new_download_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'partial'
            path.write_bytes(b'abc')
            source = {'url': URL, 'path': path, 'tmp': path, 'total': 3, 'complete': False, 'generation': 'old'}
            with mock.patch.object(media, '_progress_get', return_value={'generation': 'new', 'downloaded': 3}):
                with self.assertRaisesRegex(RuntimeError, 'generation changed'):
                    next(media.iter_stream(source, 0, 2))
            self.assertNotIn(path, media._active_paths)

    def test_already_completed_future_does_not_reenter_guard(self):
        done = Future()
        done.set_result(Path('unused'))
        futures = {}
        with mock.patch.object(media, '_prime_guard', ReentryCheckingLock()), \
             mock.patch.object(media, '_prime_futures', futures), \
             mock.patch.object(media, '_prime_errors', {}), \
             mock.patch.object(media, '_prime_executor') as executor, \
             mock.patch.object(media, '_progress_reset'), \
             mock.patch.object(media, 'get_cache_status', return_value={'ready': False, 'status': 'missing'}):
            executor.submit.return_value = done
            self.assertEqual(media.prime_cache(URL)['status'], 'pending')
            self.assertEqual(futures, {})

    def test_old_callback_cannot_replace_new_attempt_error(self):
        old, new = Future(), Future()
        old.set_exception(RuntimeError('old failure'))
        errors = {URL: 'new failure'}
        with mock.patch.object(media, '_prime_guard', threading.Lock()), \
             mock.patch.object(media, '_prime_futures', {URL: new}), \
             mock.patch.object(media, '_prime_errors', errors):
            media._finish_prime(URL, old)
        self.assertEqual(errors, {URL: 'new failure'})

    def test_header_alone_is_not_stream_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'cache'
            states = [{'total': 4}, {'total': 4, 'ready': True}]
            with mock.patch.object(media, '_cache_path', return_value=path), \
                 mock.patch.object(media, 'prime_cache'), \
                 mock.patch.object(media, '_progress_get', side_effect=states), \
                 mock.patch.object(media.time, 'sleep') as sleep:
                source = media.open_stream(URL)
            self.assertEqual(sleep.call_count, 1)
            self.assertEqual(source['total'], 4)
            self.assertFalse(source['complete'])

    def test_completed_range_yields_exact_bytes_and_closes_reader(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'cache'
            path.write_bytes(b'0123456789')
            source = {'url': URL, 'path': path, 'tmp': path.with_suffix('.tmp'), 'total': 10, 'complete': True}
            reader = path.open('rb')
            with mock.patch.object(media, '_open_cache_reader', return_value=reader):
                self.assertEqual(b''.join(media.iter_stream(source, 2, 6)), b'23456')
            self.assertTrue(reader.closed)

    def test_truncated_upstream_is_not_published_as_a_completed_cache(self):
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(media.settings, 'datapath', directory), \
             mock.patch('core.utils.runtime_mode.block_if_fixture'), \
             mock.patch.object(media._session, 'get') as get:
            response = get.return_value.__enter__.return_value
            response.status_code = 200
            response.headers = {'Content-Length': '4'}
            response.iter_content.return_value = iter([b'ab'])
            with self.assertRaisesRegex(RuntimeError, 'Content-Length'):
                media.ensure_cached(URL)
            self.assertFalse(media._cache_path(URL).exists())
            self.assertFalse(media._tmp_path(media._cache_path(URL)).exists())
            progress = media._progress_get(URL)
            self.assertTrue(progress['done'])
            self.assertIn('Content-Length', progress['error'])

    def test_ready_means_the_tmp_file_has_been_opened(self):
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(media.settings, 'datapath', directory), \
             mock.patch('core.utils.runtime_mode.block_if_fixture'), \
             mock.patch.object(media._session, 'get') as get:
            response = get.return_value.__enter__.return_value
            response.status_code = 200
            response.headers = {'Content-Length': '4'}

            def content(**kwargs):
                self.assertTrue(media._progress_get(URL)['ready'])
                self.assertTrue(media._tmp_path(media._cache_path(URL)).exists())
                yield b'abcd'

            response.iter_content.side_effect = content
            self.assertEqual(media.ensure_cached(URL).read_bytes(), b'abcd')


class MediaCapacityTests(unittest.TestCase):
    def test_waiters_share_lock_and_unused_url_locks_are_released(self):
        import gc
        import weakref
        with mock.patch.object(media, '_locks', weakref.WeakValueDictionary()):
            held = media._get_lock(URL)
            self.assertIs(held, media._get_lock(URL))
            for index in range(1000):
                media._get_lock(URL + str(index))
            gc.collect()
            self.assertEqual(len(media._locks), 1)
            del held
            self.assertEqual(len(media._locks), 0)

    def test_prefetch_capacity_rejects_without_scheduling_more_work(self):
        active = {str(i): Future() for i in range(media._MAX_PRIME_PENDING)}
        with mock.patch.object(media, '_prime_futures', active), \
             mock.patch.object(media, '_prime_executor') as executor, \
             mock.patch.object(media, 'get_cache_status', return_value={'ready': False, 'status': 'missing'}), \
             mock.patch.object(media, '_progress_reset') as reset:
            media._stop.clear()
            with self.assertRaisesRegex(RuntimeError, 'capacity exceeded'):
                media.prime_cache(URL)
            executor.submit.assert_not_called()
            reset.assert_not_called()

    def test_status_trim_preserves_live_readers(self):
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(media.settings, 'datapath', directory), \
             mock.patch.object(media, '_progress', {}), \
             mock.patch.object(media, '_active_paths', {}) as active, \
             mock.patch.object(media, '_MAX_COMPLETED_STATUS', 2):
            active[media._cache_path(URL)] = 1
            for key in [URL, URL+'1', URL+'2', URL+'3']:
                media._progress_set(key, done=True, downloaded=3)
            self.assertIn(URL, media._progress)
            self.assertEqual(set(media._progress), {URL, URL+'2', URL+'3'})
