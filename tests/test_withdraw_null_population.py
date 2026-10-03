import unittest
from unittest import mock

import pandas as pd
from sqlalchemy import create_engine

from core.database import models
from services import report_query_service as query, report_stats_service as stats


class WithdrawPopulationTests(unittest.TestCase):
    def test_sql_and_frame_filters_keep_unknown_and_empty_status(self):
        engine = create_engine('sqlite:///:memory:')
        self.addCleanup(engine.dispose)
        table = models.merge_traffic_table
        table.create(engine)
        rows = [{'ID': str(i), '처리상태': value} for i, value in enumerate([None, '', '수용', '취하'])]
        with engine.begin() as conn:
            conn.execute(table.insert(), rows)
        with mock.patch.object(query.app_settings, 'exclude_withdraw', True):
            with engine.connect() as conn:
                records = conn.execute(query._build_records_query(table)).mappings().all()
            self.assertEqual({r['ID'] for r in records}, {'0', '1', '2'})
            frame = pd.DataFrame(rows)
            self.assertEqual(query._filter_withdraw(frame)['ID'].tolist(), ['0', '1', '2'])
            self.assertEqual(stats._exclude_withdraw_rows(frame)['ID'].tolist(), ['0', '1', '2'])
            with engine.connect() as conn:
                self.assertIsNone(conn.execute(table.select().where(table.c.ID == '0')).mappings().one()['처리상태'])
