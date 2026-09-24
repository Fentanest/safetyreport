"""저장 계층의 알려진 결함을 '현재 동작'으로 고정한다(고친 것은 이름에서 currently_ 를 떼고 올바른 동작을 확인) (저장 계층 재설계 R0, docs/plans/storage-refactor-plan.md §2).

각 테스트는 지금의 잘못된 동작을 그대로 확인한다. 해당 단계(R1·R2 …)에서 결함을 고치면 이 테스트가 실패한다 —
그때 기대값을 올바른 동작으로 뒤집고 테스트 이름의 `currently_` 를 떼어 회귀 테스트로 바꾼다.
"""
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, select, update

from core.database import database, models
from core.utils import logger
from scripts.dev import fixture_server


class _SeededDb(unittest.TestCase):
    def setUp(self):
        logger.LoggerFactory.create_logger(mode="crawl")
        self._dir = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self._dir.name) / 'data.db'}")
        fixture_server.seed_engine(self.engine)

    def tearDown(self):
        self.engine.dispose()
        self._dir.cleanup()

    def merge_row(self, table, record_id):
        with self.engine.connect() as conn:
            return dict(conn.execute(select(table).where(table.c.ID == record_id)).mappings().one())

    def detail_row(self, table, record_id):
        return self.merge_row(table, record_id)

    def crawl(self, record_id, category="traffic", entry_value="자동차·교통위반-신호위반", **fields):
        row = {"ID": record_id, **fields}
        return database.detail_to_sql([(pd.DataFrame([row]), category, entry_value)], self.engine)


class ServerKnownDefectTests(_SeededDb):
    def test_S1_editor_change_survives_recrawl(self):
        """S-1(R2c 고침, 결정 D-1): 편집기로 고친 처리내용은 수정값 표에 남아 재크롤링 뒤에도 화면에 보인다. 원본은 원본대로."""
        from services import db_editor_service

        before = self.merge_row(models.merge_traffic_table, "90000001")
        fields = {k: before.get(k) or "" for k in db_editor_service._DETAIL_FIELDS}
        fields["처리내용"] = "내가 고친 처리내용"
        self.assertTrue(db_editor_service.update_record(self.engine, "traffic", "90000001", fields))
        self.assertEqual(self.merge_row(models.merge_traffic_table, "90000001")["처리내용"], "내가 고친 처리내용")

        self.crawl("90000001", 처리상태=before["처리상태"], 처리내용=before["처리내용"], 위반장소=before["위반장소"], 종결여부=before["종결여부"])
        database.merge_final(self.engine)
        self.assertEqual(self.merge_row(models.merge_traffic_table, "90000001")["처리내용"], "내가 고친 처리내용")
        self.assertEqual(self.detail_row(models.detail_traffic_table, "90000001")["처리내용"], before["처리내용"])
        with self.engine.connect() as conn:  # 다른 필드는 원본과 같아 수정값을 만들지 않는다
            overrides = conn.execute(select(models.report_override_table.c.column_name)).scalars().all()
        self.assertEqual(overrides, ["처리내용"])

    def test_S2_partial_editor_update_touches_only_sent_fields(self):
        """S-2(R2c 고침): 일부 필드만 보낸 편집은 그 필드만 바꾼다."""
        from services import db_editor_service

        before = self.merge_row(models.merge_traffic_table, "90000001")
        self.assertTrue(before["처리기관"])
        db_editor_service.update_record(self.engine, "traffic", "90000001", {"처리내용": "일부만 수정"})
        after = self.merge_row(models.merge_traffic_table, "90000001")
        self.assertEqual(after["처리내용"], "일부만 수정")
        self.assertEqual(after["처리기관"], before["처리기관"])
        # 원본과 같은 값으로 다시 고치면 수정값이 사라진다(되돌리기)
        db_editor_service.update_record(self.engine, "traffic", "90000001", {"처리내용": before["처리내용"]})
        with self.engine.connect() as conn:
            self.assertEqual(conn.execute(select(models.report_override_table)).fetchall(), [])

    def test_S5_null_vs_empty_is_not_a_change(self):
        """S-5(R2b 고침): 내용이 같으면 DB 의 NULL 과 새 값 '' 를 같게 본다(가짜 변경 알림·synced_at 오염 없음)."""
        base = self.detail_row(models.detail_traffic_table, "90000002")
        fields = dict(처리상태=base["처리상태"], 처리내용=base["처리내용"], 위반장소=base["위반장소"], 종결여부=base["종결여부"], 벌점="")
        self.crawl("90000002", **fields)
        self.assertEqual(self.crawl("90000002", **fields), [])  # 같은 값 두 번째 저장 → 변경 없음(정상)
        with self.engine.begin() as conn:
            conn.execute(update(models.detail_traffic_table).where(models.detail_traffic_table.c.ID == "90000002").values(벌점=None))
        self.assertEqual(self.crawl("90000002", **fields), [])
        self.assertEqual(self.crawl("90000002", **{**fields, "처리내용": "실제로 바뀐 내용"}), [{"id": "90000002", "change_type": "변경"}])

    def test_S19_null_closed_flag_is_recrawled(self):
        """S-19(R2a 고침): 종결여부 NULL(모름) 인 신고도 재크롤링 대상이다."""
        with self.engine.connect() as conn:
            open_id = conn.execute(select(models.detail_traffic_table.c.ID).where(models.detail_traffic_table.c.종결여부 == "N")).scalars().first()
        self.assertIsNotNone(open_id)
        self.assertIn(open_id, database.get_pending_detail_ids(self.engine))
        with self.engine.begin() as conn:
            conn.execute(update(models.detail_traffic_table).where(models.detail_traffic_table.c.ID == open_id).values(종결여부=None))
        self.assertIn(open_id, database.get_pending_detail_ids(self.engine))

    def test_S26_list_save_keeps_completed_poll_status(self):
        """S-26(R2a 고침, 결정 D-3): 목록 저장은 '참여 완료' 를 되돌리지 않는다(확정 미참여 재분류는 상세 저장만)."""
        with self.engine.begin() as conn:
            conn.execute(update(models.title_table).where(models.title_table.c.ID == "90000001").values(만족도조사여부="참여 완료"))
        title = self.merge_row(models.title_table, "90000001")
        frame = pd.DataFrame([{k: title[k] for k in ("ID", "상태", "신고번호", "신고명", "신고일")} | {"만족도조사여부": "참여 가능"}])
        database.title_to_sql([frame], self.engine)
        self.assertEqual(self.merge_row(models.title_table, "90000001")["만족도조사여부"], "참여 완료")
        frame2 = frame.assign(만족도조사여부="")  # 빈 값도 기존 유지
        database.title_to_sql([frame2], self.engine)
        self.assertEqual(self.merge_row(models.title_table, "90000001")["만족도조사여부"], "참여 완료")

    def test_S17_change_payload_sends_empty_text_for_null(self):
        """S-17(R1c 고침): 알림 payload 가 NULL 을 "None" 글자로 보내지 않는다(표시용이라 '' — Kotlin optString 이 null 을 "null" 로 바꿈)."""
        import settings.settings as app_settings
        from services import crawl_state_store

        with self.engine.begin() as conn:
            conn.execute(update(models.merge_traffic_table).where(models.merge_traffic_table.c.ID == "90000001").values(담당자=None))
        crawl_state_store.save_crawl_changes(self.engine, [{"id": "90000001", "change_type": "변경"}])
        payload = json.loads((Path(app_settings.datapath) / "crawl_changes.json").read_text(encoding="utf-8"))
        item = next(p for p in payload if p.get("신고번호") == self.merge_row(models.merge_traffic_table, "90000001")["신고번호"])
        self.assertEqual(item["담당자"], "")
        crawl_state_store.clear_crawl_changes()

    def test_S35_api_channel_keeps_null_and_integers(self):
        """S-35(R1c 고침): 모바일 API 채널(exact_values)은 NULL 을 None, 정수 열을 정수로 보낸다. 웹 화면 경로는 예전처럼 ''."""
        from services import report_query_service

        with self.engine.begin() as conn:
            conn.execute(update(models.merge_traffic_table).where(models.merge_traffic_table.c.ID == "90000001").values(담당자=None, 별점=None))
            conn.execute(update(models.merge_traffic_table).where(models.merge_traffic_table.c.ID == "90000002").values(별점=3))
        api = {r["ID"]: r for r in report_query_service.get_traffic_records(self.engine, exact_values=True)}
        self.assertIsNone(api["90000001"]["담당자"])
        self.assertIsNone(api["90000001"]["별점"])
        self.assertEqual(api["90000002"]["별점"], 3)
        self.assertIs(type(api["90000002"]["별점"]), int)
        json.dumps(list(api.values()), ensure_ascii=False)  # FastAPI 가 직렬화할 수 있는 값만
        web = {r["ID"]: r for r in report_query_service.get_traffic_records(self.engine)}
        self.assertEqual(web["90000001"]["담당자"], "")

    def test_S4_backfill_fills_coordinates_for_an_edited_address(self):
        """S-4(R6 고침): 편집기로 고친 주소가 캐시에 없으면 화면용 표 좌표가 비는데, 백필이 그 주소도 채운다. 상세의 원본 좌표는 그대로."""
        from unittest import mock
        from services import db_editor_service, geocode_service

        detail_before = self.detail_row(models.detail_traffic_table, "90000001")
        address = "서울특별시 중구 세종대로 110"
        pending_before = geocode_service.count_pending_reports(self.engine)
        db_editor_service.update_record(self.engine, "traffic", "90000001", {"위반장소": address})
        merged = self.merge_row(models.merge_traffic_table, "90000001")
        self.assertEqual((merged["위반장소"], merged["위도"], merged["지오코딩상태"]), (address, None, "pending"))
        self.assertEqual(geocode_service.count_pending_reports(self.engine), pending_before + 1)

        normalized = geocode_service.normalize_address(address)
        with self.engine.begin() as conn:  # 키 없이도 캐시에 있으면 채운다
            conn.execute(models.geocode_cache_table.insert().values(
                주소정규화=normalized, 원본주소=normalized, 행정구역="서울특별시 중구", 위도=37.5663, 경도=126.9779, 상태="ok", source="kakao"))
        self.assertGreaterEqual(geocode_service.count_cache_backfillable_reports(self.engine), 1)
        with mock.patch.object(geocode_service, "has_kakao_rest_api_key", return_value=False):
            geocode_service.backfill_missing_report_coordinates(self.engine, limit=500)

        merged = self.merge_row(models.merge_traffic_table, "90000001")
        self.assertEqual((merged["위도"], merged["경도"], merged["지오코딩상태"]), (37.5663, 126.9779, "ok"))
        detail_after = self.detail_row(models.detail_traffic_table, "90000001")
        self.assertEqual((detail_after["위반장소"], detail_after["위도"]), (detail_before["위반장소"], detail_before["위도"]))
        self.assertEqual(geocode_service.count_pending_reports(self.engine), pending_before)

    def test_S8_closed_parking_photo_times_are_retried(self):
        """S-8(R6 고침): 종결돼 다시 크롤링되지 않는 주정차 신고도 촬영 시각을 다시 시도한다(6개월 이내, 건수 제한)."""
        from datetime import datetime, timedelta
        from unittest import mock
        from services import photo_capture_time

        detail, title = models.detail_parking_table, models.title_table
        with self.engine.connect() as conn:
            ids = conn.execute(select(detail.c.ID).order_by(detail.c.ID)).scalars().all()[:2]
        self.assertEqual(len(ids), 2, "fixture 에 주정차 신고 2건 이상 필요")
        recent, old = ids
        today = datetime.now()
        with self.engine.begin() as conn:
            for rid, reported in ((recent, today - timedelta(days=10)), (old, today - timedelta(days=400))):
                conn.execute(update(detail).where(detail.c.ID == rid).values(
                    종결여부="Y", 첨부사진=f"https://x/{rid}.jpg", 사진_첫촬영=None, 사진_끝촬영=None, 사진_촬영수=None))
                conn.execute(update(title).where(title.c.ID == rid).values(신고일=reported.strftime("%Y-%m-%d")))

        with mock.patch.object(photo_capture_time, "fetch_capture_time", return_value="2026-09-14 08:01:02") as fetch:
            self.assertEqual(photo_capture_time.backfill_missing(self.engine), 1)
        fetch.assert_called_once_with(f"https://x/{recent}.jpg")
        self.assertEqual(self.merge_row(models.merge_parking_table, recent)["사진_촬영수"], 1)
        self.assertEqual(self.detail_row(detail, recent)["사진_첫촬영"], "2026-09-14 08:01:02")
        self.assertIsNone(self.detail_row(detail, old)["사진_촬영수"])  # 6개월 지난 첨부는 시도 안 함

        with mock.patch.object(photo_capture_time, "fetch_capture_time", side_effect=OSError("down")):
            self.assertEqual(photo_capture_time.backfill_missing(self.engine), 0)  # 이미 채운 건 다시 안 함, 오류는 넘어감

    def test_S34_watchlist_remove_accepts_ids(self):
        """S-34(R6 고침): 감시목록 제거도 추가처럼 ID 를 신고번호로 바꿔 지운다. 신고가 없는 신고번호도 그대로 지운다."""
        from unittest import mock
        from web.routers import watchlist_route

        report_number = self.merge_row(models.merge_traffic_table, "90000001")["신고번호"]
        with mock.patch.object(watchlist_route, "engine", self.engine):
            watchlist_route.add_to_watchlist(watchlist_route.WatchlistReq(rnums=["90000001"]))
            with self.engine.begin() as conn:
                conn.execute(models.watchlist_table.insert().values(신고번호="SPP-GONE"))
            self.assertEqual(self.merge_row(models.merge_traffic_table, "90000001")["감시목록"], "Y")
            result = watchlist_route.remove_from_watchlist(watchlist_route.WatchlistReq(rnums=["90000001", "SPP-GONE"]))
        with self.engine.connect() as conn:
            remaining = set(conn.execute(select(models.watchlist_table.c.신고번호)).scalars())
        self.assertFalse({report_number, "SPP-GONE"} & remaining)
        self.assertTrue(remaining)  # 다른 감시 항목은 그대로
        self.assertEqual(self.merge_row(models.merge_traffic_table, "90000001")["감시목록"], "N")
        self.assertIn("2건", result["message"])

if __name__ == "__main__":
    unittest.main()
