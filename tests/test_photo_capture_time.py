"""주정차 사진 촬영 시각 수집(services/photo_capture_time.py)과 저장 경로(detail_to_sql, db_backup) 회귀 테스트."""
import os
import sqlite3
import struct
import tempfile
import unittest
from unittest import mock

import pandas as pd
from sqlalchemy import create_engine, select

from core.database import database, models
from core.utils import logger
from services import photo_capture_time


def _jpeg_with_exif(value: str, *, endian: str = ">", original: bool = True) -> bytes:
    """IFD0(-> ExifIFD) 에 DateTimeOriginal 또는 IFD0 DateTime 을 넣은 최소 JPEG."""
    marker = b"MM" if endian == ">" else b"II"
    text = value.encode("ascii") + b"\x00"  # 20 bytes
    if original:
        # IFD0 @8: 1 entry (ExifIFD pointer) -> ExifIFD @26: 1 entry (0x9003) -> data @44
        ifd0 = struct.pack(endian + "H", 1) + struct.pack(endian + "HHII", 0x8769, 4, 1, 26) + struct.pack(endian + "I", 0)
        exif = struct.pack(endian + "H", 1) + struct.pack(endian + "HHII", 0x9003, 2, len(text), 44) + struct.pack(endian + "I", 0)
        tiff = marker + struct.pack(endian + "HI", 42, 8) + ifd0 + exif + text
    else:
        ifd0 = struct.pack(endian + "H", 1) + struct.pack(endian + "HHII", 0x0132, 2, len(text), 26) + struct.pack(endian + "I", 0)
        tiff = marker + struct.pack(endian + "HI", 42, 8) + ifd0 + text
    app1 = b"Exif\x00\x00" + tiff
    return b"\xff\xd8" + b"\xff\xe1" + struct.pack(">H", len(app1) + 2) + app1 + b"\xff\xda" + b"\x00" * 16


class ExifParseTest(unittest.TestCase):
    def test_big_and_little_endian_original(self):
        for endian in (">", "<"):
            with self.subTest(endian=endian):
                data = _jpeg_with_exif("2026:09:22 13:19:43", endian=endian)
                self.assertEqual(photo_capture_time.parse_exif_datetime(data), "2026-09-22 13:19:43")

    def test_falls_back_to_ifd0_datetime(self):
        data = _jpeg_with_exif("2026:09:22 01:05:00", original=False)
        self.assertEqual(photo_capture_time.parse_exif_datetime(data), "2026-09-22 01:05:00")

    def test_no_exif_or_truncated_or_not_jpeg(self):
        self.assertIsNone(photo_capture_time.parse_exif_datetime(b"\xff\xd8\xff\xda\x00\x00"))
        self.assertIsNone(photo_capture_time.parse_exif_datetime(_jpeg_with_exif("2026:09:22 13:19:43")[:30]))
        self.assertIsNone(photo_capture_time.parse_exif_datetime(b"\x89PNG\r\n"))


class CollectTest(unittest.TestCase):
    def test_first_last_and_count(self):
        times = {"https://a/1.jpg": "2026-09-22 13:20:53", "https://a/2.jpg": "2026-09-22 13:19:43", "https://a/3.jpg": None}
        result = photo_capture_time.collect("https://a/1.jpg\nhttps://a/2.jpg\nhttps://a/3.jpg", fetch=times.get)
        self.assertEqual(result, {"사진_첫촬영": "2026-09-22 13:19:43", "사진_끝촬영": "2026-09-22 13:20:53", "사진_촬영수": 2})

    def test_no_urls_means_not_attempted(self):
        self.assertIsNone(photo_capture_time.collect("6개월 초과"))
        self.assertIsNone(photo_capture_time.collect(""))

    def test_network_error_returns_none_for_retry(self):
        def boom(url):
            raise OSError("timeout")
        self.assertIsNone(photo_capture_time.collect("https://a/1.jpg", fetch=boom))

    def test_no_exif_anywhere_records_zero(self):
        self.assertEqual(photo_capture_time.collect("https://a/1.jpg", fetch=lambda url: None),
                         {"사진_첫촬영": None, "사진_끝촬영": None, "사진_촬영수": 0})

    def test_fixture_mode_blocks_download(self):
        from core.utils.runtime_mode import ExternalSideEffectBlocked, FIXTURE_ENV
        with mock.patch.dict(os.environ, {FIXTURE_ENV: "1"}), mock.patch.object(photo_capture_time.requests, "get") as get:
            with self.assertRaises(ExternalSideEffectBlocked):
                photo_capture_time.fetch_capture_time("https://www.safetyreport.go.kr/fileDown/singo/x.jpg")
            get.assert_not_called()


class DetailToSqlPhotoTest(unittest.TestCase):
    def setUp(self):
        logger.LoggerFactory.create_logger(mode="crawl")
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.engine = create_engine(f"sqlite:///{self.db_path}")
        database.upgrade_schema(self.engine)
        with self.engine.begin() as conn:
            conn.execute(models.title_table.insert().values(ID="P1", 상태="수용", 신고번호="SPP-P1", 신고명="불법주정차신고", 신고일="2026-09-22"))

    def tearDown(self):
        self.engine.dispose()
        os.remove(self.db_path)

    def _frame(self, photos="https://www.safetyreport.go.kr/fileDown/singo/1.jpg\nhttps://www.safetyreport.go.kr/fileDown/singo/2.jpg"):
        return pd.DataFrame([{"ID": "P1", "처리상태": "수용", "범칙금_과태료": "과태료", "위반장소": "", "종결여부": "Y", "첨부사진": photos}])

    def _save(self, frame):
        return database.detail_to_sql([(frame, "parking", "불법주정차신고-인도")], self.engine)

    def _row(self):
        with self.engine.connect() as conn:
            return dict(conn.execute(select(models.detail_parking_table).where(models.detail_parking_table.c.ID == "P1")).first()._mapping)

    def test_collects_once_and_carries_over_without_touching_synced_at(self):
        times = iter(["2026-09-22 13:19:43", "2026-09-22 15:30:00"])
        with mock.patch.object(photo_capture_time, "fetch_capture_time", side_effect=lambda url: next(times)), \
                mock.patch("services.geocode_service.prepare_geo_payload", return_value={}):
            self._save(self._frame())
        first = self._row()
        self.assertEqual((first["사진_첫촬영"], first["사진_끝촬영"], first["사진_촬영수"]), ("2026-09-22 13:19:43", "2026-09-22 15:30:00", 2))

        # 재크롤링: 새 프레임에는 사진 시각이 없고, 다시 받지 않으며(값 이어받음) 변경으로 보지 않는다.
        with mock.patch.object(photo_capture_time, "fetch_capture_time", side_effect=AssertionError("refetch")), \
                mock.patch("services.geocode_service.prepare_geo_payload", return_value={}):
            changed = self._save(self._frame())
        second = self._row()
        self.assertEqual(changed, [])
        self.assertEqual(second["synced_at"], first["synced_at"])
        self.assertEqual((second["사진_첫촬영"], second["사진_끝촬영"], second["사진_촬영수"]), (first["사진_첫촬영"], first["사진_끝촬영"], 2))

    def test_non_parking_is_not_fetched(self):
        with mock.patch.object(photo_capture_time, "fetch_capture_time", side_effect=AssertionError("should not fetch")), \
                mock.patch("services.geocode_service.prepare_geo_payload", return_value={}):
            database.detail_to_sql([(self._frame(), "traffic", "자동차·교통위반-신호위반")], self.engine)

    def test_merge_carries_photo_columns(self):
        with mock.patch.object(photo_capture_time, "fetch_capture_time", return_value="2026-09-22 01:00:00"), \
                mock.patch("services.geocode_service.prepare_geo_payload", return_value={}):
            self._save(self._frame())
        database.merge_final(self.engine)
        with self.engine.connect() as conn:
            merged = dict(conn.execute(select(models.merge_parking_table)).first()._mapping)
        self.assertEqual((merged["사진_첫촬영"], merged["사진_촬영수"]), ("2026-09-22 01:00:00", 2))


class UpgradeSchemaPhotoColumnsTest(unittest.TestCase):
    def test_old_db_is_not_altered_and_the_reset_db_has_photo_columns(self):
        """2026-09-26 초기화 크롤링 릴리스: 이전 DB 에 열을 더하지 않는다(업데이트 비활성). 비운 뒤 새로 만든 표에 사진 열이 있다."""
        logger.LoggerFactory.create_logger(mode="crawl")
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "old.db")
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE mysafetydetail_parking (ID TEXT PRIMARY KEY, 처리상태 TEXT)")
        con.commit(); con.close()
        engine = create_engine(f"sqlite:///{path}")
        try:
            with self.assertRaises(database.LegacyDatabase):
                database.upgrade_schema(engine)
            with engine.connect() as conn:
                cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(mysafetydetail_parking)")}
            self.assertEqual(cols, {"ID", "처리상태"})
            database.reset_legacy_database(engine, os.path.join(tmp, "backups"))
            with engine.connect() as conn:
                cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(mysafetydetail_parking)")}
            self.assertTrue({"사진_첫촬영", "사진_끝촬영", "사진_촬영수"} <= cols)
        finally:
            engine.dispose()
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
