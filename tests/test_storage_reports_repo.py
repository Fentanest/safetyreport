"""크롤링 저장 규칙 (저장 계층 재설계 R2, core/storage/reports_repo.py)."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd
from sqlalchemy import create_engine, select, update

from core.database import database, models
from core.storage import reports_repo
from core.utils import logger
from scripts.dev import fixture_server


class ReportsRepoTests(unittest.TestCase):
    def setUp(self):
        logger.LoggerFactory.create_logger(mode="crawl")
        self._dir = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self._dir.name) / 'data.db'}")
        fixture_server.seed_engine(self.engine)

    def tearDown(self):
        self.engine.dispose()
        self._dir.cleanup()

    def row(self, table, record_id):
        with self.engine.connect() as conn:
            found = conn.execute(select(table).where(table.c.ID == record_id)).mappings().first()
            return dict(found) if found else None

    def save(self, record_id, category="traffic", title_fields=None, **detail):
        base = self.row(models.detail_traffic_table, record_id) or self.row(models.detail_parking_table, record_id) or self.row(models.detail_other_table, record_id) or {}
        fields = {c: base.get(c) for c in reports_repo.DETAIL_SITE_COLUMNS}
        fields.update(detail)
        rec = reports_repo.CrawledDetail(id=record_id, category=category, detail={"ID": record_id, **fields}, title_fields=title_fields)
        return reports_repo.save_crawled(self.engine, [rec])

    def test_merge_row_is_updated_in_the_same_save(self):
        self.save("90000001", 처리내용="새 처리내용")
        self.assertEqual(self.row(models.merge_traffic_table, "90000001")["처리내용"], "새 처리내용")

    def test_category_move_removes_the_old_rows(self):
        self.save("90000001", category="other")
        self.assertIsNone(self.row(models.detail_traffic_table, "90000001"))
        self.assertIsNone(self.row(models.merge_traffic_table, "90000001"))
        self.assertIsNotNone(self.row(models.merge_other_table, "90000001"))

    def test_empty_identity_fields_do_not_blank_the_title(self):
        before = self.row(models.title_table, "90000001")
        self.save("90000001", title_fields={"상태": "수용", "신고번호": "", "신고명": "", "신고일": ""})
        after = self.row(models.title_table, "90000001")
        self.assertEqual(after["신고번호"], before["신고번호"])  # S-23
        self.assertEqual(after["신고명"], before["신고명"])

    def test_user_override_survives_recrawl(self):
        with self.engine.begin() as conn:
            conn.execute(models.report_override_table.insert().values(ID="90000001", column_name="처리내용", value="내 수정", updated_at=1))
        database.merge_final(self.engine)
        self.assertEqual(self.row(models.merge_traffic_table, "90000001")["처리내용"], "내 수정")
        self.save("90000001", 처리내용="사이트 원본")
        self.assertEqual(self.row(models.detail_traffic_table, "90000001")["처리내용"], "사이트 원본")  # 원본은 원본대로
        self.assertEqual(self.row(models.merge_traffic_table, "90000001")["처리내용"], "내 수정")  # 화면은 수정값

    def test_attachment_expiry_is_the_same_for_full_and_single_refresh(self):
        with self.engine.begin() as conn:
            conn.execute(update(models.title_table).where(models.title_table.c.ID == "90000001").values(신고일="2020-01-01"))
            conn.execute(update(models.detail_traffic_table).where(models.detail_traffic_table.c.ID == "90000001").values(첨부사진="https://example/1.jpg"))
        database.merge_final(self.engine)
        self.assertEqual(self.row(models.merge_traffic_table, "90000001")["첨부사진"], reports_repo.EXPIRED_ATTACHMENT)
        self.save("90000001")
        self.assertEqual(self.row(models.merge_traffic_table, "90000001")["첨부사진"], reports_repo.EXPIRED_ATTACHMENT)
        self.assertEqual(self.row(models.detail_traffic_table, "90000001")["첨부사진"], "https://example/1.jpg")  # 원본은 보존

    def test_geocoding_runs_before_the_transaction(self):
        from services import geocode_service

        seen = {}

        def fake_prepare(engine, address, *, existing_record=None, conn=None):
            seen["conn"] = conn
            return geocode_service.build_pending_geo_payload(address, status="pending")

        with mock.patch.object(geocode_service, "prepare_geo_payload", side_effect=fake_prepare):
            self.save("90000001")
        self.assertIn("conn", seen)
        self.assertIsNone(seen["conn"])  # 저장 트랜잭션 연결을 넘기지 않음 → HTTP 가 트랜잭션 밖(S-7)

    def test_failures_are_reported_not_swallowed(self):
        bad = reports_repo.CrawledDetail(id="90000001", category="traffic", detail={"ID": "90000001"})
        with mock.patch.object(reports_repo, "_update_title", side_effect=RuntimeError("boom")):
            result = reports_repo.save_crawled(self.engine, [bad])
        self.assertEqual(result.saved, 0)
        self.assertEqual([rid for rid, _ in result.failed], ["90000001"])

    def test_legacy_tuple_adapter_keeps_detail_to_sql_contract(self):
        frame = pd.DataFrame([{"ID": "90000009", "처리상태": "수용", "처리내용": "튜플 경로", "종결여부": "Y"}])
        changed = database.detail_to_sql([(frame, "traffic", "자동차·교통위반-신호위반")], self.engine)
        self.assertEqual(changed, [{"id": "90000009", "change_type": "변경"}])
        self.assertEqual(self.row(models.merge_traffic_table, "90000009")["처리내용"], "튜플 경로")

    def test_start_saves_details_as_they_arrive_even_if_the_crawler_stops(self):
        """S-9: 크롤러가 중간에 예외로 멈춰도 그때까지 받은 상세는 저장돼 있다."""
        import start

        def stream():
            for rid in ("90000001", "90000002"):
                yield (pd.DataFrame([{"ID": rid, "처리상태": "수용", "처리내용": f"스트림 {rid}", "종결여부": "Y"}]), "traffic", "자동차·교통위반-신호위반")
            raise ConnectionError("네트워크 끊김")

        changed = start._save_details_as_they_arrive(self.engine, stream())
        self.assertEqual({c["id"] for c in changed}, {"90000001", "90000002"})
        self.assertEqual(self.row(models.merge_traffic_table, "90000002")["처리내용"], "스트림 90000002")

    def test_rating_submission_stores_score_and_updates_merge(self):
        """S-25: 일괄 별점 제출이 점수까지 기록하고 화면용 표에도 바로 보인다."""
        number = self.row(models.title_table, "90000004")["신고번호"]
        database.sync_rating_status(self.engine, number, score=4, cause="")
        merged = self.row(models.merge_traffic_table, "90000004")
        self.assertEqual((merged["만족도조사여부"], merged["별점"], merged["별점사유"]), ("참여 완료", 4, ""))
        database.sync_rating_status(self.engine, number)  # 점수 없이 부르면 점수는 그대로
        self.assertEqual(self.row(models.title_table, "90000004")["별점"], 4)

    def test_queue_report_number_resolution(self):
        """S-24: 큐 신고번호는 정확 일치 우선, 부분 일치는 딱 1건일 때만."""
        import start

        number = self.row(models.title_table, "90000004")["신고번호"]  # SPP-2602-9000004
        with self.engine.connect() as conn:
            self.assertEqual(start._resolve_report_number(conn, number), "90000004")
            self.assertEqual(start._resolve_report_number(conn, number[4:]), "90000004")  # 'SPP-' 없이
            self.assertIsNone(start._resolve_report_number(conn, "SPP-26"))  # 여러 건에 걸리는 부분 일치는 거부


if __name__ == "__main__":
    unittest.main()
