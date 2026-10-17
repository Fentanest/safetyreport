"""재파싱 공유 캡처 입력의 목록 값은 기존 행 (DB 최적화 결함 회귀).

일반 크롤 저장(`save_crawled`) 때 공유 payload 에 실린 목록 값(신고번호·신고명·
신고일·별점 등)은 만족도 조회까지 머지된 title_fields 에서 온다. 재파싱이 파서
출력만으로 캡처 입력을 만들면 같은 원본도 payload 가 달라져 가짜
`completed_observation` 이 나가고 공유 최신값(특히 별점)을 덮는다.
재파싱은 쓰기 잠금 안에서 읽은 기존 행 값으로 캡처 입력을 채워야 한다
(DB 목록 열 자체는 갱신하지 않음).

실제 `community_capture` 경로·실제 journal 로만 확인한다(캡처 가짜 금지).
"""
import json
import os
import shutil
import unittest
from unittest import mock

from sqlalchemy import select

import settings.settings as settings_module
from core.crawler import detail_pipeline
from core.database import models
from core.database.engine import get_engine
from core.storage import reports_repo
from core.utils import logger
from services import db_optimize
from services.community_store import CommunityStore


RID = "91000001"
RNUM = "SPP-2512-9100001"
RDATE = "2025-12-30"
RATING = 4
RATING_CAUSE = "친절하고 상세함"


def _envelope(seed="", status="수용", agency="서울특별시 강서경찰서 교통과",
              amount_sentence="과태료 40,000원을 부과하였습니다.", c_now=10,
              score=5):
    return {
        "resultCode": "0000",
        "result": {
            "C_NO": int(RID),
            "C_NAME": "홍길동" + seed,
            "C_EMAIL": "a@b.c",
            "C_PHONE": "01012345678",
            "C_PHONE2": "010-1234-5678",
            "C_ID": "fixtureid1",
            "C_NOW": c_now,
            "STSFDG_SCORE": score,
            "C_A_CONTENTS": ("본 신고는 안전신문고 앱의 자동차·교통위반 메뉴로 접수된 신고입니다\n"
                             "* 차량번호 : 12가3456\n* 발생일자 : 2026-09-01\n* 발생시각 : 08:00\n신고합니다" + seed),
            "RN_ADRES": "서울특별시 강서구 마곡동 1",
            "STTEMNT_NO": RNUM,
            "C_A_TITLE": "(자동차·교통위반) 신호위반",
            "C_DATE": RDATE + " 23:40:00",
            "answers": [
                {"C_MANAGER_TYPE_NM": status,
                 "C_MANAGE_CONTENTS": amount_sentence,
                 "C_MANAGE_ORG_NAME": agency,
                 "C_MANAGE_MAN": "김담당",
                 "C_DATE": "2026-01-02"},
            ],
        },
    }


def _satisfaction_at_crawl(client, spp_no):
    """일반 크롤의 만족도 조회(네트워크) 자리. 파서 점수(5)와 다른 확정 점수를 준다."""
    assert spp_no == RNUM
    return (RATING, RATING_CAUSE)


def _crawl_rec(envelope):
    """일반 크롤과 같은 함수로 저장용 rec를 만든다(만족도 조회만 스텁)."""
    from services import parser as doc_parser

    details = doc_parser.parse_json_details(envelope["result"])
    tup = detail_pipeline.build_detail_result(
        RID, details, progress_status=details.get("progress_status", ""),
        satisfaction_client=object(), satisfaction_fetcher=_satisfaction_at_crawl,
        raw_envelope=envelope, raw_source="crawl")
    return reports_repo.CrawledDetail.from_legacy_tuple(tup)


def _journal_rows():
    store = CommunityStore.open()
    return store.connect().execute(
        "SELECT payload_json, payload_sha256 FROM source_journal"
        " WHERE local_dataset_id=? AND source_report_id=?"
        " ORDER BY source_revision",
        (store.local_dataset_id(), RID)).fetchall()


def _db_row(engine):
    with engine.connect() as conn:
        return dict(conn.execute(select(models.report_table).where(
            models.report_table.c.ID == RID)).mappings().first())


class ReparseCaptureTitlesTests(unittest.TestCase):
    def setUp(self):
        logger.LoggerFactory.create_logger(mode="crawl")
        get_engine().dispose()
        for ext in ("", "-wal", "-shm"):
            if os.path.exists(settings_module.db_path + ext):
                os.remove(settings_module.db_path + ext)
        cpath = os.path.join(settings_module.datapath, "community.db")
        try:
            CommunityStore.open().close()
        except Exception:
            pass
        CommunityStore._forget(cpath)
        for ext in ("", "-wal", "-shm", "-journal"):
            if os.path.exists(cpath + ext):
                os.remove(cpath + ext)
        retry = os.path.join(settings_module.datapath, "community_capture_retry.json")
        if os.path.exists(retry):
            os.remove(retry)
        shutil.rmtree(settings_module.raw_details_dir, ignore_errors=True)
        ctx_patch = mock.patch("services.community_cloud.capture_context",
                               side_effect=lambda ctx: (ctx, "none"))
        ctx_patch.start()
        self.addCleanup(ctx_patch.stop)
        phone_patch = mock.patch.object(settings_module._instance, "phone_number", "010-1234-5678")
        phone_patch.start()
        self.addCleanup(phone_patch.stop)
        from core.database import database

        self.engine = get_engine()
        database.upgrade_schema(self.engine)

    def tearDown(self):
        get_engine().dispose()

    def _save_crawled(self, envelope):
        result = reports_repo.save_crawled(self.engine, [_crawl_rec(envelope)])
        self.assertEqual(result.saved, 1)
        self.assertEqual(result.failed, [])
        return result

    def test_same_source_reparse_emits_no_event_and_keeps_payload(self):
        envelope = _envelope()
        self._save_crawled(envelope)
        # 일반 크롤 저장이 별점까지 journal 에 남겼다(전제).
        rows = _journal_rows()
        self.assertEqual(len(rows), 1)
        first_payload = json.loads(rows[0]["payload_json"])
        self.assertEqual(first_payload["rating"], RATING)
        self.assertEqual(first_payload["report_date"], RDATE)
        row = _db_row(self.engine)
        self.assertEqual(row["별점"], RATING)
        self.assertEqual(row["신고일"], RDATE)
        self.assertEqual(row["신고번호"], RNUM)

        # 같은 원본으로 재파싱 → 바뀐 것 없음·새 이벤트 없음·payload 동일.
        from services import parser as doc_parser

        details = doc_parser.parse_json_details(envelope["result"])
        rec = db_optimize._build_rec(RID, details)
        change = reports_repo.reparse_one(self.engine, rec, trigger="reparse")
        self.assertIsNone(change)
        rows_after = _journal_rows()
        self.assertEqual(len(rows_after), 1)
        self.assertEqual(rows_after[0]["payload_json"], rows[0]["payload_json"])
        self.assertEqual(rows_after[0]["payload_sha256"], rows[0]["payload_sha256"])

        # db_optimize 로 1건을 처리해도 마찬가지다.
        import threading

        stop = threading.Event()
        result = db_optimize.process_all(
            self.engine, is_crawling=lambda: False, rebuild_pending=lambda: False,
            interval=0, wait_seconds=0, login_retry_seconds=0.01,
            sleep=lambda s: None, stop=stop,
            session_factory=lambda: (_ for _ in ()).throw(AssertionError("네트워크 금지")),
            fetch_fn=lambda rid: (_ for _ in ()).throw(AssertionError("네트워크 금지")))
        self.assertTrue(result["finished"])
        self.assertEqual(result["stats"].get("events", 0), 0)
        self.assertEqual(len(_journal_rows()), 1)
        self.assertEqual(_journal_rows()[0]["payload_json"], rows[0]["payload_json"])

    def test_changed_disposition_emits_one_event_with_existing_titles(self):
        self._save_crawled(_envelope())
        before = len(_journal_rows())
        self.assertEqual(before, 1)

        # 원본의 파서 결과가 실제로 바뀜(처분 불수용).
        changed = _envelope(seed="바뀜", status="불수용", agency="경기도 부천원미경찰서",
                            amount_sentence="처분이 불가합니다.", c_now=14)
        from services import parser as doc_parser

        details = doc_parser.parse_json_details(changed["result"])
        rec = db_optimize._build_rec(RID, details)
        change = reports_repo.reparse_one(self.engine, rec, trigger="reparse")
        self.assertIsNotNone(change)
        rows = _journal_rows()
        self.assertEqual(len(rows), before + 1)
        payload = json.loads(rows[-1]["payload_json"])
        self.assertEqual(payload["rating"], RATING)
        self.assertEqual(payload["report_date"], RDATE)
        store = CommunityStore.open()
        report_number = store.connect().execute(
            "SELECT report_number FROM source_journal WHERE local_dataset_id=?"
            " AND source_report_id=? ORDER BY source_revision DESC LIMIT 1",
            (store.local_dataset_id(), RID)).fetchone()["report_number"]
        self.assertEqual(report_number, RNUM)
        # DB 목록 열은 여전히 그대로다.
        row = _db_row(self.engine)
        self.assertEqual((row["별점"], row["신고일"], row["신고번호"]), (RATING, RDATE, RNUM))


if __name__ == "__main__":
    unittest.main()
