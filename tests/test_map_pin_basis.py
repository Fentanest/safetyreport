"""신고 지도 핀 기준(pin_basis) — 정본 벡터 `contracts/map-pin-basis-vectors.json` 검증.

- `apply_pin_basis` 함수 단위: 양 모드 effective 좌표가 벡터와 같음.
- fixture DB 로 `get_report_map_stats`/missing 2함수(양 모드): geocoded/missing 건수,
  점 집합(lat,lng,total), 좌표 없는 그룹 수·건수가 벡터와 같음.
- 웹·API 경로가 `pin_basis` 를 전달함(시그니처), 쿼리 없으면 기존 결과와 같음.
"""
import inspect
import json
import os
import tempfile
import unittest
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine

from core.utils import logger
from core.database import database, models
from services.stats import map as map_stats
from services import report_stats_service as stats

VECTORS = json.loads((Path(__file__).resolve().parent.parent / "contracts" / "map-pin-basis-vectors.json").read_text(encoding="utf-8"))


def _frame_from_rows(rows):
    """벡터 행 → `_load_map_records_frame` 뒤 프레임과 같은 모양(위도·유효좌표·주소키)."""
    from services.stats.common import _is_finite_number
    frame = pd.DataFrame(rows)
    frame["위도"] = pd.to_numeric(frame.get("위도"), errors="coerce")
    frame["경도"] = pd.to_numeric(frame.get("경도"), errors="coerce")
    frame["유효좌표"] = frame["위도"].apply(_is_finite_number) & frame["경도"].apply(_is_finite_number)
    frame["주소정규화"] = frame.get("주소정규화", "").fillna("").astype(str)
    frame["위반장소"] = frame.get("위반장소", "").fillna("").astype(str)
    frame["주소키"] = frame["주소정규화"].str.strip()
    frame.loc[frame["주소키"] == "", "주소키"] = frame["위반장소"].str.strip()
    return frame


def _effective_map(frame, basis):
    out = map_stats.apply_pin_basis(frame, basis)
    result = {}
    for _, row in out.iterrows():
        if row["유효좌표"]:
            result[row["ID"]] = [float(row["위도"]), float(row["경도"])]
        else:
            result[row["ID"]] = None
    return result


def _points_set(payload):
    return {(round(p["lat"], 6), round(p["lng"], 6), p["total"]) for p in payload["points"]}


def _expected_points_set(items):
    return {(round(p["lat"], 6), round(p["lng"], 6), p["total"]) for p in items}


class PinBasisVectorsTest(unittest.TestCase):
    def test_normalize_pin_basis(self):
        self.assertEqual(map_stats.normalize_pin_basis(None), "coords")
        self.assertEqual(map_stats.normalize_pin_basis(""), "coords")
        self.assertEqual(map_stats.normalize_pin_basis("coords"), "coords")
        self.assertEqual(map_stats.normalize_pin_basis("address"), "address")
        self.assertEqual(map_stats.normalize_pin_basis(" ADDRESS "), "address")
        self.assertEqual(map_stats.normalize_pin_basis("지오코드"), "coords")

    def test_apply_pin_basis_matches_vectors(self):
        frame = _frame_from_rows(VECTORS["rows"])
        for basis in ("coords", "address"):
            with self.subTest(basis=basis):
                expected = VECTORS["expected"][basis]
                self.assertEqual(_effective_map(frame, basis), expected["effective"])

    def test_apply_pin_basis_does_not_mutate_input(self):
        frame = _frame_from_rows(VECTORS["rows"])
        before = frame.copy()
        map_stats.apply_pin_basis(frame, "address")
        pd.testing.assert_frame_equal(frame, before)


class PinBasisServiceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        logger.LoggerFactory.create_logger(mode="crawl")
        fd, cls.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        cls.engine = create_engine(f"sqlite:///{cls.db_path}")
        database.upgrade_schema(cls.engine)
        with cls.engine.begin() as conn:
            for index, row in enumerate(VECTORS["rows"]):
                conn.execute(models.merge_traffic_table.insert().values(
                    ID=row["ID"], 상태="수용", 신고번호=f"SN-{row['ID']}", 신고명="핀기준검수",
                    신고일="2026-01-01", 처리상태="수용", 위반장소=row["위반장소"],
                    주소정규화=row["주소정규화"], 행정구역="", 위도=row["위도"], 경도=row["경도"],
                    처리기관="핀기준검수기관", 담당자="핀기준담당",
                    답변일="2026-01-02", 발생일자="2026-01-01", 발생시각="10:00",
                ))

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()
        os.remove(cls.db_path)

    def test_stats_match_vectors_both_modes(self):
        for basis in ("coords", "address"):
            with self.subTest(basis=basis):
                expected = VECTORS["expected"][basis]
                payload = stats.get_report_map_stats(self.engine, mode="raw", pin_basis=basis)
                meta = payload["meta"]
                self.assertEqual(meta["pin_basis"], basis)
                self.assertEqual(meta["total_reports"], len(VECTORS["rows"]))
                self.assertEqual(meta["geocoded_reports"], expected["geocoded_reports"])
                self.assertEqual(meta["missing_reports"], expected["missing_reports"])
                self.assertEqual(_points_set(payload), _expected_points_set(expected["points"]))
                summary = stats.get_report_map_missing_summary(self.engine, mode="raw", pin_basis=basis)
                self.assertEqual(summary, {"group_count": expected["missing_groups"]["group_count"],
                                           "report_count": expected["missing_groups"]["report_count"]})
                groups = stats.get_report_map_missing_groups(self.engine, mode="raw", pin_basis=basis)
                self.assertEqual(groups["meta"]["pin_basis"], basis)
                self.assertEqual(groups["meta"]["group_count"], expected["missing_groups"]["group_count"])
                self.assertEqual(groups["meta"]["report_count"], expected["missing_groups"]["report_count"])
                self.assertEqual(sum(g["report_count"] for g in groups["groups"]),
                                 expected["missing_groups"]["report_count"])

    def test_default_matches_coords(self):
        default_stats = stats.get_report_map_stats(self.engine, mode="raw")
        coords_stats = stats.get_report_map_stats(self.engine, mode="raw", pin_basis="coords")
        self.assertEqual(default_stats, coords_stats)
        self.assertEqual(default_stats["meta"]["pin_basis"], "coords")
        default_groups = stats.get_report_map_missing_groups(self.engine, mode="raw")
        coords_groups = stats.get_report_map_missing_groups(self.engine, mode="raw", pin_basis="coords")
        self.assertEqual(default_groups, coords_groups)
        self.assertEqual(stats.get_report_map_missing_summary(self.engine, mode="raw"),
                         stats.get_report_map_missing_summary(self.engine, mode="raw", pin_basis="coords"))
        # 알 수 없는 값도 coords 로 정규화
        junk = stats.get_report_map_stats(self.engine, mode="raw", pin_basis="geocode")
        self.assertEqual(junk, coords_stats)


class PinBasisRoutePassthroughTest(unittest.TestCase):
    def test_web_and_api_routes_accept_pin_basis(self):
        from web.routers import stats as web_stats
        from web.routers import api_route
        for func in (web_stats.view_report_map, web_stats.get_report_map_points,
                     web_stats.get_report_map_missing, api_route.get_stats_map,
                     api_route.get_stats_map_points, api_route.get_stats_map_missing):
            with self.subTest(route=func.__name__):
                self.assertIn("pin_basis", inspect.signature(func).parameters)

    def test_service_reexports_accept_pin_basis(self):
        from services import data_service
        for func in (stats.get_report_map_stats, stats.get_report_map_missing_summary,
                     stats.get_report_map_missing_groups, data_service.get_report_map_stats,
                     data_service.get_report_map_missing_groups):
            with self.subTest(service=func.__name__):
                self.assertIn("pin_basis", inspect.signature(func).parameters)


if __name__ == "__main__":
    unittest.main()
