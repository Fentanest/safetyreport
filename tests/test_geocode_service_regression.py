import os
import tempfile
import time
import unittest

from sqlalchemy import create_engine

from core.database import database
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
