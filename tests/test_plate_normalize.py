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


class MotorcycleDuplicateTest(unittest.TestCase):
    def test_grouping_preserves_raw_and_combines_regional_variants(self):
        import tempfile
        from sqlalchemy import create_engine
        from core.database import database, models
        from core.utils import logger
        from services.report_query_service import get_duplicate_records
        logger.LoggerFactory.create_logger(mode="crawl")
        with tempfile.TemporaryDirectory() as directory:
            engine = create_engine(f"sqlite:///{directory}/reports.db")
            try:
                database.upgrade_schema(engine)
                plates = ["경기군포다2671", "경기도 군포시 다 2671", "경기-군포-다2671", "서울강남다0025"]
                with engine.begin() as conn:
                    for i, plate in enumerate(plates):
                        conn.execute(models.report_table.insert().values(
                            ID=f"M{i}", 신고번호=f"M-{i}", 차량번호=plate,
                            category="traffic", 처리상태="수용", 상태="답변완료", 종결여부="Y"))
                database.rebuild_effective(engine)
                rows = get_duplicate_records(engine)
                self.assertEqual({r["ID"] for r in rows}, {"M0", "M1", "M2"})
                self.assertEqual({r["차량번호"] for r in rows}, set(plates[:3]))
                self.assertTrue(all(r["total_count"] == 3 for r in rows))
            finally:
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
