"""업데이트 뒤 한 번 훑기 작업: 주정차 사진 촬영 시각 (services/maintenance_service.py)."""
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine, select, update

from core.database import models
from core.utils import logger
from scripts.dev import fixture_server
from services import maintenance_service


class PhotoBackfillJobTests(unittest.TestCase):
    def setUp(self):
        logger.LoggerFactory.create_logger(mode="crawl")
        self._dir = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self._dir.name) / 'data.db'}")
        fixture_server.seed_engine(self.engine)
        detail, title = models.detail_parking_table, models.title_table
        recent = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
        with self.engine.begin() as conn:
            self.ids = conn.execute(select(detail.c.ID).order_by(detail.c.ID)).scalars().all()[:3]
            for i, rid in enumerate(self.ids):
                conn.execute(update(detail).where(detail.c.ID == rid).values(
                    첨부사진=f"https://x/{i}.jpg", 사진_첫촬영=None, 사진_끝촬영=None, 사진_촬영수=None))
                conn.execute(update(title).where(title.c.ID == rid).values(신고일=recent))

    def tearDown(self):
        self.engine.dispose()
        self._dir.cleanup()

    def test_job_fills_what_it_can_and_reports_progress(self):
        def fetch(url):
            if url.endswith("1.jpg"):
                raise OSError("down")  # 한 건은 네트워크 오류 — 다음에 다시
            return "2026-09-20 10:00:00"

        state = maintenance_service.start_photo_backfill(self.engine, fetch=fetch, interval=0, wait=True)
        self.assertEqual((state["state"], state["total"], state["done"], state["filled"], state["failed"]), ("completed", 3, 3, 2, 1))
        self.assertIn("2건 채움", state["message"])
        with self.engine.connect() as conn:
            counts = dict(conn.execute(select(models.merge_parking_table.c.ID, models.merge_parking_table.c["사진_촬영수"])
                                       .where(models.merge_parking_table.c.ID.in_(self.ids))).all())
        self.assertEqual(sorted(v for v in counts.values() if v is not None), [1, 1])
        # 다음 기동: 채운 건은 대상에서 빠지고 실패한 1건만 다시
        state = maintenance_service.start_photo_backfill(self.engine, fetch=lambda url: "2026-09-20 10:00:00", interval=0, wait=True)
        self.assertEqual((state["total"], state["filled"]), (1, 1))
        state = maintenance_service.start_photo_backfill(self.engine, fetch=lambda url: None, interval=0, wait=True)
        self.assertEqual(state["state"], "idle")

    def test_job_waits_while_crawling(self):
        calls = {"n": 0}

        def crawling():
            calls["n"] += 1
            return calls["n"] <= 2  # 처음 두 번 확인할 땐 크롤링 중

        maintenance_service._CRAWL_WAIT_SECONDS, saved = 0.01, maintenance_service._CRAWL_WAIT_SECONDS
        try:
            state = maintenance_service.start_photo_backfill(self.engine, fetch=lambda url: None, interval=0,
                                                             crawling=crawling, wait=True)
        finally:
            maintenance_service._CRAWL_WAIT_SECONDS = saved
        self.assertEqual((state["state"], state["done"]), ("completed", 3))
        self.assertGreaterEqual(calls["n"], 3)

    def test_status_lists_running_jobs_for_the_bottom_bar(self):
        maintenance_service._update(state="running", total=10, done=4, current="SPP-1")
        try:
            data = maintenance_service.status(self.engine)
            self.assertTrue(data["active"])
            self.assertEqual(data["jobs"][0]["label"], "주정차 사진 촬영 시각 읽기")
        finally:
            maintenance_service._update(state="idle", total=0, done=0, current="")
        self.assertFalse(maintenance_service.status(self.engine)["active"])


class CarNumberRepairTests(unittest.TestCase):
    def setUp(self):
        logger.LoggerFactory.create_logger(mode="crawl")
        self._dir = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self._dir.name) / 'data.db'}")
        fixture_server.seed_engine(self.engine)

    def tearDown(self):
        self.engine.dispose()
        self._dir.cleanup()

    def test_numbers_that_swallowed_the_next_line_are_extracted_again(self):
        detail, raw = models.detail_traffic_table, models.raw_content_table
        with self.engine.begin() as conn:
            rid = conn.execute(select(detail.c.ID).order_by(detail.c.ID)).scalars().first()
            conn.execute(update(detail).where(detail.c.ID == rid).values(차량번호="*발생일자:2026.09.01"))
            conn.execute(raw.delete().where(raw.c.ID == rid))
            conn.execute(raw.insert().values(ID=rid, raw_content="본문\n* 차량번호 : \n* 발생일자 : 2026.09.01", raw_type="report_body", saved_at=1))
            good = conn.execute(select(detail.c.ID, detail.c["차량번호"]).where(detail.c.ID != rid).where(detail.c["차량번호"] != "")).first()
        self.assertEqual(maintenance_service.repair_car_numbers(self.engine), 1)
        with self.engine.connect() as conn:
            self.assertEqual(conn.execute(select(detail.c["차량번호"]).where(detail.c.ID == rid)).scalar(), "")
            self.assertEqual(conn.execute(select(models.merge_traffic_table.c["차량번호"]).where(models.merge_traffic_table.c.ID == rid)).scalar(), "")
            self.assertEqual(conn.execute(select(detail.c["차량번호"]).where(detail.c.ID == good[0])).scalar(), good[1])  # 정상 값은 그대로
        self.assertEqual(maintenance_service.repair_car_numbers(self.engine), 0)


if __name__ == "__main__":
    unittest.main()
