"""Sol 독립검수 7건(Opus 판정) 재현 시험 — 실경로, 실DB, skip·완화 없음.

각 시험은 수정 전 코드에서 실패한다(확인 순서: git stash → 이 파일만 실행 → 실패 → 복원).
"""
import json
import os
import shutil
import threading
import unittest
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
    OptimizeBase,
    _align_no_new_activity,
    _envelope,
    _insert_raw,
    _journal_count,
    _report_ids,
)


class RestoreRaceTests(OptimizeBase):
    def test_restore_stops_optimize_without_writing_old_cursor(self):
        ids = _report_ids(self.engine)
        for rid in ids:
            _insert_raw(self.engine, rid, _envelope(rid))
        calls = []

        real_save = db_optimize._save_reparsed

        def hooked(*args, **kwargs):
            report_id = args[2] if len(args) >= 3 else kwargs.get("report_id", "")
            calls.append(report_id)
            out = real_save(*args, **kwargs)
            if len(calls) == 1:
                # 실제 복원 경로: 데이터셋 선회전(복원은 교체 직전 community.db 데이터셋을 돌린다).
                CommunityStore.open().rotate_dataset("restore_test")
            return out

        with mock.patch.object(db_optimize, "_save_reparsed", side_effect=hooked):
            result = self._run(**self._no_network())
        # 현재 신고(1건)는 끝내고 멈춘다. 복원 DB에 옛 커서·done을 쓰지 않는다.
        self.assertEqual(len(calls), 1)
        self.assertFalse(result["finished"])
        meta = db_optimize.read_meta(self.engine)
        # 옛 ID 목록 끝까지 갔다는 done 기록이 없어야 한다(메타를 그대로 두므로 None일 수도 있음).
        if meta is not None:
            self.assertFalse(meta.get("done"))
            self.assertNotEqual(meta.get("cursor"), ids[-1])


class VersionBumpTests(OptimizeBase):
    def test_old_target_discards_cursor_and_reprocesses_all(self):
        ids = _report_ids(self.engine)
        for rid in ids:
            _insert_raw(self.engine, rid, _envelope(rid))
        db_optimize.write_meta(self.engine, {"target": "pc-parser-1", "cursor": ids[-1],
                                              "done": True, "total": len(ids),
                                              "stats": {"reused": 0, "refetched": 0, "changed": 0,
                                                        "events": 0, "skipped": {}}})
        seen = []
        real = db_optimize._save_reparsed
        with mock.patch.object(db_optimize, "_save_reparsed",
                               side_effect=lambda *a, **k: (seen.append(a[2]), real(*a, **k))[1]):
            result = self._run(**self._no_network())
        self.assertEqual(seen, ids)
        self.assertTrue(result["finished"])
        meta = db_optimize.read_meta(self.engine)
        self.assertEqual(meta.get("target"), PARSER_VERSION)
        self.assertTrue(meta.get("done"))


class TransientRetryTests(OptimizeBase):
    def test_retryable_is_retried_then_done(self):
        ids = _report_ids(self.engine)
        target = ids[0]
        _align_no_new_activity(self.engine)
        calls = {rid: 0 for rid in ids}

        def fetch(report_id):
            calls[report_id] += 1
            if report_id == target and calls[report_id] <= 3:
                return {"error": "HTTP 500"}
            return _envelope(report_id)

        result = self._run(session_factory=lambda: object(), fetch_fn=fetch)
        self.assertTrue(result["finished"])
        # 첫 시도(3회 envelope 재시도) 뒤 같은 회차에서 다시 시도했으므로 3회 초과 호출이다.
        self.assertGreater(calls[target], 3)
        self.assertNotIn("fetch_failed", result["stats"].get("skipped", {}))
        meta = db_optimize.read_meta(self.engine)
        self.assertTrue(meta.get("done"))
        self.assertEqual(meta.get("attempts", {}), {})

    def test_three_transients_become_holds_and_partial(self):
        # 명세 7-1: 일시 실패 3회는 영구 완료가 아니라 보류 목록에 남고 상태 partial이다.
        ids = _report_ids(self.engine)
        target = ids[0]
        _align_no_new_activity(self.engine)

        def fetch(report_id):
            if report_id == target:
                return {"error": "HTTP 500"}
            return _envelope(report_id)

        result = self._run(session_factory=lambda: object(), fetch_fn=fetch)
        self.assertFalse(result["finished"])
        self.assertEqual(result["stats"]["skipped"].get(db_optimize.RETRY_EXCEEDED_REASON), 1)
        meta = db_optimize.read_meta(self.engine)
        self.assertFalse(meta.get("done"))
        self.assertEqual(meta.get("holds", []), [target])
        self.assertFalse(db_optimize.is_running())

    def test_save_failure_is_retried(self):
        ids = _report_ids(self.engine)
        for rid in ids:
            _insert_raw(self.engine, rid, _envelope(rid))
        target = ids[0]
        attempts = {"n": 0}
        real = db_optimize._save_reparsed

        def hooked(engine, rec, report_id, **kwargs):
            if report_id == target:
                attempts["n"] += 1
                if attempts["n"] == 1:
                    raise db_optimize._SaveTransient("boom")
            return real(engine, rec, report_id, **kwargs)

        with mock.patch.object(db_optimize, "_save_reparsed", side_effect=hooked):
            result = self._run(**self._no_network())
        self.assertTrue(result["finished"])
        self.assertEqual(attempts["n"], 2)
        meta = db_optimize.read_meta(self.engine)
        self.assertTrue(meta.get("done"))


class DeletedShareTests(OptimizeBase):
    def test_reparse_does_not_resurrect_deleted_observation(self):
        from services import community_capture as cap

        ids = _report_ids(self.engine)
        for rid in ids:
            _insert_raw(self.engine, rid, _envelope(rid))
        self._run(**self._no_network())
        before = _journal_count()
        self.assertGreater(before, 0)
        # 사용자가 공유 관측을 지운다(실제 삭제 경로).
        cap.on_contributions_deleted()
        # 삭제 뒤 원본이 바뀌면(답변 변경) 재파싱은 payload가 달라지지만 삭제 관측을 되살리면 안 된다.
        target = ids[0]
        with self.engine.begin() as conn:
            conn.execute(models.report_detail_raw_table.delete().where(
                models.report_detail_raw_table.c.ID == target))
        _insert_raw(self.engine, target, _envelope(target, seed="바뀜", status="불수용",
                                                    agency="경기도 부천원미경찰서",
                                                    amount_sentence="처분이 불가합니다.", c_now=14))
        db_optimize.write_meta(self.engine, {"target": PARSER_VERSION, "cursor": "",
                                              "done": False, "total": len(ids), "stats": {}})
        result = self._run(**self._no_network())
        self.assertTrue(result["finished"])
        self.assertEqual(_journal_count(), before)
        self.assertEqual(result["stats"].get("events", 0), 0)


class DeferredFullTests(OptimizeBase):
    def _entry(self, name):
        return {"crawl_mode": "full", "queue_filename": name,
                "header": f"=== {name} ===", "broadcast_source": "web_start"}

    def test_two_defers_merge_and_survive_until_start(self):
        db_optimize.defer_full_crawl(self.engine, self._entry("queue.txt"))
        n = db_optimize.defer_full_crawl(self.engine, self._entry("queue2.txt"))
        self.assertEqual(n, 1)
        items = db_optimize.peek_deferred_fulls(self.engine)
        self.assertEqual(len(items), 1)
        # 시작 전에는 실제 DB에 남는다.
        with self.engine.connect() as conn:
            value = conn.execute(select(models.sync_meta_table.c.value).where(
                models.sync_meta_table.c.key == db_optimize.DEFERRED_META_KEY)).scalar()
        self.assertEqual(len(json.loads(value)), 1)

    def test_after_finish_starts_once_and_clears(self):
        from services import crawl_control

        db_optimize.defer_full_crawl(self.engine, self._entry("queue.txt"))
        db_optimize.defer_full_crawl(self.engine, self._entry("queue2.txt"))
        started = []
        with mock.patch.object(crawl_control, "start_crawl",
                               side_effect=lambda **k: started.append(k) or "log"):
            db_optimize._after_finish(self.engine)
        self.assertEqual(len(started), 1)
        self.assertEqual(db_optimize.peek_deferred_fulls(self.engine), [])

    def test_running_crawl_keeps_deferred(self):
        from services import crawl_control
        from services.crawl_manager import crawl_manager

        db_optimize.defer_full_crawl(self.engine, self._entry("queue.txt"))

        def _busy():
            raise RuntimeError("크롤링이 이미 실행 중입니다.")

        with mock.patch.object(crawl_manager, "is_crawling", return_value=True):
            with mock.patch.object(crawl_control, "start_crawl", side_effect=_busy):
                db_optimize._after_finish(self.engine)
        # 유실 금지: 실행 중이면 지우지 않는다.
        self.assertEqual(len(db_optimize.peek_deferred_fulls(self.engine)), 1)

    def test_restart_with_done_starts_deferred(self):
        from services import crawl_control

        ids = _report_ids(self.engine)
        db_optimize.defer_full_crawl(self.engine, self._entry("queue.txt"))
        db_optimize.write_meta(self.engine, {"target": PARSER_VERSION, "cursor": ids[-1],
                                              "done": True, "total": len(ids), "stats": {}})
        started = []
        with mock.patch.object(crawl_control, "start_crawl",
                               side_effect=lambda **k: started.append(k) or "log"):
            self.assertIsNone(db_optimize.maybe_start(self.engine))
        self.assertEqual(len(started), 1)
        self.assertEqual(db_optimize.peek_deferred_fulls(self.engine), [])


class ReparseScopeTests(OptimizeBase):
    def test_concurrent_photo_update_survives_reparse(self):
        from core.storage import reports_repo

        rid = "90000001"
        with self.engine.connect() as conn:
            before = conn.execute(select(models.report_table).where(
                models.report_table.c.ID == rid)).mappings().first()
        self.assertIsNotNone(before)
        stale = dict(before)
        _insert_raw(self.engine, rid, _envelope(rid))

        new_photo = {"사진_첫촬영": "2026-09-20 10:00:00", "사진_끝촬영": "2026-09-20 10:00:05",
                     "사진_촬영수": 2}
        # 사진 보충이 끝난 현재값(실제 DB 갱신). GEO는 파서가 만드는 값이라 재파싱이
        # 원본에서 다시 계산해 쓴다 — 사진 열만 현재 값을 유지한다.
        with self.engine.begin() as conn:
            conn.execute(models.report_table.update().where(
                models.report_table.c.ID == rid).values(**new_photo))
        # 재파싱의 잠금 전 읽기가 오래된 값을 들고 있는 경합: 첫 _find_row는 옛 값을 돌린다.
        # GEO는 파싱 결과에서 다시 계산하므로 옛 값으로 되돌아가지 않고,
        # 사진 열은 UPDATE에 포함하지 않으므로(캡처 입력만 잠금 안 현재값) 새 값이 남는다.
        real_find = reports_repo._find_row
        calls = {"n": 0}

        def hooked(conn, record_id):
            calls["n"] += 1
            if record_id == rid and calls["n"] == 1:
                return dict(stale)
            return real_find(conn, record_id)

        from services.parser import parse_json_details
        from core.crawler import detail_pipeline

        with self.engine.connect() as conn:
            raw_row = conn.execute(select(models.report_detail_raw_table).where(
                models.report_detail_raw_table.c.ID == rid)).mappings().first()
        envelope = json.loads(raw_detail.verify(raw_row["raw_zlib"], raw_row["raw_sha256"]).decode("utf-8"))
        details = parse_json_details(envelope["result"])
        rec = detail_pipeline.build_detail_result(
            rid, details, progress_status=details.get("progress_status", ""),
            satisfaction_client=None,
            satisfaction_fetcher=lambda *a: (None, None),
            raw_envelope=None, raw_source=db_optimize.BACKFILL_SOURCE)
        rec = reports_repo.CrawledDetail.from_legacy_tuple(rec)
        with mock.patch.object(reports_repo, "_find_row", side_effect=hooked):
            reports_repo.reparse_one(self.engine, rec, trigger="reparse")
        with self.engine.connect() as conn:
            after = conn.execute(select(models.report_table).where(
                models.report_table.c.ID == rid)).mappings().first()
        from core.storage.reports_repo import comparable
        from services.geocode_service import official_geo_payload

        for column, value in new_photo.items():
            self.assertEqual(comparable(after[column]), comparable(value))
        # GEO는 원본에서 일반 저장과 같은 규칙으로 다시 계산된 값이다.
        expected_geo = official_geo_payload(
            rec.detail.get("위반장소") or "", rec.detail.get("위도"), rec.detail.get("경도"))
        for column in reports_repo.GEO_COLUMNS:
            self.assertEqual(comparable(after[column]), comparable(expected_geo[column]))


class ErrorReleaseTests(OptimizeBase):
    def test_error_releases_crawls_and_reports_error(self):
        from services import crawl_control
        from services.crawl_manager import crawl_manager

        ids = _report_ids(self.engine)
        db_optimize.defer_full_crawl(
            self.engine, {"crawl_mode": "full", "queue_filename": "queue.txt",
                          "header": "=== 테스트 전체 ===", "broadcast_source": "web_start"})
        crawl_manager.append_to_pending("SPP-2609-9000001")
        started = []
        with mock.patch.object(db_optimize, "process_all", side_effect=RuntimeError("boom")), \
                mock.patch.object(crawl_control, "start_crawl",
                                   side_effect=lambda **k: started.append(k) or "log"):
            db_optimize.maybe_start(self.engine, background=False, is_crawling=lambda: False,
                                    rebuild_pending=lambda: False, interval=0, wait_seconds=0,
                                    sleep=lambda s: None, stop=self.stop)
        state = db_optimize.job_for_status()
        self.assertEqual(state["state"], "error")
        # 크롤 보류 해제: 번호 대기열 재시도 요청 + 미뤄 둔 전체 크롤 시작.
        self.assertTrue(self.launch_mock.called)
        self.assertEqual(len(started), 1)
        data = maintenance_service.status(self.engine)
        self.assertFalse(data["active"])
        jobs = {j["key"]: j for j in data["jobs"]}
        self.assertEqual(jobs["db_optimize"]["state"], "error")
        # '작업 완료'로 보이지 않게 — 표시줄 JS는 error를 '오류'로 보인다.
        js = Path(settings.__file__).parent.parent / "web" / "static" / "ui" / "shell-polling.js"
        text = Path(js).read_text(encoding="utf-8")
        self.assertIn("오류", text)


if __name__ == "__main__":
    unittest.main()
