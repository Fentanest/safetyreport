"""Sol R7 Opus 판정 P1~P7 서버 재현 — 실경로·실DB, skip·완화 없음.

각 시험은 수정 전 코드에서 실패한다.
"""
import json
import os
import shutil
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from sqlalchemy import select

import settings.settings as settings
from core.database import models
from core.database.engine import get_engine
from core.utils import logger, raw_detail
from services import db_optimize, maintenance_service
from services.community_capture import PARSER_VERSION
from services.community_store import CommunityStore

from tests.test_db_optimize import (
    OptimizeBase, _align_no_new_activity, _envelope, _insert_raw, _report_ids,
)


def _mobile_partial_meta(ids, hold_id):
    # 상대 레포(모바일) 형식 partial 메타 픽스처: 같은 키·같은 의미(통일 후 형식).
    return {
        "target": "mobile-parser-13",
        "cursor": "",
        "done": False,
        "total": len(ids),
        "stats": {"reused": 0, "refetched": 0, "changed": 0, "events": 0,
                  "forwarded": 0, "skipped": {}},
        "attempts": {},
        "holds": [hold_id],
        "round": 1,
        "next_retry_at": "2020-01-01T00:00:00.000Z",
    }


class P1MetaShapeTests(OptimizeBase):
    def test_partial_meta_shape_iso_round_holds(self):
        ids = _report_ids(self.engine)
        target = ids[0]
        _align_no_new_activity(self.engine)

        def fetch(report_id):
            if report_id == target:
                return {"error": "HTTP 500"}
            return _envelope(report_id)

        result = self._run(session_factory=lambda: object(), fetch_fn=fetch)
        self.assertFalse(result["finished"])
        meta = db_optimize.read_meta(self.engine)
        # 같은 키·같은 의미: holds 이름, attempts, round, next_retry_at ISO, 통계 키
        self.assertIn("holds", meta)
        self.assertNotIn("pending", meta)
        self.assertEqual(meta["holds"], [target])
        self.assertEqual(meta.get("attempts"), {})
        self.assertEqual(meta.get("round"), 1)
        nxt = meta.get("next_retry_at")
        self.assertIsInstance(nxt, str)
        # UTC ISO8601 파싱 가능
        datetime.fromisoformat(nxt.replace("Z", "+00:00"))
        for key in ("reused", "refetched", "changed", "events", "forwarded", "skipped"):
            self.assertIn(key, meta.get("stats", {}))

    def test_cross_mobile_partial_continues_holds_only(self):
        ids = _report_ids(self.engine)
        hold = ids[3]
        _align_no_new_activity(self.engine)
        db_optimize.write_meta(self.engine, _mobile_partial_meta(ids, hold))
        seen = []

        def fetch(report_id):
            seen.append(report_id)
            return _envelope(report_id)

        result = self._run(session_factory=lambda: object(), fetch_fn=fetch)
        self.assertTrue(result["finished"])
        # holds만 이어서 처리
        self.assertEqual(seen, [hold])

    def test_legacy_pending_fallback_continues_holds_only(self):
        ids = _report_ids(self.engine)
        hold = ids[2]
        _align_no_new_activity(self.engine)
        legacy = _mobile_partial_meta(ids, hold)
        legacy["pending"] = legacy.pop("holds")
        del legacy["next_retry_at"]
        db_optimize.write_meta(self.engine, legacy)
        seen = []

        def fetch(report_id):
            seen.append(report_id)
            return _envelope(report_id)

        result = self._run(session_factory=lambda: object(), fetch_fn=fetch)
        self.assertTrue(result["finished"])
        self.assertEqual(seen, [hold])


class P2BackoffTests(OptimizeBase):
    def test_login_rest_uses_exponential_backoff_and_wakes(self):
        # 로그인 쉼이 고정 간격이 아니라 지수 백오프(밑값→2배→최대 1시간)여야 한다.
        # 실제 경로: 403 연속 → 로그인 쉼 로그에 커지는 대기 초가 나온다.
        ids = _report_ids(self.engine)
        _align_no_new_activity(self.engine)

        def fetch(report_id):
            return {"error": "HTTP 403"}

        # _do_rest 내부 대기를 가로채지 않고 로그의 대기 초를 본다.
        result = self._run(session_factory=lambda: object(), fetch_fn=fetch,
                           interval=0, login_retry_seconds=1,
                           outage_retry_seconds=1, max_rest_episodes=3)
        self.assertFalse(result["finished"])
        log_text = "\n".join(db_optimize._state.get("log", []))
        # 지수 백오프면 1초·2초가 모두 나온다(고정 간격이면 2초 없음).
        self.assertIn("2초", log_text)


class P3PartialStateTests(OptimizeBase):
    def test_partial_state_not_counted_running_quiet_bar(self):
        ids = _report_ids(self.engine)
        target = ids[0]
        _align_no_new_activity(self.engine)

        def fetch(report_id):
            if report_id == target:
                return {"error": "HTTP 500"}
            return _envelope(report_id)

        result = self._run(session_factory=lambda: object(), fetch_fn=fetch)
        self.assertFalse(result["finished"])
        state = db_optimize.job_for_status()
        self.assertEqual(state["state"], "partial")
        self.assertEqual(state["stage"], "partial")
        self.assertFalse(db_optimize.is_running())
        data = maintenance_service.status(self.engine)
        self.assertFalse(data["active"])
        # 조용히: 보류 건수·다음 재시도 시각이 메시지에 있다.
        self.assertIn("보류 1건", state["message"])
        self.assertIn("재시도", state["message"])


class P4DrainTests(OptimizeBase):
    def test_partial_drains_deferred_full(self):
        from services import crawl_control

        ids = _report_ids(self.engine)
        target = ids[0]
        _align_no_new_activity(self.engine)
        db_optimize.defer_full_crawl(
            self.engine, {"crawl_mode": "full", "queue_filename": "queue.txt",
                          "header": "=== P4 ===", "broadcast_source": "web_start"})

        def fetch(report_id):
            if report_id == target:
                return {"error": "HTTP 500"}
            return _envelope(report_id)

        started = []
        with mock.patch.object(crawl_control, "start_crawl",
                               side_effect=lambda **k: started.append(k) or "log"):
            result = self._run(session_factory=lambda: object(), fetch_fn=fetch)
        self.assertFalse(result["finished"])
        self.assertEqual(len(started), 1)
        self.assertEqual(db_optimize.peek_deferred_fulls(self.engine), [])

    def test_outage_rest_drains_deferred_full(self):
        from services import crawl_control

        ids = _report_ids(self.engine)
        _align_no_new_activity(self.engine)
        db_optimize.defer_full_crawl(
            self.engine, {"crawl_mode": "full", "queue_filename": "queue.txt",
                          "header": "=== P4 outage ===", "broadcast_source": "web_start"})

        def fetch(report_id):
            return {"error": "HTTP 503"}

        started = []
        with mock.patch.object(crawl_control, "start_crawl",
                               side_effect=lambda **k: started.append(k) or "log"):
            result = self._run(session_factory=lambda: object(), fetch_fn=fetch,
                               outage_retry_seconds=0, max_rest_episodes=1)
        self.assertFalse(result["finished"])
        self.assertEqual(len(started), 1)


class P5NoNumberTests(OptimizeBase):
    def test_no_number_new_activity_not_saved_nor_queued(self):
        from services.crawl_manager import crawl_manager

        ids = _report_ids(self.engine)
        target = ids[0]
        _align_no_new_activity(self.engine)
        with self.engine.connect() as conn:
            row = conn.execute(select(models.report_table).where(
                models.report_table.c.ID == target)).mappings().first()
        self.assertTrue(row["신고번호"])
        # 신고번호 없는 행 + 새 활동 응답
        with self.engine.begin() as conn:
            conn.execute(models.report_table.update().where(
                models.report_table.c.ID == target).values(신고번호="", 상태="진행", 답변일=""))
        fresh = _envelope(target, status="수용", agency="서울특별시 강서경찰서 교통과",
                          amount_sentence="과태료 40,000원을 부과하였습니다.", c_now=10)

        def fetch(report_id):
            if report_id == target:
                return fresh
            return _envelope(report_id)

        pending_before = set(crawl_manager.pending_items())
        result = self._run(session_factory=lambda: object(), fetch_fn=fetch)
        self.assertTrue(result["finished"])
        # 저장하지 않는다: 원본·파서 열·파일
        with self.engine.connect() as conn:
            raw = conn.execute(select(models.report_detail_raw_table).where(
                models.report_detail_raw_table.c.ID == target)).first()
        self.assertIsNone(raw)
        self.assertFalse((Path(settings.raw_details_dir) / f"{target}.json").exists())
        with self.engine.connect() as conn:
            after = conn.execute(select(models.report_table).where(
                models.report_table.c.ID == target)).mappings().first()
        self.assertEqual(after["상태"], "진행")
        # 대기열에 넣지 않는다
        self.assertEqual(set(crawl_manager.pending_items()), pending_before)
        log_text = "\n".join(db_optimize._state.get("log", []))
        self.assertIn("새 답변 — 다음 일반 크롤에서 처리(신고번호 없음)", log_text)
        meta = db_optimize.read_meta(self.engine)
        self.assertTrue(meta.get("done"))


class P6ConflictTests(OptimizeBase):
    def test_save_conflict_goes_to_holds_without_attempts(self):
        ids = _report_ids(self.engine)
        target = ids[0]
        for rid in ids:
            _insert_raw(self.engine, rid, _envelope(rid))
        real_save = db_optimize._save_reparsed

        def hooked(engine, rec, report_id, **kwargs):
            out = None
            if report_id == target:
                # 스냅샷(기대값)을 읽은 뒤 다른 엔진이 저장한 것처럼 행을 바꾼다.
                with engine.begin() as conn:
                    conn.execute(models.report_table.update().where(
                        models.report_table.c.ID == report_id).values(답변일="2026-10-16"))
            return real_save(engine, rec, report_id, **kwargs)

        with mock.patch.object(db_optimize, "_save_reparsed", side_effect=hooked):
            result = self._run(**self._no_network())
        # 처리 완료로 세지 않고 holds로
        self.assertFalse(result["finished"])
        meta = db_optimize.read_meta(self.engine)
        self.assertFalse(meta.get("done"))
        self.assertIn(target, meta.get("holds", []))
        self.assertEqual(meta.get("attempts", {}), {})


class P7TokenReuseTests(OptimizeBase):
    def test_reuse_requires_same_username(self):
        import settings.settings as settings_mod
        from core.crawler import direct_login

        # 파일 계정과 현재 설정 계정이 다르면 재사용하지 않는다.
        other = {"access_token": "tok", "jsessionid": "j", "wmonid": "w",
                 "username": "__other_account__", "expires_at": 9999999999}
        direct_login.save_token(other)
        current = settings_mod.username
        try:
            settings_mod.username = "__current_account__"
            sess = db_optimize._reuse_last_crawl_session()
            self.assertIsNone(sess)
        finally:
            settings_mod.username = current
            try:
                direct_login.invalidate_token()
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main()
