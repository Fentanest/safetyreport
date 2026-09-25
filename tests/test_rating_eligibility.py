"""별점 대상 판정 — 모바일과 같은 규칙(공용 contracts/rating-eligibility-vectors.json) + 제출 단계 사전 확인."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from services import rating_eligibility

ROOT = Path(__file__).resolve().parents[1]
VECTORS = json.loads((ROOT / "contracts" / "rating-eligibility-vectors.json").read_text(encoding="utf-8"))


class EligibilityVectorTests(unittest.TestCase):
    def test_shared_vectors(self):
        for case in VECTORS["eligibility_cases"]:
            with self.subTest(case=case):
                self.assertEqual(
                    rating_eligibility.ineligible_reason(case["poll_status"], case["status"]),
                    case["reason"],
                )


class CauseVectorTests(unittest.TestCase):
    def test_shared_cause_vectors(self):
        self.assertEqual(VECTORS["rating_cause_max"], rating_eligibility.RATING_CAUSE_MAX)
        for case in VECTORS["cause_cases"]:
            raw = case["input"]
            if raw is not None and "repeat" in case:
                raw = raw * case["repeat"]
            with self.subTest(case={k: v for k, v in case.items() if k != "input"} | {"input": (case["input"] or "")[:10]}):
                normalized = rating_eligibility.normalize_cause(raw)
                if "normalized" in case:
                    self.assertEqual(normalized, case["normalized"])
                else:
                    self.assertEqual(len(normalized), case["normalized_length"])
                self.assertEqual(rating_eligibility.cause_error(raw) is not None, case["too_long"])


class BatchPreSkipTests(unittest.TestCase):
    """run_batch_rating 이 신고번호로 병합 행을 찾아 사전 스킵하고, 모바일이 읽는 `스킵: [SPP-…] 사유` 줄을 남긴다."""

    def test_pre_skip_by_report_number_logs_each_report(self):
        from services import star_rating_service

        records = [
            {"ID": "1", "신고번호": "SPP-1", "만족도조사여부": "참여 완료", "처리상태": "수용"},
            {"ID": "2", "신고번호": "SPP-2", "만족도조사여부": "참여 가능", "처리상태": "검토중"},
        ]
        logged = []
        fake_log = mock.Mock()
        fake_log.info.side_effect = lambda m: logged.append(m)
        fake_log.warning.side_effect = lambda m: logged.append(m)
        fake_log.error.side_effect = lambda m: logged.append(m)
        with mock.patch.object(star_rating_service, "get_engine"), \
             mock.patch.object(star_rating_service.database, "get_merged_records_by_report_numbers", return_value=records) as lookup, \
             mock.patch.object(star_rating_service.logger.LoggerFactory, "star_log", fake_log), \
             mock.patch.object(star_rating_service.requests, "Session") as session_cls, \
             mock.patch.object(star_rating_service.time, "sleep"):
            session_cls.return_value.get.side_effect = Exception("network off")
            star_rating_service.run_batch_rating(["SPP-1", "SPP-2"], score=5)
        lookup.assert_called_once()
        self.assertEqual(lookup.call_args.args[1], ["SPP-1", "SPP-2"])
        self.assertIn("  - 스킵: [SPP-1] 이미 만족도 조사에 참여한 신고입니다.", logged)
        self.assertIn("  - 스킵: [SPP-2] 처리중 상태에서는 만족도 조사를 진행할 수 없습니다.", logged)
        self.assertTrue(any("성공: 0, 스킵: 2, 실패: 0" in m for m in logged))
        session_cls.return_value.post.assert_not_called()


class _Resp:
    def __init__(self, status=200, result=None, text=""):
        self.status_code = status
        self._result = result
        self.text = text

    def json(self):
        return {"result": self._result}


class SubmitFlowTests(unittest.TestCase):
    """제출 흐름(가짜 사이트): 사유 전송, 제출 뒤 재확인, 재확인 실패 시 재시도, 끝까지 확인 못 하면 실패."""

    def run_batch(self, gets, *, cause="감사합니다", score=4, retries=1):
        from services import star_rating_service

        logged = []
        fake_log = mock.Mock()
        for level in ("info", "warning", "error"):
            getattr(fake_log, level).side_effect = lambda m: logged.append(m)
        session = mock.Mock()
        session.headers = {}
        # 첫 get 은 워밍업(사이트 첫 화면), 그다음부터 점수 조회
        session.get.side_effect = [_Resp()] + list(gets)
        session.post.return_value = _Resp(200)
        synced = []
        with mock.patch.object(star_rating_service, "get_engine"), \
             mock.patch.object(star_rating_service.database, "get_merged_records_by_report_numbers", return_value=[]), \
             mock.patch.object(star_rating_service.database, "sync_rating_status", side_effect=lambda *a, **k: synced.append(k)), \
             mock.patch.object(star_rating_service.logger.LoggerFactory, "star_log", fake_log), \
             mock.patch.object(star_rating_service.requests, "Session", return_value=session), \
             mock.patch.object(star_rating_service.settings, "max_retry_attemps", retries), \
             mock.patch.object(star_rating_service.settings, "retry_interval", 0), \
             mock.patch.object(star_rating_service.settings, "phone_number", "01000000000"), \
             mock.patch.object(star_rating_service.time, "sleep"):
            star_rating_service.run_batch_rating(["SPP-9"], score=score, cause=cause)
        return logged, session, synced

    def test_cause_is_sent_and_success_needs_site_confirmation(self):
        logged, session, synced = self.run_batch([
            _Resp(result={"STSFDG_SCORE": 0}),
            _Resp(result={"STSFDG_SCORE": 4, "STSFDG_CAUSE": "감사합니다"}),
        ], cause="  감사합니다\r\n")
        payload = session.post.call_args.kwargs["data"]
        self.assertEqual(payload["STSFDG_CAUSE"], "감사합니다")
        self.assertEqual(payload["STSFDG_SCORE"], "4")
        self.assertIn("  - [SPP-9] 4점 별점 부여 성공 (API)", logged)
        self.assertEqual(synced[-1], {"score": 4, "cause": "감사합니다"})
        self.assertTrue(any("성공: 1, 스킵: 0, 실패: 0" in m for m in logged))

    def test_unconfirmed_submit_retries_and_counts_success_once_confirmed(self):
        logged, session, _ = self.run_batch([
            _Resp(result={"STSFDG_SCORE": 0}),          # 사전 확인
            _Resp(result={"STSFDG_SCORE": 0}),          # 제출 뒤 확인 — 아직 안 보임
            _Resp(result={"STSFDG_SCORE": 4, "STSFDG_CAUSE": "감사합니다"}),  # 재시도의 사전 확인에서 보임
        ])
        self.assertEqual(session.post.call_count, 1)  # 다시 제출하지 않는다
        self.assertIn("  - [SPP-9] 4점 별점 부여 성공 (API)", logged)
        self.assertFalse(any("스킵: [SPP-9]" in m for m in logged))

    def test_never_confirmed_is_a_failure_not_a_success(self):
        logged, _, synced = self.run_batch([
            _Resp(result={"STSFDG_SCORE": 0}),
            _Resp(result={"STSFDG_SCORE": 0}),
            _Resp(result={"STSFDG_SCORE": 0}),
            _Resp(result={"STSFDG_SCORE": 0}),
        ])
        self.assertFalse(any("별점 부여 성공" in m for m in logged))
        self.assertTrue(any(m.startswith("  - 최종 실패: [SPP-9] 오류 발생:") for m in logged))
        self.assertEqual(synced, [])
        self.assertTrue(any("성공: 0, 스킵: 0, 실패: 1" in m for m in logged))

    def test_site_dropping_the_cause_is_warned_and_site_value_saved(self):
        logged, _, synced = self.run_batch([
            _Resp(result={"STSFDG_SCORE": 0}),
            _Resp(result={"STSFDG_SCORE": 4, "STSFDG_CAUSE": ""}),
        ])
        self.assertTrue(any("사이트에 저장된 사유가 보낸 사유와 다릅니다" in m for m in logged))
        self.assertEqual(synced[-1], {"score": 4, "cause": ""})

    def test_empty_site_cause_is_read_from_the_popup_like_mobile(self):
        popup = '<textarea id="STSFDG_CAUSE" readonly>감사합니다</textarea>'
        logged, _, synced = self.run_batch([
            _Resp(result={"STSFDG_SCORE": 0}),
            _Resp(result={"STSFDG_SCORE": 4, "STSFDG_CAUSE": ""}),
            _Resp(text=popup),
        ])
        self.assertEqual(synced[-1], {"score": 4, "cause": "감사합니다"})
        self.assertFalse(any("사유와 다릅니다" in m for m in logged))

    def test_already_rated_on_site_is_skipped_with_site_values(self):
        logged, session, synced = self.run_batch([_Resp(result={"STSFDG_SCORE": 5, "STSFDG_CAUSE": "예전 사유"})])
        session.post.assert_not_called()
        self.assertIn("  - 스킵: [SPP-9] 이미 만족도 조사에 참여하셨습니다.", logged)
        self.assertEqual(synced[-1], {"score": 5, "cause": "예전 사유"})


class ApiContractTests(unittest.TestCase):
    """모바일 Client 계약: /api/v1/app/config 의 capabilities, /api/v1/rating/start 의 cause."""

    def test_app_config_advertises_rating_cause(self):
        from web.routers import api_route

        data = api_route.get_app_config(_="key")["data"]
        self.assertIn("rating_cause", data["capabilities"])
        self.assertEqual(data["rating_cause_max"], rating_eligibility.RATING_CAUSE_MAX)

    def _start(self, body):
        import asyncio

        from web.routers import api_route

        request = mock.Mock()

        async def json_body():
            return body

        request.json = json_body
        with mock.patch.object(api_route.rating_service, "start_batch_rating", return_value=["SPP-1"]) as start:
            result = asyncio.run(api_route.api_start_batch_rating(request, _="key"))
        return result, start

    def test_rating_start_passes_cause_and_old_clients_get_empty(self):
        _, start = self._start({"report_numbers": ["SPP-1"], "score": 3, "cause": "고맙습니다"})
        self.assertEqual(start.call_args.args[2:], (3, "고맙습니다"))
        _, start = self._start({"report_numbers": ["SPP-1"], "score": 3})
        self.assertEqual(start.call_args.args[2:], (3, ""))

    def test_rating_start_rejects_too_long_cause(self):
        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as ctx:
            self._start({"report_numbers": ["SPP-1"], "score": 3, "cause": "가" * 1001})
        self.assertEqual(ctx.exception.status_code, 400)


class FixtureDbTests(unittest.TestCase):
    """합성 DB 로 목록(get_unrated_records)과 신고번호 조회가 같은 규칙을 쓰는지."""

    def setUp(self):
        from sqlalchemy import create_engine

        from core.utils import logger
        from scripts.dev import fixture_server

        logger.LoggerFactory.create_logger(mode="crawl")
        self._dir = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self._dir.name) / 'data.db'}")
        fixture_server.seed_engine(self.engine)

    def tearDown(self):
        self.engine.dispose()
        self._dir.cleanup()

    def test_unrated_list_matches_shared_rule_and_lookup_by_number(self):
        from core.database import database
        from services import report_query_service

        unrated = report_query_service.get_unrated_records(self.engine)
        numbers = [r["신고번호"] for r in unrated]
        self.assertTrue(numbers)
        rows = database.get_merged_records_by_report_numbers(self.engine, numbers)
        self.assertEqual(sorted(r["신고번호"] for r in rows), sorted(numbers))
        for row in rows:
            self.assertIsNone(rating_eligibility.ineligible_reason(row["만족도조사여부"], row["처리상태"]), row["신고번호"])
        # 거꾸로: 목록에서 빠진 신고는 모두 사유가 있다
        from sqlalchemy import select

        from core.database import models

        with self.engine.connect() as conn:
            every = [r[0] for r in conn.execute(select(models.title_table.c["신고번호"]))]
        others = database.get_merged_records_by_report_numbers(self.engine, [n for n in every if n not in numbers])
        self.assertTrue(others)
        for row in others:
            self.assertIsNotNone(rating_eligibility.ineligible_reason(row["만족도조사여부"], row["처리상태"]), row["신고번호"])

if __name__ == "__main__":
    unittest.main()
