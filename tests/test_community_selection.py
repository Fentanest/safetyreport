"""증분 선정 — vectors/list_refetch.json 13건 + override 무시 (T3b).

규칙 정본: contracts/community-ingest/vectors/list_refetch.json.
closed/supplement_open 은 detail 표 원본만 쓴다(사용자 override 무시).
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sqlalchemy import create_engine

import settings.settings as app_settings
from core.database import database, models
from core.utils import logger

VECTORS = Path(__file__).resolve().parents[1] / "contracts" / "community-ingest" / "vectors" / "list_refetch.json"


def _should(case):
    return database.should_refetch_list_item(
        in_personal_detail=case["in_personal_detail"],
        list_label=case.get("list_label"),
        detail_status_label=case.get("detail_status_label"),
        closed=case.get("closed"),
        supplement_open=case.get("supplement_open"),
        in_capture_retry=bool(case.get("in_capture_retry")),
        rebuild_failed_permanent=bool(case.get("rebuild_failed_permanent")),
        failed_list_label=case.get("failed_list_label"))


class ListRefetchVectorTest(unittest.TestCase):
    def test_all_vector_cases(self):
        vectors = json.loads(VECTORS.read_text(encoding="utf-8"))
        self.assertEqual(vectors["contract"], "list-refetch-v1")
        self.assertEqual(len(vectors["cases"]), 13)
        for case in vectors["cases"]:
            with self.subTest(name=case["name"]):
                self.assertEqual(_should(case), case["refetch"], case["name"])

    def test_override_closed_is_ignored_a07(self):
        vectors = json.loads(VECTORS.read_text(encoding="utf-8"))
        case = next(c for c in vectors["cases"] if c["name"] == "override_closed_is_ignored")
        # 사용자 override 가 Y 여도 detail 원본(closed=N)으로만 판정 → 재조회.
        self.assertEqual(_should(case), True)
        flipped = dict(case, closed="Y")
        self.assertEqual(_should(flipped), False)

    def test_null_labels_never_compared_directly(self):
        self.assertFalse(database._labels_differ(None, None))
        self.assertTrue(database._labels_differ(None, "답변완료"))
        self.assertTrue(database._labels_differ("답변완료", None))
        self.assertFalse(database._labels_differ("답변완료", "답변완료"))


class SelectionIntegrationTest(unittest.TestCase):
    """개인 DB + community.db 보조자료를 합쳐 get_pending_detail_ids 에 반영한다."""

    def setUp(self):
        logger.LoggerFactory.create_logger(mode="crawl")
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.engine = create_engine(f"sqlite:///{self.db_path}")
        database.upgrade_schema(self.engine)
        # title(ID, 상태) — 목록 수집 반영 뒤.
        titles = [
            ("n1", "진행"),        # 신규(detail 없음) → 재조회
            ("o1", "진행"),        # 미종결 → 재조회
            ("c1", "답변완료"),    # 종결·라벨 동일 → 제외
            ("c2", "진행"),        # 종결 Y 지만 목록 라벨 변경 → 재조회
            ("c3", "답변완료"),    # 종결·영구 실패·같은 라벨 → 제외
            ("c4", "취하"),        # 종결·영구 실패지만 라벨 변경 → 재조회
            ("c5", "답변완료"),    # 종결·라벨 동일 + capture 재시도 → 재조회
            ("c6", "답변완료"),    # 종결·detail_status 없음 → 1회 재조회
            ("c7", "답변완료"),    # 종결·라벨 동일 + 보완 미응답 → 재조회
            ("c8", "진행"),        # detail 종결 N + override Y → 재조회(override 무시)
        ]
        details = {
            # ID: (table, 종결여부, 보완_미응답)
            "o1": ("traffic", "N", "N"),
            "c1": ("traffic", "Y", "N"),
            "c2": ("parking", "Y", "N"),
            "c3": ("other", "Y", "N"),
            "c4": ("traffic", "Y", "N"),
            "c5": ("traffic", "Y", "N"),
            "c6": ("traffic", "Y", "N"),
            "c7": ("parking", "Y", "Y"),
            "c8": ("traffic", "N", "N"),
        }
        tables = {"traffic": models.detail_traffic_table, "parking": models.detail_parking_table,
                  "other": models.detail_other_table}
        with self.engine.begin() as conn:
            for rid, label in titles:
                conn.execute(models.title_table.insert().values(
                    ID=rid, 상태=label, 신고번호=f"SPP-{rid}", 신고명="t", 신고일="2026-09-01"))
            for rid, (cat, closed, supp) in details.items():
                conn.execute(tables[cat].insert().values(ID=rid, 종결여부=closed, 보완_미응답=supp))
            # 사용자 override: c8 종결여부=Y 로 바꿨다고 주장해도 선정은 무시한다.
            conn.execute(models.report_override_table.insert().values(
                ID="c8", column_name="종결여부", value="Y", updated_at=1))

        self._status = {"o1": "진행", "c1": "답변완료", "c2": "답변완료",
                        "c3": None, "c4": None, "c5": "답변완료",
                        "c6": None, "c7": "답변완료", "c8": "진행"}
        self._permanent = {"c3": "답변완료", "c4": "답변완료"}
        self._retry = {"c5"}
        p1 = mock.patch.object(database, "_community_detail_status_labels",
                               lambda: dict(self._status))
        p2 = mock.patch.object(database, "_community_rebuild_permanent_labels",
                               lambda: dict(self._permanent))
        p3 = mock.patch.object(database, "_community_capture_retry_ids",
                               lambda: set(self._retry))
        p1.start(); p2.start(); p3.start()
        self.addCleanup(p1.stop); self.addCleanup(p2.stop); self.addCleanup(p3.stop)

    def tearDown(self):
        self.engine.dispose()
        try:
            os.remove(self.db_path)
        except OSError:
            pass

    def test_union_of_base_and_refetch(self):
        ids = set(database.get_pending_detail_ids(self.engine, force=False))
        self.assertEqual(ids, {"n1", "o1", "c2", "c4", "c5", "c6", "c7", "c8"})

    def test_force_path_unchanged(self):
        ids = set(database.get_pending_detail_ids(self.engine, force=True))
        self.assertEqual(ids, {"n1", "o1", "c1", "c2", "c3", "c4", "c5", "c6", "c7", "c8"})


if __name__ == "__main__":
    unittest.main()
