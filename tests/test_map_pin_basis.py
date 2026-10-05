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
from unittest import mock
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine

from core.utils import logger
from core.database import database, models
from services.stats import map as map_stats
from services import report_stats_service as stats

VECTORS = json.loads((Path(__file__).resolve().parent.parent / "contracts" / "map-pin-basis-vectors.json").read_text(encoding="utf-8"))
CLUSTER_VECTORS = json.loads((Path(__file__).resolve().parent.parent / "contracts" / "map-cluster-label-vectors.json").read_text(encoding="utf-8"))


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


class ClusterLabelVectorsTest(unittest.TestCase):
    """§6 후속 B: 묶음 점 이름은 `map-cluster-label-vectors.json` 규칙(description 정본)."""

    def test_cluster_cell_label_matches_vectors(self):
        for cell in CLUSTER_VECTORS["cells"]:
            with self.subTest(cell=cell["name"]):
                pairs = [(row.get("주소정규화"), row.get("위반장소")) for row in cell["rows"]]
                self.assertEqual(map_stats.cluster_cell_label(pairs), cell["expected"])

    def test_cluster_cell_label_does_not_mutate_input(self):
        pairs = [("서울 강서구 등촌동 101", "서울 강서구 등촌동 101 ")]
        before = list(pairs)
        map_stats.cluster_cell_label(pairs)
        self.assertEqual(pairs, before)


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
                self.assertEqual(meta["address_groups"], expected["address_groups"])
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

    def test_clustered_points_follow_label_vectors(self):
        # max_points 를 작게 해 공간 칸 묶음이 생기게 한다(양 모드).
        for basis in ("coords", "address"):
            with self.subTest(basis=basis):
                payload = stats.get_report_map_stats(self.engine, mode="raw", pin_basis=basis, max_points=2)
                self.assertTrue(payload["meta"]["clustered"])
                self.assertGreater(len(payload["points"]), 0)
                for point in payload["points"]:
                    self.assertTrue(point.get("cluster"))
                    self.assertNotEqual(point["region"], "영역 집계")
                    self.assertNotEqual(point["address"], "이 영역의 신고")
                    count = point["address_count"]
                    self.assertIsInstance(count, int)
                    if count == 0:
                        self.assertEqual(point["address"], "")
                        self.assertEqual(point["region"], "주소 정보 없음")
                    elif count == 1:
                        self.assertEqual(point["region"], point["address"])
                    else:
                        self.assertEqual(point["region"], f"{point['address']} 외 {count - 1}곳")

    def test_non_clustered_points_unchanged(self):
        payload = stats.get_report_map_stats(self.engine, mode="raw", pin_basis="coords")
        self.assertFalse(payload["meta"]["clustered"])
        for point in payload["points"]:
            self.assertNotIn("cluster", point)
            self.assertNotIn("address_count", point)

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


class PinBasisBoundaryTest(unittest.TestCase):
    """명세 §7 경계(모바일과 같아야 함): 유니코드 공백 strip, 실수 좌표 숫자 비교."""

    @classmethod
    def setUpClass(cls):
        logger.LoggerFactory.create_logger(mode="crawl")

    def _engine(self, rows):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        engine = create_engine(f"sqlite:///{path}")
        self.addCleanup(os.remove, path)
        self.addCleanup(engine.dispose)
        database.upgrade_schema(engine)
        with engine.begin() as conn:
            for rid, normalized, place, lat, lng in rows:
                conn.execute(models.merge_traffic_table.insert().values(
                    ID=rid, 상태="수용", 신고번호=f"SN-{rid}", 신고명="경계", 신고일="2026-01-01",
                    처리상태="수용", 위반장소=place, 주소정규화=normalized, 행정구역="", 위도=lat, 경도=lng,
                    처리기관="경계기관", 답변일="2026-01-02", 발생일자="2026-01-01", 발생시각="10:00"))
        return engine

    def test_tab_newline_nbsp_addresses_share_one_key(self):
        engine = self._engine([("T1", "\t서울 중구\t", "\t서울 중구\t", 37.5, 127.0),
                               ("T2", "서울 중구", "서울 중구", None, None),
                               ("T3", "\n서울 중구\u00a0", "서울 중구", None, None)])
        meta = stats.get_report_map_stats(engine, mode="raw", pin_basis="address")["meta"]
        self.assertEqual((meta["geocoded_reports"], meta["missing_reports"], meta["address_groups"]), (3, 0, 1))
        self.assertEqual(stats.get_report_map_missing_summary(engine, mode="raw", pin_basis="address")["report_count"], 0)

    def test_strip_set_is_python_default(self):
        # 정본 집합(공용 벡터)이 서버 str.strip() 기본 집합과 같다. 모바일은 이 목록을 그대로 쓴다.
        expected = [c for c in range(0x110000) if chr(c).isspace()]
        self.assertEqual(VECTORS["strip_code_points"], expected)

    def test_key_cases_through_server_loader(self):
        from services.stats.reads import _load_map_records_frame
        cases = VECTORS["key_cases"]
        engine = self._engine([(f"K{i}", c["주소정규화"], c["위반장소"], 37.5, 127.0) for i, c in enumerate(cases)])
        _, _, frame = _load_map_records_frame(engine, year=None, category="all", mode="raw", column_names=None, filters=None)
        got = dict(zip(frame["ID"], frame["주소키"]))
        for i, case in enumerate(cases):
            with self.subTest(case=i):
                self.assertEqual(got[f"K{i}"], case["key"])

    def test_close_floats_are_distinct_address_groups(self):
        engine = self._engine([("F1", "서울 중구", "서울 중구", 37.5, 127.0),
                               ("F2", "서울 중구", "서울 중구", 37.50000000000001, 127.0)])
        self.assertEqual(stats.get_report_map_stats(engine, mode="raw")["meta"]["address_groups"], 2)


class PinBasisRoutePassthroughTest(unittest.TestCase):
    """웹·API 경로를 실제로 불러 pin_basis 가 서비스까지 전달되는지 본다(Sol 1차 L2).
    서비스 호출에서 pin_basis 인자를 빼면 이 시험이 실패해야 한다."""

    def setUp(self):
        self.calls = []

        def record(name, result):
            def fake(engine, **kwargs):
                self.calls.append((name, kwargs))
                return result(kwargs)
            return fake

        stats_result = lambda kw: {"points": [], "meta": {"pin_basis": map_stats.normalize_pin_basis(kw.get("pin_basis"))}}
        groups_result = lambda kw: {"groups": [], "meta": {"pin_basis": map_stats.normalize_pin_basis(kw.get("pin_basis"))}}
        from services import data_service
        self.patches = [
            mock.patch.object(stats, "get_report_map_stats", record("stats", stats_result)),
            mock.patch.object(stats, "get_report_map_missing_summary",
                              record("summary", lambda kw: {"group_count": 0, "report_count": 0})),
            mock.patch.object(data_service, "get_report_map_stats", record("stats", stats_result)),
            mock.patch.object(data_service, "get_report_map_missing_groups", record("groups", groups_result)),
        ]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def _request(path, query=""):
        from starlette.requests import Request
        return Request({"type": "http", "method": "GET", "path": path, "query_string": query.encode(),
                        "headers": [], "session": {}})

    def _assert_forwarded(self, *names):
        self.assertEqual(sorted(name for name, _ in self.calls), sorted(names))
        for name, kwargs in self.calls:
            self.assertEqual(kwargs.get("pin_basis"), "address", name)
        self.calls.clear()

    def test_web_routes_forward_pin_basis(self):
        from web.routers import stats as web_stats
        with mock.patch.object(web_stats.templates, "TemplateResponse", side_effect=lambda req, name, ctx: ctx), \
                mock.patch.object(web_stats.csrf, "get_or_create_token", return_value="t"):
            context = web_stats.view_report_map(self._request("/stats/map", "pin_basis=address"),
                                                year=None, category="all", dedupe=None, pin_basis="address")
        self.assertEqual(context["pin_basis"], "address")
        self._assert_forwarded("stats", "summary")
        web_stats.get_report_map_points(self._request("/stats/map/points", "pin_basis=address"),
                                        year=None, category="all", dedupe=None, pin_basis="address")
        self._assert_forwarded("stats")
        web_stats.get_report_map_missing(self._request("/stats/map/missing", "pin_basis=address"),
                                         year=None, category="all", dedupe=None, pin_basis="address")
        self._assert_forwarded("groups")

    def test_api_routes_forward_pin_basis(self):
        from web.routers import api_route
        body = api_route.get_stats_map("key", year=None, category="all", pin_basis="address")
        self.assertEqual(body["data"]["meta"]["pin_basis"], "address")
        self._assert_forwarded("stats")
        body = api_route.get_stats_map_points("key", year=None, category="all", dedupe=None, max_points=1200,
                                              bounds=None, zoom=7, law=None, pin_basis="address")
        self.assertEqual(body["data"]["meta"]["pin_basis"], "address")
        self._assert_forwarded("stats")
        body = api_route.get_stats_map_missing("key", year=None, category="all", pin_basis="address")
        self.assertEqual(body["data"]["meta"]["pin_basis"], "address")
        self._assert_forwarded("groups")

    def test_routes_without_query_keep_coords(self):
        from web.routers import api_route
        api_route.get_stats_map_points("key", year=None, category="all", dedupe=None, max_points=1200,
                                       bounds=None, zoom=7, law=None)
        self.assertEqual(map_stats.normalize_pin_basis(self.calls[0][1].get("pin_basis")), "coords")


if __name__ == "__main__":
    unittest.main()
