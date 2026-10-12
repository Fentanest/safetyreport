"""번호판 정규화(services/plate_normalize.py) — 세 레포 공유 벡터(tests/fixtures/plate_normalize_vectors.json)로
검증한다. 같은 파일을 모바일(test/fixtures/…)·지도(tests/fixtures/…) 레포도 바이트 그대로 가진다."""
import json
import pathlib
import unittest

from services import plate_normalize

VECTORS = json.loads((pathlib.Path(__file__).parent / "fixtures" / "plate_normalize_vectors.json").read_text(encoding="utf-8"))


class PlateNormalizeVectorTest(unittest.TestCase):
    def test_vectors(self):
        for case in VECTORS["cases"]:
            with self.subTest(name=case["name"]):
                self.assertEqual(plate_normalize.normalize_plate(case["raw"]), case["expected"])


if __name__ == "__main__":
    unittest.main()
