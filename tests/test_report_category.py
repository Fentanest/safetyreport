import hashlib
import json
import unittest
from pathlib import Path

from services import report_category as rc

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "report-categories.json"


class ReportCategoryContractTest(unittest.TestCase):
    """services/report_category.py 의 표가 contracts/report-categories.json 과 같은지, 벡터 결과가 같은지."""

    @classmethod
    def setUpClass(cls):
        cls.contract = json.loads(CONTRACT.read_text(encoding="utf-8"))

    def test_categories_match(self):
        keys = tuple(c["key"] for c in self.contract["categories"])
        self.assertEqual(keys, rc.CATEGORIES)
        self.assertEqual({c["key"]: c["label"] for c in self.contract["categories"]}, rc.CATEGORY_LABELS)

    def test_groups_match(self):
        self.assertEqual({g["cd"]: g["category"] for g in self.contract["groups"]}, rc.GROUP_CATEGORY)

    def test_codes_match(self):
        expected = {(c["cd"], c["type"]): (c["name"], c["category"]) for c in self.contract["codes"]}
        self.assertEqual(expected, rc.CODES)
        self.assertEqual(len(expected), 43)

    def test_menus_and_aliases_match(self):
        ev = self.contract["entry_value"]
        menus = {m["menu"]: (tuple(m["cds"]), m["category"]) for m in ev["menus"]}
        self.assertEqual(menus, rc.ENTRY_MENUS)
        aliases = {(tuple(a["cds"]), a["sub"]): tuple(a["code"].split("-")) for a in ev["aliases"]}
        self.assertEqual(aliases, rc.ENTRY_ALIASES)

    def test_vectors(self):
        for v in self.contract["vectors"]:
            with self.subTest(v["name"]):
                got = rc.resolve(v["ssnpc_cd"], v["ssnpc_type"], v["entry_value"])
                self.assertEqual(got.code, v["expect"]["code"])
                self.assertEqual(got.category, v["expect"]["category"])

    def test_code_name(self):
        self.assertEqual(rc.code_name("IP-05"), "어린이보호구역")
        self.assertEqual(rc.code_name("XX-01"), "")
        self.assertEqual(rc.code_name(None), "")

    def test_mobile_copy_is_byte_identical_when_present(self):
        mobile = ROOT.parent / "safetyreport-mobile" / "contracts" / "report-categories.json"
        if not mobile.exists():
            self.skipTest("모바일 레포 없음")
        self.assertEqual(hashlib.sha256(mobile.read_bytes()).hexdigest(), hashlib.sha256(CONTRACT.read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
