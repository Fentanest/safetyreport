"""크롤링 저장 규칙 (저장 계층 재설계 R2, core/storage/reports_repo.py). 서버 스키마 6: 신고 한 건 = mysafety_report 한 행,
category 는 열 값이라 분류가 바뀌어도 표를 옮기지 않는다. 화면용 표는 mysafety_report_effective."""
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

    def save(self, record_id, category="traffic", title_fields=None, entry_value=None, ssnpc_cd=None, ssnpc_type=None, **detail):
        base = self.row(models.report_table, record_id) or {}
        fields = {c: base.get(c) for c in reports_repo.DETAIL_SITE_COLUMNS}
        fields.update(detail)
        rec = reports_repo.CrawledDetail(id=record_id, category=category, detail={"ID": record_id, **fields},
                                          title_fields=title_fields, entry_value=entry_value,
                                          ssnpc_cd=ssnpc_cd, ssnpc_type=ssnpc_type)
        return reports_repo.save_crawled(self.engine, [rec])

    def test_effective_row_is_updated_in_the_same_save(self):
        self.save("90000001", 처리내용="새 처리내용")
        self.assertEqual(self.row(models.report_effective_table, "90000001")["처리내용"], "새 처리내용")

    def test_category_change_updates_the_same_row_in_place(self):
        """스키마 6: category 는 표 이름이 아니라 열 값이다. 다른 메뉴로 다시 수집해도 같은 ID 행 하나의 category 만 바뀐다."""
        result = self.save("90000001", entry_value="생활불편신고-쓰레기, 폐기물")
        self.assertEqual(result.changed, [{"id": "90000001", "change_type": "변경", "category": "living", "previous_category": "traffic"}])
        row = self.row(models.report_table, "90000001")
        self.assertEqual(row["category"], "living")
        self.assertEqual(self.row(models.report_effective_table, "90000001")["category"], "living")
        # 표가 하나뿐이니 당연하지만, 옛 "표 이동" 가정이 남지 않았는지 명시적으로 확인
        with self.engine.connect() as conn:
            count = conn.execute(select(models.report_table).where(models.report_table.c.ID == "90000001")).fetchall()
        self.assertEqual(len(count), 1)

    def test_empty_identity_fields_do_not_blank_the_title(self):
        before = self.row(models.report_table, "90000001")
        self.save("90000001", title_fields={"상태": "수용", "신고번호": "", "신고명": "", "신고일": ""})
        after = self.row(models.report_table, "90000001")
        self.assertEqual(after["신고번호"], before["신고번호"])  # S-23
        self.assertEqual(after["신고명"], before["신고명"])

    def test_user_override_survives_recrawl(self):
        with self.engine.begin() as conn:
            conn.execute(models.report_override_table.insert().values(ID="90000001", column_name="처리내용", value="내 수정", updated_at=1))
        database.rebuild_effective(self.engine)
        self.assertEqual(self.row(models.report_effective_table, "90000001")["처리내용"], "내 수정")
        self.save("90000001", 처리내용="사이트 원본")
        self.assertEqual(self.row(models.report_table, "90000001")["처리내용"], "사이트 원본")  # 원본은 원본대로
        self.assertEqual(self.row(models.report_effective_table, "90000001")["처리내용"], "내 수정")  # 화면은 수정값

    def test_attachment_expiry_is_the_same_for_full_and_single_refresh(self):
        with self.engine.begin() as conn:
            conn.execute(update(models.report_table).where(models.report_table.c.ID == "90000001")
                         .values(신고일="2020-01-01", 첨부사진="https://example/1.jpg"))
        database.rebuild_effective(self.engine)
        self.assertEqual(self.row(models.report_effective_table, "90000001")["첨부사진"], reports_repo.EXPIRED_ATTACHMENT)
        self.save("90000001")
        self.assertEqual(self.row(models.report_effective_table, "90000001")["첨부사진"], reports_repo.EXPIRED_ATTACHMENT)
        self.assertEqual(self.row(models.report_table, "90000001")["첨부사진"], "https://example/1.jpg")  # 원본은 보존

    def test_official_coordinates_replace_previous_values_on_recrawl(self):
        self.save("90000001", 위도=37.560123456789, 경도=126.830123456789)
        row = self.row(models.report_table, "90000001")
        self.assertEqual((row["위도"], row["경도"], row["지오코딩상태"]),
                         (37.560123456789, 126.830123456789, "ok"))
        synced_at = row["synced_at"]
        self.save("90000001", 위도=37.56123456789, 경도=126.83123456789)
        row = self.row(models.report_table, "90000001")
        self.assertEqual((row["위도"], row["경도"]), (37.56123456789, 126.83123456789))
        self.assertEqual(row["synced_at"], synced_at)
        self.save("90000001", 위도=None, 경도=None)
        row = self.row(models.report_table, "90000001")
        self.assertEqual((row["위도"], row["경도"], row["지오코딩상태"]),
                         (None, None, "not_found"))

    def test_map_keeps_official_point_even_without_an_address(self):
        from services.report_stats_service import get_report_map_stats

        self.save("90000001", 위반장소="", 위도=37.560123456789, 경도=126.830123456789)
        payload = get_report_map_stats(self.engine, mode="raw")
        self.assertTrue(any(
            point["lat"] == 37.560123456789 and point["lng"] == 126.830123456789
            for point in payload["points"]
        ))

    def test_failures_are_reported_not_swallowed(self):
        bad = reports_repo.CrawledDetail(id="90000001", category="traffic", detail={"ID": "90000001"})
        with mock.patch.object(reports_repo, "_title_values", side_effect=RuntimeError("boom")):
            result = reports_repo.save_crawled(self.engine, [bad])
        self.assertEqual(result.saved, 0)
        self.assertEqual([rid for rid, _ in result.failed], ["90000001"])

    def test_legacy_tuple_adapter_keeps_detail_to_sql_contract(self):
        frame = pd.DataFrame([{"ID": "90000009", "처리상태": "수용", "처리내용": "튜플 경로", "종결여부": "Y"}])
        changed = database.detail_to_sql([(frame, "traffic", "자동차·교통위반-신호위반")], self.engine)
        self.assertEqual(changed, [{"id": "90000009", "change_type": "변경", "category": "traffic"}])
        self.assertEqual(self.row(models.report_effective_table, "90000009")["처리내용"], "튜플 경로")

    def test_start_saves_details_as_they_arrive_even_if_the_crawler_stops(self):
        """S-9: 크롤러가 중간에 예외로 멈춰도 그때까지 받은 상세는 저장돼 있다."""
        import start

        def stream():
            for rid in ("90000001", "90000002"):
                yield (pd.DataFrame([{"ID": rid, "처리상태": "수용", "처리내용": f"스트림 {rid}", "종결여부": "Y"}]), "traffic", "자동차·교통위반-신호위반")
            raise ConnectionError("네트워크 끊김")

        changed = start._save_details_as_they_arrive(self.engine, stream())
        self.assertEqual({c["id"] for c in changed}, {"90000001", "90000002"})
        self.assertEqual(self.row(models.report_effective_table, "90000002")["처리내용"], "스트림 90000002")

    def test_rating_submission_stores_score_and_updates_effective(self):
        """S-25: 일괄 별점 제출이 점수까지 기록하고 화면용 표에도 바로 보인다."""
        number = self.row(models.report_table, "90000004")["신고번호"]
        with mock.patch("services.community_capture.add_retry_id") as queue:
            database.sync_rating_status(self.engine, number, score=4, cause="")
            queue.assert_called_once_with("90000004", "rating_confirmed_refetch")
        merged = self.row(models.report_effective_table, "90000004")
        self.assertEqual((merged["만족도조사여부"], merged["별점"], merged["별점사유"]), ("참여 완료", 4, ""))
        database.sync_rating_status(self.engine, number)  # 점수 없이 부르면 점수는 그대로
        self.assertEqual(self.row(models.report_table, "90000004")["별점"], 4)

    def test_queue_report_number_resolution(self):
        """S-24: 큐 신고번호는 정확 일치 우선, 부분 일치는 딱 1건일 때만."""
        import start

        number = self.row(models.report_table, "90000004")["신고번호"]  # SPP-2602-9000004
        with self.engine.connect() as conn:
            self.assertEqual(start._resolve_report_number(conn, number), "90000004")
            self.assertEqual(start._resolve_report_number(conn, number[4:]), "90000004")  # 'SPP-' 없이
            self.assertIsNone(start._resolve_report_number(conn, "SPP-26"))  # 여러 건에 걸리는 부분 일치는 거부

    # ── SSNPC 코드(스키마 6) ────────────────────────────────────────────────────

    def test_ssnpc_code_is_stored_and_normalized(self):
        """응답 코드는 대문자·두 자리 소분류로 정규화해 저장하고, 코드가 있으면 코드로 분류한다."""
        rec = reports_repo.CrawledDetail(id="90009999", category="other", detail={"ID": "90009999"},
                                          ssnpc_cd=" ip ", ssnpc_type="5")
        reports_repo.save_crawled(self.engine, [rec])
        row = self.row(models.report_table, "90009999")
        self.assertEqual((row["ssnpc_cd"], row["ssnpc_type"], row["category"]), ("IP", "05", "parking"))

    def test_category_from_code_beats_entry_value(self):
        """코드가 있으면 entry_value(메뉴)는 참고하지 않는다(services/report_category.resolve)."""
        rec = reports_repo.CrawledDetail(id="90009998", category="other", detail={"ID": "90009998"},
                                          ssnpc_cd="DR", ssnpc_type="06", entry_value="자동차·교통위반-신호위반")
        reports_repo.save_crawled(self.engine, [rec])
        row = self.row(models.report_table, "90009998")
        self.assertEqual((row["ssnpc_cd"], row["ssnpc_type"], row["category"]), ("DR", "06", "living"))

    def test_missing_code_in_a_new_response_keeps_the_stored_code(self):
        """이번 응답에 코드가 없으면(None) 저장된 코드를 지우지 않고 그 코드로 다시 분류한다."""
        first = reports_repo.CrawledDetail(id="90009997", category="other", detail={"ID": "90009997"},
                                            ssnpc_cd="IP", ssnpc_type="05")
        reports_repo.save_crawled(self.engine, [first])
        second = reports_repo.CrawledDetail(id="90009997", category="other", detail={"ID": "90009997", "처리상태": "수용"})
        reports_repo.save_crawled(self.engine, [second])
        row = self.row(models.report_table, "90009997")
        self.assertEqual((row["ssnpc_cd"], row["ssnpc_type"], row["category"]), ("IP", "05", "parking"))

    def test_list_only_row_getting_its_first_detail_is_reported_as_new(self):
        """category NULL(목록만 본 신고)이 상세를 처음 받으면 '신규'로 보고한다 — 행이 이미 있어도 신규다."""
        with self.engine.begin() as conn:
            conn.execute(models.report_table.insert().values(
                ID="90009996", 상태="접수", 신고번호="SPP-TEST-LIST", 신고명="목록만", 신고일="2026-09-01", 감시목록="N"))
        rec = reports_repo.CrawledDetail(id="90009996", category="other", detail={"ID": "90009996"},
                                          entry_value="안전신고-생활안전")
        result = reports_repo.save_crawled(self.engine, [rec])
        self.assertEqual(result.changed, [{"id": "90009996", "change_type": "신규", "category": "safety"}])
        self.assertIsNotNone(self.row(models.report_effective_table, "90009996"))


if __name__ == "__main__":
    unittest.main()
