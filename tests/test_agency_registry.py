"""Agency/region registry vectors — shared snapshot resolvers check (2026-09-29.3).

shared/agency-region-registry/vectors/resolve_cases.json 의 36건을 정본 리더(resolve.py)로
확인한다. Dart/TS 포트는 각 레포의 같은 파일로 검증한다.
"""
import json
import os
import sys
import unittest
from pathlib import Path

REGISTRY = Path(__file__).resolve().parents[1] / "shared" / "agency-region-registry"
sys.path.insert(0, str(REGISTRY / "resolvers"))

from resolve import Snapshot, display_agency, display_region, resolve_agency, resolve_region_gap  # noqa: E402


class RegistryVectorTests(unittest.TestCase):
    def test_cycle_branch_cannot_forward_through_live_branch(self):
        from scripts.agency_registry.build_official_index import build_derived

        rows = {
            "A": {"code": "A", "name": "A", "alive": False, "prev": "B"},
            "B": {"code": "B", "name": "B", "alive": False, "prev": "A"},
            "C": {"code": "C", "name": "C", "alive": True, "prev": "A"},
        }
        derived = build_derived(rows)
        self.assertNotIn("A", derived["forward"])
        self.assertNotIn("B", derived["forward"])

    @classmethod
    def setUpClass(cls):
        cls.snap = Snapshot.load(REGISTRY)
        cls.cases = json.loads((REGISTRY / "vectors" / "resolve_cases.json").read_text(encoding="utf-8"))["cases"]
        cls.links = json.loads((REGISTRY / "data" / "agency_links.json").read_text(encoding="utf-8"))["links"]
        cls.events = json.loads((REGISTRY / "data" / "region_events.json").read_text(encoding="utf-8"))["events"]

    def test_case_count(self):
        self.assertEqual(len(self.cases), 37)

    def test_all_vectors(self):
        for case in self.cases:
            with self.subTest(case=case["name"]):
                if case["kind"] == "agency":
                    got = resolve_agency(case["input"]["code"], case["input"]["name"],
                                         case["input"]["answered_at"], self.snap)
                else:
                    got = resolve_region_gap(case["input"]["code"], case["input"]["date"], self.snap)
                for key, want in case["expected"].items():
                    self.assertEqual(got.get(key), want, f"{case['name']} · {key}")

    def test_display_never_invents_gu_prefix(self):
        unresolved = resolve_agency("9999999", "어딘가구청", "2026-09-01", self.snap)
        self.assertEqual(display_agency("어딘가구청", unresolved), "어딘가구청")
        region = resolve_region_gap("4159100000", "2026-09-01", self.snap)
        self.assertEqual(display_region("경기도 화성시", region), "경기도 화성시")

    def test_display_uses_historical_prefix_only_for_known_nodes(self):
        region = resolve_region_gap("2811000000", "2026-09-01", self.snap)
        self.assertEqual(display_region("인천광역시 중구", region), "(구)인천광역시 중구")

    def test_manifest_matches_snapshot_files(self):
        import hashlib

        manifest = json.loads((REGISTRY / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["registry_version"], "2026-09-29.3")
        self.assertEqual(manifest["schema_version"], 2)
        for rel, digest in manifest["files"].items():
            target = REGISTRY / rel
            if target.is_file():
                self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), digest, rel)


class RegistryDisplayWiringTests(unittest.TestCase):
    """통계 표시 연결: 확인된 승계만 현행명으로, 나머지는 기존 출력 그대로."""

    def test_resolved_rows_show_current_name(self):
        import pandas as pd

        from services import report_stats_service as stats

        df = pd.DataFrame([
            {"처리기관": "경찰청 광주광역시경찰청 광주동부경찰서", "처리기관코드": "1812314", "답변일": "2026-09-01"},
            {"처리기관": "경찰청 광주광역시경찰청 광주동부경찰서", "처리기관코드": "1812314", "답변일": "2026-06-01"},
            {"처리기관": "경찰청 광주경찰청 광주동부경찰서", "처리기관코드": "1815198", "답변일": "2026-09-01"},
            {"처리기관": "서울특별시 중구청", "처리기관코드": None, "답변일": "2026-09-01"},
            {"처리기관": "서울특별시 강서경찰서 교통과", "처리기관코드": None, "답변일": "2026-09-01"},
        ])
        out = stats._apply_registry_agency_display(df)["처리기관"].tolist()
        # 확인된 1:1 개명: 현행 표시(registry as_of 기준)는 답변일과 무관하게 현행명
        # (REVIEW2 중간-2: 과거 답변이 과거명으로 남던 문제 수정).
        # 승계 후 코드(1815198)로 들어와도 같은 현행명(REVIEW3 높음-2).
        self.assertEqual(out[0], "광주경찰청 광주동부경찰서")
        self.assertEqual(out[1], "광주경찰청 광주동부경찰서")
        self.assertEqual(out[2], "광주경찰청 광주동부경찰서")
        # 미확정은 원문 유지
        self.assertEqual(out[3], "서울특별시 중구청")
        self.assertEqual(out[4], "서울특별시 강서경찰서 교통과")

    def test_historical_identity_still_available_via_answered_at(self):
        # 답변일은 당시 식별용으로만 쓴다: 정본 리더에 답변일을 직접 주면
        # 승계 전 이름이 확인된다. 표시 경로는 현행명을 쓴다(위 테스트).
        from resolve import resolve_agency

        snap = Snapshot.load(REGISTRY)
        got = resolve_agency("1812314", "경찰청 광주광역시경찰청 광주동부경찰서", "2026-06-01", snap)
        self.assertEqual(got["institution_id"], "ag-gwangju-police-hq")
        # 답변일 이전 링크까지만 적용된 당시 표시도 스냅샷 표시 규칙을 따른다
        # (2026-09-29: 맨 앞 '경찰청 ' 제거).
        self.assertEqual(got["current_agency_name"], "광주광역시경찰청 광주동부경찰서")
        self.assertEqual(got["resolution_status"], "resolved_as_of_date")

    def test_missing_columns_keep_legacy_output(self):
        import pandas as pd

        from services import report_stats_service as stats

        df = pd.DataFrame([{"처리기관": "서울특별시 강서경찰서 교통과"}])
        out = stats._apply_registry_agency_display(df)["처리기관"].tolist()
        self.assertEqual(out, ["서울특별시 강서경찰서 교통과"])


class RegistryStatsWiringTests(unittest.TestCase):
    """M1: 통계 조회가 처리기관코드를 읽어 현행명으로 묶는다(공개 함수 경유)."""

    def test_stats_columns_carry_agency_code(self):
        from services import report_stats_service as stats

        self.assertIn("처리기관코드", stats._STATS_COLUMNS)
        self.assertIn("처리기관코드", stats._MAP_COLUMNS)

    def test_agency_stats_groups_old_code_under_current_name(self):
        import tempfile
        import os

        from sqlalchemy import create_engine

        import settings.settings as app_settings
        from core.database import models
        from services import report_stats_service as stats

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        engine = create_engine(f"sqlite:///{path}")
        try:
            with engine.begin() as conn:
                models.merge_traffic_table.create(conn)
                models.merge_parking_table.create(conn)
                models.merge_other_table.create(conn)
                models.entry_value_table.create(conn)
                rows = [
                    # 옛 코드·옛 이름·승계 전 답변일 + 새 코드·새 이름: 한 기관으로 묶여야 한다.
                    {"ID": "c1", "신고번호": "SPP-2609-000001", "신고명": "신호위반",
                     "신고일": "2026-05-01 10:00", "답변일": "2026-06-01",
                     "처리기관": "경찰청 광주광역시경찰청 광주동부경찰서", "처리기관코드": "1812314",
                     "담당자": "김담당", "처리상태": "수용", "범칙금_과태료": "과태료: 50000원"},
                    {"ID": "c2", "신고번호": "SPP-2609-000002", "신고명": "신호위반",
                     "신고일": "2026-08-01 10:00", "답변일": "2026-09-01",
                     "처리기관": "경찰청 광주경찰청 광주동부경찰서", "처리기관코드": "1815198",
                     "담당자": "이담당", "처리상태": "수용", "범칙금_과태료": "과태료: 50000원"},
                ]
                for row in rows:
                    conn.execute(models.merge_traffic_table.insert().values(**row))
            got = stats.get_agency_stats(engine, {}, mode="raw")
            by_agency = {r["agency"]: r for r in got["traffic"]["by_agency"]}
            self.assertEqual(set(by_agency), {"광주경찰청 광주동부경찰서"})
            self.assertEqual(by_agency["광주경찰청 광주동부경찰서"]["total"], 2)
            exact = stats.get_agency_stats(engine, {
                "agency": "광주경찰청 광주동부경찰서", "agencyExact": True,
            }, mode="raw")
            self.assertEqual(exact["traffic"]["by_agency"][0]["total"], 2)
        finally:
            engine.dispose()
            os.remove(path)

    def test_agency_stats_groups_new_code_first_order(self):
        # REVIEW3 높음-2: 새 코드 행이 먼저 들어와도 같은 현행 기관 1행으로 묶인다.
        import tempfile
        import os

        from sqlalchemy import create_engine

        import settings.settings as app_settings
        from core.database import models
        from services import report_stats_service as stats

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        engine = create_engine(f"sqlite:///{path}")
        try:
            with engine.begin() as conn:
                models.merge_traffic_table.create(conn)
                models.merge_parking_table.create(conn)
                models.merge_other_table.create(conn)
                models.entry_value_table.create(conn)
                rows = [
                    {"ID": "c2", "신고번호": "SPP-2609-000002", "신고명": "신호위반",
                     "신고일": "2026-08-01 10:00", "답변일": "2026-09-01",
                     "처리기관": "경찰청 광주경찰청 광주동부경찰서", "처리기관코드": "1815198",
                     "담당자": "이담당", "처리상태": "수용", "범칙금_과태료": "과태료: 50000원"},
                    {"ID": "c1", "신고번호": "SPP-2609-000001", "신고명": "신호위반",
                     "신고일": "2026-05-01 10:00", "답변일": "2026-06-01",
                     "처리기관": "경찰청 광주광역시경찰청 광주동부경찰서", "처리기관코드": "1812314",
                     "담당자": "김담당", "처리상태": "수용", "범칙금_과태료": "과태료: 50000원"},
                ]
                for row in rows:
                    conn.execute(models.merge_traffic_table.insert().values(**row))
            got = stats.get_agency_stats(engine, {}, mode="raw")
            by_agency = {r["agency"]: r for r in got["traffic"]["by_agency"]}
            self.assertEqual(set(by_agency), {"광주경찰청 광주동부경찰서"})
            self.assertEqual(by_agency["광주경찰청 광주동부경찰서"]["total"], 2)
        finally:
            engine.dispose()
            os.remove(path)

    def _engine_with_rows(self, rows):
        import tempfile
        import os

        from sqlalchemy import create_engine

        import settings.settings as app_settings
        from core.database import models
        from services import report_stats_service as stats

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        engine = create_engine(f"sqlite:///{path}")
        with engine.begin() as conn:
            models.merge_traffic_table.create(conn)
            models.merge_parking_table.create(conn)
            models.merge_other_table.create(conn)
            models.entry_value_table.create(conn)
            for row in rows:
                conn.execute(models.merge_traffic_table.insert().values(**row))
        return engine, path, app_settings, stats

    def _row(self, rid, code, agency, person="김담당"):
        return {"ID": rid, "신고번호": f"SPP-2609-{rid}", "신고명": "신호위반",
                "신고일": "2026-08-01 10:00", "답변일": "2026-09-01",
                "처리기관": agency, "처리기관코드": code,
                "담당자": person, "처리상태": "수용", "범칙금_과태료": "과태료: 50000원"}

    def test_agency_stats_groups_by_stat_key_dept_rolls_up(self):
        """하위부서 코드는 경계 기관 키로 묶인다. 담당자도 기관 키와 함께 묶인다."""
        engine, path, app_settings, stats = self._engine_with_rows([
            self._row("d1", "1336812", "경찰청 충청북도경찰청 청주흥덕경찰서 교통과", "김담당"),
            self._row("d2", "1336464", "경찰청 충청북도경찰청 청주흥덕경찰서", "이담당"),
        ])
        try:
            got = stats.get_agency_stats(engine, {}, mode="raw")
            by_agency = got["traffic"]["by_agency"]
            self.assertEqual(len(by_agency), 1)
            self.assertEqual(by_agency[0]["agency"], "충청북도경찰청 청주흥덕경찰서")
            self.assertEqual(by_agency[0]["agency_key"], "inst:ag-c1324595")
            self.assertEqual(by_agency[0]["total"], 2)
            by_person = {(r["agency_key"], r["person"]) for r in got["traffic"]["by_person"]}
            self.assertEqual(by_person, {("inst:ag-c1324595", "김담당"), ("inst:ag-c1324595", "이담당")})
        finally:
            engine.dispose()
            os.remove(path)

    def test_agency_stats_splits_same_display_different_keys(self):
        """같은 표시·다른 코드는 다른 행으로 갈라진다(원문 보존, 재크롤링 없음)."""
        engine, path, app_settings, stats = self._engine_with_rows([
            self._row("s1", "9999991", "어딘가구청"),
            self._row("s2", "9999992", "어딘가구청 교통과"),
        ])
        try:
            got = stats.get_agency_stats(engine, {}, mode="raw")
            by_agency = got["traffic"]["by_agency"]
            # normalize가 '어딘가구청'으로 합치던 표시가 코드별로 갈라진다.
            self.assertEqual(len(by_agency), 2)
            keys = {r["agency_key"] for r in by_agency}
            self.assertEqual(keys, {"src:9999991:어딘가구청", "src:9999992:어딘가구청 교통과"})
            self.assertEqual(sorted(r["total"] for r in by_agency), [1, 1])
        finally:
            engine.dispose()
            os.remove(path)

    def test_agency_stats_historical_branch_keeps_gu_row(self):
        """1:다 분기 코드는 (구) 별도 행으로 보존된다."""
        engine, path, app_settings, stats = self._engine_with_rows([
            self._row("h1", "1270379", "법무부 대구지방교정청 부산교도소 서무과"),
            self._row("h2", "1815198", "경찰청 광주경찰청 광주동부경찰서"),
        ])
        try:
            got = stats.get_agency_stats(engine, {}, mode="raw")
            by_agency = {r["agency_key"]: r for r in got["traffic"]["by_agency"]}
            self.assertIn("src:1270379:법무부 대구지방교정청 부산교도소 서무과", by_agency)
            self.assertEqual(
                by_agency["src:1270379:법무부 대구지방교정청 부산교도소 서무과"]["agency"],
                "(구)법무부 대구지방교정청 부산교도소 서무과")
            self.assertIn("inst:ag-gwangju-police-hq", by_agency)
        finally:
            engine.dispose()
            os.remove(path)

    def test_agency_stats_groups_abolished_dept_codes(self):
        """2026-09-29.3: 폐지 부서 코드는 답변 당시 소속 집계기관으로 묶인다
        (재크롤링 없이 파생 재계산 — 운영 86건 미확정 결함 대응)."""
        engine, path, app_settings, stats = self._engine_with_rows([
            {"ID": "a1", "신고번호": "SPP-2306-a1", "신고명": "신호위반",
             "신고일": "2023-06-01 10:00", "답변일": "2023-06-02",
             "처리기관": "경찰청 서울특별시경찰청 서울강서경찰서 교통과", "처리기관코드": "1810341",
             "담당자": "김담당", "처리상태": "수용", "범칙금_과태료": "과태료: 50000원"},
            {"ID": "a2", "신고번호": "SPP-2501-a2", "신고명": "신호위반",
             "신고일": "2025-01-01 10:00", "답변일": "2025-01-02",
             "처리기관": "경찰청 경기도남부경찰청 김포경찰서 교통과", "처리기관코드": "1814146",
             "담당자": "이담당", "처리상태": "수용", "범칙금_과태료": "과태료: 50000원"},
            {"ID": "a3", "신고번호": "SPP-2501-a3", "신고명": "불법주정차",
             "신고일": "2025-01-01 10:00", "답변일": "2025-01-02",
             "처리기관": "전라남도 여수시 교통도로국 주차차량과", "처리기관코드": "4810475",
             "담당자": "박담당", "처리상태": "수용", "범칙금_과태료": "과태료: 40000원"},
            self._row("a4", "4060425", "경기도 파주시 안전건설교통국 도시경관과"),
            self._row("a5", "4060000", "경기도 파주시"),
            self._row("a6", "3000188", "서울특별시 종로구 행정국 총무과"),
            self._row("a7", "3000000", "서울특별시 종로구"),
        ])
        try:
            got = stats.get_agency_stats(engine, {}, mode="raw")
            by_agency = {r["agency_key"]: r for r in got["traffic"]["by_agency"]}
            # 이전기관코드가 있는 폐지 부서도 경찰서 단위로 묶인다.
            self.assertIn("inst:ag-c1321068", by_agency)
            self.assertEqual(by_agency["inst:ag-c1321068"]["agency"], "서울특별시경찰청 서울강서경찰서")
            self.assertIn("inst:ag-c1811029", by_agency)
            self.assertEqual(by_agency["inst:ag-c1811029"]["agency"], "경기도남부경찰청 김포경찰서")
            # 집계기관 자체가 폐지된 경우(여수시)도 당시 소속으로 묶인다.
            self.assertIn("inst:ag-c4810000", by_agency)
            self.assertEqual(by_agency["inst:ag-c4810000"]["agency"], "전라남도 여수시")
            self.assertEqual(by_agency["inst:ag-c4060000"]["total"], 2)
            self.assertEqual(by_agency["inst:ag-c4060000"]["agency"], "경기도 파주시")
            self.assertEqual(by_agency["inst:ag-c3000000"]["total"], 2)
            self.assertEqual(by_agency["inst:ag-c3000000"]["agency"], "서울특별시 종로구")
            keys = set(by_agency)
            self.assertFalse({k for k in keys if k.startswith("src:")}, keys)
        finally:
            engine.dispose()
            os.remove(path)


if __name__ == "__main__":
    unittest.main()
