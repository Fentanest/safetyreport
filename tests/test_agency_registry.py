"""Agency/region registry vectors — shared snapshot resolvers check (seed 2026-09-28.1).

shared/agency-region-registry/vectors/resolve_cases.json 의 14건을 정본 리더(resolve.py)로
확인한다. Dart/TS 포트는 각 레포의 같은 파일로 검증한다.
"""
import json
import sys
import unittest
from pathlib import Path

REGISTRY = Path(__file__).resolve().parents[1] / "shared" / "agency-region-registry"
sys.path.insert(0, str(REGISTRY / "resolvers"))

from resolve import Snapshot, display_agency, display_region, resolve_agency, resolve_region_gap  # noqa: E402


class RegistryVectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snap = Snapshot.load(REGISTRY)
        cls.cases = json.loads((REGISTRY / "vectors" / "resolve_cases.json").read_text(encoding="utf-8"))["cases"]
        cls.links = json.loads((REGISTRY / "data" / "agency_links.json").read_text(encoding="utf-8"))["links"]
        cls.events = json.loads((REGISTRY / "data" / "region_events.json").read_text(encoding="utf-8"))["events"]

    def test_case_count(self):
        self.assertEqual(len(self.cases), 14)

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
        self.assertEqual(manifest["registry_version"], "2026-09-28.1")
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
            {"처리기관": "광주광역시경찰청", "처리기관코드": "1812314", "답변일": "2026-09-01"},
            {"처리기관": "광주광역시경찰청", "처리기관코드": "1812314", "답변일": "2026-06-01"},
            {"처리기관": "서울특별시 중구청", "처리기관코드": None, "답변일": "2026-09-01"},
            {"처리기관": "서울특별시 강서경찰서 교통과", "처리기관코드": None, "답변일": "2026-09-01"},
        ])
        out = stats._apply_registry_agency_display(df)["처리기관"].tolist()
        # 확인된 1:1 개명: 답변일 이후면 현행명, 이전이면 당시명
        self.assertEqual(out[0], "광주경찰청")
        self.assertEqual(out[1], "광주광역시경찰청")
        # 미확정은 원문 유지, 기존 normalize 동작 유지(경찰서 뒤 절단)
        self.assertEqual(out[2], "서울특별시 중구청")
        self.assertEqual(out[3], "서울특별시 강서경찰서")

    def test_missing_columns_keep_legacy_output(self):
        import pandas as pd

        from services import report_stats_service as stats

        df = pd.DataFrame([{"처리기관": "서울특별시 강서경찰서 교통과"}])
        out = stats._apply_registry_agency_display(df)["처리기관"].tolist()
        self.assertEqual(out, ["서울특별시 강서경찰서"])


if __name__ == "__main__":
    unittest.main()
