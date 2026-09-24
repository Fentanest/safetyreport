"""저장 계층의 알려진 결함을 '현재 동작'으로 고정한다 (저장 계층 재설계 R0, docs/plans/storage-refactor-plan.md §2).

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
    def test_S1_currently_recrawl_reverts_editor_change(self):
        """S-1(R2): 편집기로 고친 처리내용이 재크롤링의 사이트 원본으로 되돌아간다. 결정 D-1 이후엔 수정값이 남아야 한다."""
        from services import db_editor_service

        before = self.merge_row(models.merge_traffic_table, "90000001")
        fields = {k: before.get(k) or "" for k in db_editor_service._DETAIL_FIELDS}
        fields["처리내용"] = "내가 고친 처리내용"
        self.assertTrue(db_editor_service.update_record(self.engine, "traffic", "90000001", fields))
        self.assertEqual(self.merge_row(models.merge_traffic_table, "90000001")["처리내용"], "내가 고친 처리내용")

        self.crawl("90000001", 처리상태=before["처리상태"], 처리내용=before["처리내용"], 위반장소=before["위반장소"], 종결여부=before["종결여부"])
        database.merge_final(self.engine)
        self.assertEqual(self.merge_row(models.merge_traffic_table, "90000001")["처리내용"], before["처리내용"])

    def test_S2_currently_partial_editor_update_blanks_other_fields(self):
        """S-2(R2): 일부 필드만 보낸 편집이 나머지 상세 필드를 ''로 지운다."""
        from services import db_editor_service

        before = self.merge_row(models.merge_traffic_table, "90000001")
        self.assertTrue(before["처리기관"])
        db_editor_service.update_record(self.engine, "traffic", "90000001", {"처리내용": "일부만 수정"})
        after = self.detail_row(models.detail_traffic_table, "90000001")
        self.assertEqual(after["처리내용"], "일부만 수정")
        self.assertEqual(after["처리기관"], "")

    def test_S5_currently_null_vs_empty_counts_as_change(self):
        """S-5(R2): 내용이 같아도 DB 의 NULL 과 새 값 '' 를 다르게 보고 변경으로 친다(synced_at 갱신·변경 알림)."""
        base = self.detail_row(models.detail_traffic_table, "90000002")
        fields = dict(처리상태=base["처리상태"], 처리내용=base["처리내용"], 위반장소=base["위반장소"], 종결여부=base["종결여부"], 벌점="")
        self.crawl("90000002", **fields)
        self.assertEqual(self.crawl("90000002", **fields), [])  # 같은 값 두 번째 저장 → 변경 없음(정상)
        with self.engine.begin() as conn:
            conn.execute(update(models.detail_traffic_table).where(models.detail_traffic_table.c.ID == "90000002").values(벌점=None))
        self.assertEqual(self.crawl("90000002", **fields), [{"id": "90000002", "change_type": "변경"}])

    def test_S19_currently_null_closed_flag_is_never_recrawled(self):
        """S-19(R2): 종결여부 NULL 인 신고는 재크롤링 대상에서 빠진다(`!= 'Y'` 가 NULL 을 거름)."""
        with self.engine.connect() as conn:
            open_id = conn.execute(select(models.detail_traffic_table.c.ID).where(models.detail_traffic_table.c.종결여부 == "N")).scalars().first()
        self.assertIsNotNone(open_id)
        self.assertIn(open_id, database.get_pending_detail_ids(self.engine))
        with self.engine.begin() as conn:
            conn.execute(update(models.detail_traffic_table).where(models.detail_traffic_table.c.ID == open_id).values(종결여부=None))
        self.assertNotIn(open_id, database.get_pending_detail_ids(self.engine))

    def test_S26_currently_list_save_downgrades_poll_status(self):
        """S-26(R2, 결정 D-3): 목록 저장이 '참여 완료' 를 '참여 가능' 으로 되돌린다(상세 저장은 막음). Gemini G9 재현과 같음."""
        with self.engine.begin() as conn:
            conn.execute(update(models.title_table).where(models.title_table.c.ID == "90000001").values(만족도조사여부="참여 완료"))
        title = self.merge_row(models.title_table, "90000001")
        frame = pd.DataFrame([{k: title[k] for k in ("ID", "상태", "신고번호", "신고명", "신고일")} | {"만족도조사여부": "참여 가능"}])
        database.title_to_sql([frame], self.engine)
        self.assertEqual(self.merge_row(models.title_table, "90000001")["만족도조사여부"], "참여 가능")

    def test_S17_currently_change_payload_sends_none_string(self):
        """S-17(R1): 알림 payload 가 NULL 을 문자열 "None" 으로 보낸다."""
        import settings.settings as app_settings
        from services import crawl_state_store

        with self.engine.begin() as conn:
            conn.execute(update(models.merge_traffic_table).where(models.merge_traffic_table.c.ID == "90000001").values(담당자=None))
        crawl_state_store.save_crawl_changes(self.engine, [{"id": "90000001", "change_type": "변경"}])
        payload = json.loads((Path(app_settings.datapath) / "crawl_changes.json").read_text(encoding="utf-8"))
        item = next(p for p in payload if p.get("신고번호") == self.merge_row(models.merge_traffic_table, "90000001")["신고번호"])
        self.assertEqual(item["담당자"], "None")
        crawl_state_store.clear_crawl_changes()

    def test_S35_currently_api_records_turn_null_into_empty_and_int_into_float(self):
        """S-35(R1): API 조회 경로는 NULL→'' , 정수 열(별점·synced_at)에 NULL 이 섞이면 실수로 보낸다. DB 파일 경로는 원형 그대로라 채널마다 값이 다르다."""
        from services import report_query_service

        with self.engine.begin() as conn:
            conn.execute(update(models.merge_traffic_table).where(models.merge_traffic_table.c.ID == "90000001").values(담당자=None, 별점=None))
            conn.execute(update(models.merge_traffic_table).where(models.merge_traffic_table.c.ID == "90000002").values(별점=3))
        records = {r["ID"]: r for r in report_query_service.get_traffic_records(self.engine)}
        self.assertEqual(records["90000001"]["담당자"], "")
        self.assertEqual(records["90000001"]["별점"], "")
        self.assertIsInstance(records["90000002"]["별점"], float)


if __name__ == "__main__":
    unittest.main()
