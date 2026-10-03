import sqlite3
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine, event
from core.utils import logger
from scripts.dev.fixture_server import seed_engine
from services import report_query_service as query, report_stats_service as stats


class ReadSnapshotTests(unittest.TestCase):
    def setUp(self):
        logger.LoggerFactory.create_logger(mode='crawl')
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'data.db'
        self.engine = create_engine('sqlite:///' + str(self.path))
        self.addCleanup(self.engine.dispose)
        seed_engine(self.engine)
        with self.engine.connect() as connection:
            connection.exec_driver_sql('PRAGMA journal_mode=WAL')

    def assert_snapshot(self, load):
        before = load()
        changed = []
        def during_read(conn, cursor, statement, parameters, context, executemany):
            if not changed and statement.lstrip().upper().startswith('SELECT') and 'mysafetymerge_traffic' in statement:
                changed.append(True)
                with sqlite3.connect(self.path) as writer:
                    writer.execute('UPDATE mysafetymerge_parking SET 위반장소=?', ('동시 수정된 장소',))
        event.listen(self.engine, 'after_cursor_execute', during_read)
        try:
            result = load()
        finally:
            event.remove(self.engine, 'after_cursor_execute', during_read)
        self.assertEqual(changed, [True])
        self.assertEqual(result, before)
        self.assertNotEqual(load(), before)

    def test_all_category_query_uses_one_read_snapshot(self):
        self.assert_snapshot(lambda: query.get_all_records(self.engine, mode='raw'))

    def test_map_frames_use_one_read_snapshot(self):
        self.assert_snapshot(lambda: stats._load_map_records_frame.__wrapped__(self.engine, mode='raw')[2].fillna('').to_dict('records'))
