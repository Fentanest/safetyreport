"""Astra 교차 확인(배포 전) 서버 결함 2건 재현 — 실제 작업자·실DB, skip·완화 없음.

1) 바퀴 도중(커서가 남은) 메타에 보류가 있어도 커서 뒤 미방문 신고를 버리지 않는다.
2) partial 로 끝난 작업자는 재시도 시각 전에 다음 회차를 바로 돌리지 않고 타이머만 건다.
"""
import time

from services import db_optimize
from services.community_capture import PARSER_VERSION

from tests.test_db_optimize import OptimizeBase, _align_no_new_activity, _envelope, _report_ids


class ResumeWithCursorKeepsTailTests(OptimizeBase):
    def test_mid_pass_holds_do_not_drop_unvisited_tail(self):
        ids = _report_ids(self.engine)
        self.assertGreaterEqual(len(ids), 3)
        _align_no_new_activity(self.engine)
        # 첫 신고에서 저장 직전 충돌로 보류가 생긴 뒤(커서=첫 신고) 서버가 멈춘 상태.
        db_optimize.write_meta(self.engine, {
            "target": PARSER_VERSION, "cursor": ids[0], "done": False, "total": len(ids),
            "stats": {}, "attempts": {}, "holds": [ids[0]], "round": 0})
        seen = []

        def fetch(report_id):
            seen.append(report_id)
            return _envelope(report_id)

        result = self._run(session_factory=lambda: object(), fetch_fn=fetch)
        # 커서 뒤 신고를 모두 방문해야 한다(보류만 처리하고 끝내면 안 됨).
        self.assertEqual(seen, ids[1:])
        # 이어받은 보류가 남았으므로 완료가 아니라 partial 이다.
        self.assertFalse(result["finished"])
        meta = db_optimize.read_meta(self.engine)
        self.assertFalse(meta.get("done"))
        self.assertIn(ids[0], meta.get("holds") or [])


class PartialWorkerRespectsBackoffTests(OptimizeBase):
    def test_worker_finally_schedules_timer_instead_of_immediate_round(self):
        ids = _report_ids(self.engine)
        target = ids[0]
        _align_no_new_activity(self.engine)
        calls = {"target": 0}

        def fetch(report_id):
            if report_id == target:
                calls["target"] += 1
                return {"error": "HTTP 500"}
            return _envelope(report_id)

        # 실제 백그라운드 작업자(모듈 _stop)로 돌린다 — 작업자 종료 재판단 경로를 탄다.
        thread = db_optimize.maybe_start(
            self.engine, is_crawling=lambda: False, rebuild_pending=lambda: False,
            interval=0, wait_seconds=0, login_retry_seconds=0.01, sleep=lambda s: None,
            session_factory=lambda: object(), fetch_fn=fetch)
        self.assertIsNotNone(thread)
        thread.join(timeout=30)
        after_first = calls["target"]
        self.assertGreater(after_first, 0)
        time.sleep(0.5)  # 종료 재판단이 새 작업자를 띄웠다면 여기서 이미 다시 조회한다.
        meta = db_optimize.read_meta(self.engine)
        self.assertFalse(meta.get("done"))
        self.assertEqual(meta.get("holds"), [target])
        # 한 회차만 돌고, 재시도 시각 전에는 다음 회차를 돌리지 않는다(조회 횟수 그대로).
        self.assertEqual(calls["target"], after_first)
        with db_optimize._life_lock:
            live = db_optimize._thread
        self.assertTrue(live is None or not live.is_alive())
        self.assertIsNotNone(db_optimize._retry_timer)
        self.assertTrue(db_optimize._retry_timer.is_alive())
