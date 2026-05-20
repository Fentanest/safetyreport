import os
import tempfile
import unittest

from sqlalchemy import create_engine, func, select

from core.database import database, models
from core.utils import logger
import start


class StartResetRegressionTest(unittest.TestCase):
    def setUp(self):
        logger.LoggerFactory.create_logger(mode="crawl")
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.engine = create_engine(f"sqlite:///{self.db_path}")
        database.upgrade_schema(self.engine)

    def tearDown(self):
        self.engine.dispose()
        try:
            os.remove(self.db_path)
        except FileNotFoundError:
            pass

    def test_reset_preserves_geocode_cache_and_watchlist_meta(self):
        with self.engine.begin() as conn:
            conn.execute(
                models.geocode_cache_table.insert().values(
                    주소정규화="서울특별시 강서구 테스트 1",
                    원본주소="서울특별시 강서구 테스트 1",
                    행정구역="서울 강서구",
                    위도=37.5,
                    경도=126.8,
                    상태="ok",
                    updated_at=1,
                )
            )
            conn.execute(
                models.merge_traffic_table.insert().values(
                    ID="id-1",
                    신고번호="r-1",
                    위반장소="서울특별시 강서구 테스트 1",
                )
            )
            conn.execute(models.sync_meta_table.insert().values(key="watchlist", value="1,2"))
            conn.execute(models.sync_meta_table.insert().values(key="last_sync", value="123"))

        start._prepare_database(self.engine, reset=True)

        with self.engine.connect() as conn:
            geocode_count = conn.execute(
                select(func.count()).select_from(models.geocode_cache_table)
            ).scalar_one()
            merge_count = conn.execute(
                select(func.count()).select_from(models.merge_traffic_table)
            ).scalar_one()
            meta_rows = conn.execute(
                select(models.sync_meta_table.c.key, models.sync_meta_table.c.value).order_by(
                    models.sync_meta_table.c.key
                )
            ).fetchall()

        self.assertEqual(geocode_count, 1)
        self.assertEqual(merge_count, 0)
        self.assertEqual([(row[0], row[1]) for row in meta_rows], [("watchlist", "1,2")])


if __name__ == "__main__":
    unittest.main()
