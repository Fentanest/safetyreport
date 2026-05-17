import os
import tempfile
import time
import unittest

from sqlalchemy import create_engine

from core.database import database, models
from core.utils import logger
from services import geocode_service


class GeocodeServiceRegressionTest(unittest.TestCase):
    def setUp(self):
        logger.LoggerFactory.create_logger(mode="crawl")
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.engine = create_engine(f"sqlite:///{self.db_path}")
        database.upgrade_schema(self.engine)

    def tearDown(self):
        try:
            os.remove(self.db_path)
        except FileNotFoundError:
            pass

    def test_missing_api_key_uses_cached_coordinates_before_warning(self):
        with self.engine.begin() as conn:
            conn.execute(
                models.geocode_cache_table.insert().values(
                    주소정규화="서울특별시 강서구 마곡동 1",
                    원본주소="서울특별시 강서구 마곡동 1",
                    행정구역="서울특별시 강서구 마곡동",
                    위도=37.5601,
                    경도=126.8301,
                    상태="ok",
                    updated_at=1,
                )
            )
            conn.execute(
                models.detail_traffic_table.insert(),
                [
                    {
                        "ID": "cached-row",
                        "위반장소": "서울특별시 강서구 마곡동 1",
                        "주소정규화": "서울특별시 강서구 마곡동 1",
                    },
                    {
                        "ID": "missing-row",
                        "위반장소": "서울특별시 강서구 방화동 9",
                        "주소정규화": "서울특별시 강서구 방화동 9",
                    },
                ],
            )
            conn.execute(
                models.merge_traffic_table.insert(),
                [
                    {
                        "ID": "cached-row",
                        "신고번호": "R-1",
                        "위반장소": "서울특별시 강서구 마곡동 1",
                        "주소정규화": "서울특별시 강서구 마곡동 1",
                    },
                    {
                        "ID": "missing-row",
                        "신고번호": "R-2",
                        "위반장소": "서울특별시 강서구 방화동 9",
                        "주소정규화": "서울특별시 강서구 방화동 9",
                    },
                ],
            )

        original_get_key = geocode_service.get_kakao_rest_api_key
        original_has_key = geocode_service.has_kakao_rest_api_key
        try:
            geocode_service.get_kakao_rest_api_key = lambda: ""
            geocode_service.has_kakao_rest_api_key = lambda: False

            result = geocode_service.backfill_missing_report_coordinates(
                self.engine,
                limit=20,
            )
        finally:
            geocode_service.get_kakao_rest_api_key = original_get_key
            geocode_service.has_kakao_rest_api_key = original_has_key

        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["remaining_missing"], 1)
        self.assertEqual(result["error_state"], "config_warning")
        self.assertIn("DB에 없는 새 주소", result["error_message"])

        with self.engine.connect() as conn:
            cached_row = conn.execute(
                models.detail_traffic_table.select().where(
                    models.detail_traffic_table.c.ID == "cached-row"
                )
            ).mappings().first()
            missing_row = conn.execute(
                models.detail_traffic_table.select().where(
                    models.detail_traffic_table.c.ID == "missing-row"
                )
            ).mappings().first()

        self.assertAlmostEqual(float(cached_row["위도"]), 37.5601)
        self.assertAlmostEqual(float(cached_row["경도"]), 126.8301)
        self.assertEqual(str(cached_row["지오코딩상태"]), "ok")
        self.assertIsNone(missing_row["위도"])
        self.assertIsNone(missing_row["경도"])

    def test_missing_api_key_with_saved_coordinates_returns_config_warning_state(self):
        with self.engine.begin() as conn:
            conn.execute(
                models.geocode_cache_table.insert().values(
                    주소정규화="서울특별시 강서구 마곡동 1",
                    원본주소="서울특별시 강서구 마곡동 1",
                    행정구역="서울특별시 강서구 마곡동",
                    위도=37.5601,
                    경도=126.8301,
                    상태="ok",
                    updated_at=1,
                )
            )
            conn.execute(
                models.detail_traffic_table.insert().values(
                    ID="pending-only",
                    위반장소="서울특별시 강서구 방화동 99",
                    주소정규화="서울특별시 강서구 방화동 99",
                )
            )

        original_get_key = geocode_service.get_kakao_rest_api_key
        original_has_key = geocode_service.has_kakao_rest_api_key
        try:
            geocode_service.get_kakao_rest_api_key = lambda: ""
            geocode_service.has_kakao_rest_api_key = lambda: False

            state = geocode_service.ensure_map_backfill_started(
                self.engine,
                batch_size=20,
            )
        finally:
            geocode_service.get_kakao_rest_api_key = original_get_key
            geocode_service.has_kakao_rest_api_key = original_has_key

        self.assertEqual(state["state"], "config_warning")
        self.assertFalse(state["running"])
        self.assertEqual(state["remaining_missing"], 1)
        self.assertTrue(state["has_saved_coordinates"])
        self.assertIn("DB에 없는 새 주소", state["error_message"])

    def test_stale_running_lease_transitions_to_error_state(self):
        old_ms = int((time.time() - 600) * 1000)
        geocode_service._set_progress_state(
            engine=self.engine,
            state="running",
            running=True,
            total=10,
            processed=4,
            updated=3,
            not_found=1,
            remaining_missing=6,
            error_message="",
            started_at=old_ms,
            finished_at=0,
            heartbeat_at=old_ms,
            lease_owner="lease-owner",
        )

        state = geocode_service.get_backfill_progress(self.engine)

        self.assertEqual(state["state"], "error")
        self.assertFalse(state["running"])
        self.assertEqual(state["processed"], 4)
        self.assertEqual(state["updated"], 3)
        self.assertEqual(state["not_found"], 1)
        self.assertIn("중단", state["error_message"])

    def test_cross_platform_lock_fallback_blocks_double_acquire(self):
        original_fcntl = geocode_service.fcntl
        original_path = geocode_service._BACKFILL_LOCK_PATH
        fd, lock_path = tempfile.mkstemp(prefix="geocode-lock-", suffix=".lock")
        os.close(fd)
        os.unlink(lock_path)

        try:
            geocode_service.fcntl = None
            geocode_service._BACKFILL_LOCK_PATH = lock_path

            first = geocode_service._try_acquire_backfill_start_lock(timeout_ms=50)
            second = geocode_service._try_acquire_backfill_start_lock(timeout_ms=50)

            self.assertIsNotNone(first)
            self.assertIsNone(second)

            geocode_service._release_backfill_start_lock(first)
            third = geocode_service._try_acquire_backfill_start_lock(timeout_ms=50)
            self.assertIsNotNone(third)
            geocode_service._release_backfill_start_lock(third)
            self.assertFalse(os.path.exists(lock_path))
        finally:
            geocode_service.fcntl = original_fcntl
            geocode_service._BACKFILL_LOCK_PATH = original_path
            try:
                os.unlink(lock_path)
            except FileNotFoundError:
                pass


if __name__ == "__main__":
    unittest.main()
