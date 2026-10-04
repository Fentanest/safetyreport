import os
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from contextlib import closing

from sqlalchemy import create_engine
from services import report_cache as cache


class ConcurrentSnapshotStressTests(unittest.TestCase):
    def test_100k_rows_concurrent_wal_reads_and_inode_replacements_have_owned_consistent_results(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'data.db'
            def create(target, version, wal=False):
                with closing(sqlite3.connect(target)) as conn, conn:
                    if wal:
                        conn.execute('PRAGMA journal_mode=WAL')
                    conn.execute('CREATE TABLE items(id INTEGER PRIMARY KEY,n INTEGER)')
                    conn.executemany('INSERT INTO items VALUES (?,?)', ((i, version) for i in range(100000)))
                    conn.execute('CREATE TABLE version(n INTEGER)')
                    conn.execute('INSERT INTO version VALUES (?)', (version,))
            create(path, 0, wal=True)
            engine = create_engine('sqlite:///' + str(path), connect_args={'timeout': 10})
            cache.clear()
            try:
                @cache.cached
                def load(engine, key=0):
                    with engine.connect() as conn:
                        conn.exec_driver_sql('BEGIN')
                        version = conn.exec_driver_sql('SELECT n FROM version').scalar()
                        count, total = conn.exec_driver_sql('SELECT COUNT(*),SUM(n) FROM items').one()
                        return {'version': version, 'count': count, 'total': total, 'owned': [key]}
                start = threading.Barrier(9)
                def reader(worker):
                    start.wait(timeout=10)
                    for iteration in range(80):
                        value = load(engine, (worker + iteration) % 12)
                        self.assertEqual(value['count'], 100000)
                        self.assertEqual(value['total'], 100000 * value['version'])
                        value['owned'].append('consumer mutation')
                    return True
                with ThreadPoolExecutor(max_workers=8) as pool:
                    readers = [pool.submit(reader, worker) for worker in range(8)]
                    start.wait(timeout=10)
                    for version in range(1, 17):
                        with closing(sqlite3.connect(path, timeout=10)) as conn, conn:
                            conn.execute('UPDATE items SET n=?', (version,))
                            conn.execute('UPDATE version SET n=?', (version,))
                    self.assertTrue(all(future.result(timeout=30) for future in readers))
                # Close the WAL before controlled atomic database replacement.
                cache.clear()
                engine.dispose()
                with closing(sqlite3.connect(path)) as conn, conn:
                    conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
                    conn.execute('PRAGMA journal_mode=DELETE')
                for version in range(17, 21):
                    replacement = Path(directory) / 'replacement.db'
                    create(replacement, version)
                    os.replace(replacement, path)
                    with ThreadPoolExecutor(max_workers=8) as pool:
                        values = list(pool.map(lambda _: load(engine), range(32)))
                    self.assertTrue(all(value['version'] == version and value['total'] == version * 100000 for value in values))
                    self.assertTrue(all(value['owned'] == [0] for value in values))
                    self.assertLessEqual(len(cache._entries), cache._limit)
                    self.assertLessEqual(sum(cache._sizes.values()), cache._byte_limit)
                self.assertFalse(cache._flights)
            finally:
                cache.clear()
                engine.dispose()
            self.assertFalse(cache._probes)
