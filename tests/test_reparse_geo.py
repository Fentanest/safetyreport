"""reparse_one 좌표 규칙: GEO는 파싱 결과에서 일반 저장과 같은 함수·같은 규칙으로 계산해 씀.
PHOTO만 읽지도 덮지도 않음(현재값 유지, 캡처 입력은 잠금 안 현재값).

수정 전 코드(reparse가 GEO/PHOTO를 쓰지 않음)에서 geo 2건은 실패, photo 1건은 통과해야 한다.
"""
import json
import unittest
from unittest import mock

from sqlalchemy import select

from core.database import models
from core.utils import raw_detail
from services import db_optimize
from services.geocode_service import official_geo_payload

from tests.test_db_optimize import (
    OptimizeBase,
    _envelope,
    _insert_raw,
    _report_ids,
)


def _envelope_with_geo(report_id, *, lat=37.1234, lng=127.5678,
                       address="서울특별시 강서구 마곡동 99", **kwargs):
    env = _envelope(report_id, **kwargs)
    env["result"]["RN_ADRES"] = address
    env["result"]["C_A_W"] = str(lat)
    env["result"]["C_A_E"] = str(lng)
    return env


def _rec_for(engine, report_id, envelope):
    from services.parser import parse_json_details
    from core.crawler import detail_pipeline
    from core.storage import reports_repo

    details = parse_json_details(envelope["result"])
    tup = detail_pipeline.build_detail_result(
        report_id, details, progress_status=details.get("progress_status", ""),
        satisfaction_client=None,
        satisfaction_fetcher=lambda *a: (None, None),
        raw_envelope=None, raw_source=db_optimize.BACKFILL_SOURCE)
    return reports_repo.CrawledDetail.from_legacy_tuple(tup), details


def _geo_of(engine, rid):
    with engine.connect() as conn:
        row = conn.execute(select(models.report_table).where(
            models.report_table.c.ID == rid)).mappings().first()
    return {c: row[c] for c in ("주소정규화", "행정구역", "위도", "경도", "지오코딩상태")}


def _photo_of(engine, rid):
    with engine.connect() as conn:
        row = conn.execute(select(models.report_table).where(
            models.report_table.c.ID == rid)).mappings().first()
    return {c: row[c] for c in ("사진_첫촬영", "사진_끝촬영", "사진_촬영수")}


class ReparseGeoTests(OptimizeBase):
    def test_reparse_updates_geo_like_normal_save(self):
        """원본 공식 좌표 != 기존 행 좌표 → 재파싱 뒤 위도·경도·지오코딩상태가 일반 저장과 같은 값으로 바뀜."""
        from core.storage import reports_repo
        from core.storage.reports_repo import comparable

        rid = "90000001"
        env = _envelope_with_geo(rid)
        _insert_raw(self.engine, rid, env)
        rec, _ = _rec_for(self.engine, rid, env)
        expected = official_geo_payload(
            rec.detail.get("위반장소"), rec.detail.get("위도"), rec.detail.get("경도"))

        reports_repo.reparse_one(self.engine, rec, trigger="reparse")

        after = _geo_of(self.engine, rid)
        for column in ("주소정규화", "행정구역", "위도", "경도", "지오코딩상태"):
            self.assertEqual(comparable(after[column]), comparable(expected[column]),
                             f"GEO {column} 불일치")
        # 핵심: 위도·경도·지오코딩상태가 바뀜 (기존 fixture 행은 좌표 없음)
        self.assertEqual(comparable(after["위도"]), comparable(37.1234))
        self.assertEqual(comparable(after["경도"]), comparable(127.5678))
        self.assertEqual(after["지오코딩상태"], "ok")

    def test_reparse_keeps_photo_columns(self):
        """사진 보충이 사진 열을 갱신한 뒤 재파싱해도 사진 열 유지."""
        from core.storage import reports_repo
        from core.storage.reports_repo import comparable

        rid = "90000001"
        env = _envelope_with_geo(rid)
        _insert_raw(self.engine, rid, env)
        new_photo = {"사진_첫촬영": "2026-09-20 10:00:00",
                     "사진_끝촬영": "2026-09-20 10:00:05",
                     "사진_촬영수": 2}
        with self.engine.begin() as conn:
            conn.execute(models.report_table.update().where(
                models.report_table.c.ID == rid).values(**new_photo))
        rec, _ = _rec_for(self.engine, rid, env)
        reports_repo.reparse_one(self.engine, rec, trigger="reparse")

        after = _photo_of(self.engine, rid)
        for column, value in new_photo.items():
            self.assertEqual(comparable(after[column]), comparable(value))

    def test_save_one_and_reparse_produce_same_geo(self):
        """일반 저장과 재파싱이 같은 원본에서 같은 GEO 값을 냄."""
        from core.storage import reports_repo
        from core.storage.reports_repo import comparable

        rid_save = "90000002"
        rid_reparse = "90000003"
        env = _envelope_with_geo(rid_save)
        # 일반 저장 경로
        rec_save, _ = _rec_for(self.engine, rid_save, env)
        # _save_one은 기존 fixture 행을 갱신 (신규 INSERT 아님)
        reports_repo._save_one(self.engine, rec_save)
        geo_save = _geo_of(self.engine, rid_save)

        # 재파싱 경로 (같은 원본 내용, 다른 ID)
        env2 = _envelope_with_geo(rid_reparse)
        _insert_raw(self.engine, rid_reparse, env2)
        rec_reparse, _ = _rec_for(self.engine, rid_reparse, env2)
        reports_repo.reparse_one(self.engine, rec_reparse, trigger="reparse")
        geo_reparse = _geo_of(self.engine, rid_reparse)

        for column in ("주소정규화", "행정구역", "위도", "경도", "지오코딩상태"):
            self.assertEqual(comparable(geo_save[column]), comparable(geo_reparse[column]),
                             f"일반 저장 vs 재파싱 GEO {column} 불일치")


if __name__ == "__main__":
    unittest.main()
