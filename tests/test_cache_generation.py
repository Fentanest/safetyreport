import os
import sqlite3
import tempfile
import threading
import unittest
from unittest import mock
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from sqlalchemy import create_engine
from services import report_cache as cache


class CacheGenerationTests(unittest.TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'data.db'
        with sqlite3.connect(self.path) as conn:
            conn.execute('CREATE TABLE value(n INTEGER)')
            conn.execute('INSERT INTO value VALUES (1)')
        self.engine = create_engine('sqlite:///' + str(self.path))
        self.addCleanup(self.engine.dispose)

    def test_concurrent_miss_computes_once_and_return_values_are_owned(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        @cache.cached
        def load(engine):
            calls.append(1)
            entered.set()
            self.assertTrue(release.wait(5))
            return {'rows': [{'value': 1}]}
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(load, self.engine) for _ in range(4)]
            self.assertTrue(entered.wait(5))
            release.set()
            results = [future.result(5) for future in futures]
        self.assertEqual(len(calls), 1)
        results[0]['rows'][0]['value'] = 9
        self.assertEqual(load(self.engine)['rows'][0]['value'], 1)

    def test_clear_during_compute_does_not_publish_old_generation(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        @cache.cached
        def load(engine):
            calls.append(1)
            if len(calls) == 1:
                entered.set()
                self.assertTrue(release.wait(5))
            return len(calls)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(load, self.engine)
            self.assertTrue(entered.wait(5))
            cache.clear()
            release.set()
            self.assertEqual(future.result(5), 1)
        self.assertEqual(load(self.engine), 2)

    def test_replaced_database_reopens_observer_and_sees_new_value(self):
        @cache.cached
        def load(engine):
            with engine.connect() as conn:
                return conn.exec_driver_sql('SELECT n FROM value').fetchone()[0]
        self.assertEqual(load(self.engine), 1)
        replacement = self.path.with_name('replacement.db')
        with sqlite3.connect(replacement) as conn:
            conn.execute('CREATE TABLE value(n INTEGER)')
            conn.execute('INSERT INTO value VALUES (2)')
        os.replace(replacement, self.path)
        self.assertEqual(load(self.engine), 2)

    def test_owner_failure_wakes_waiter_without_caching_the_exception(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        @cache.cached
        def load(engine):
            calls.append(1)
            if len(calls) == 1:
                entered.set()
                self.assertTrue(release.wait(5))
                raise RuntimeError('fixture compute failure')
            return {'value': 2}
        with ThreadPoolExecutor(max_workers=2) as pool:
            owner = pool.submit(load, self.engine)
            self.assertTrue(entered.wait(5))
            waiter = pool.submit(load, self.engine)
            release.set()
            with self.assertRaises(RuntimeError): owner.result(5)
            self.assertEqual(waiter.result(5), {'value': 2})
        self.assertEqual(load(self.engine), {'value': 2})
        self.assertEqual(len(calls), 2)

    def test_oversized_results_remain_complete_but_are_not_cached(self):
        calls = []
        @cache.cached
        def load(engine):
            calls.append(1)
            return {'text': '가' * 1000}
        with mock.patch.object(cache, '_byte_limit', 100):
            self.assertEqual(load(self.engine), {'text': '가' * 1000})
            self.assertEqual(load(self.engine), {'text': '가' * 1000})
        self.assertEqual(len(calls), 2)
        self.assertEqual(cache._entries, {})
