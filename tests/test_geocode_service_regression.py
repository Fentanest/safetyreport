"""공식 신고 좌표를 기존 DB 열에 저장하는 호환 규칙."""
import unittest

from services import geocode_service


class OfficialCoordinatesTest(unittest.TestCase):
    def test_valid_pair_preserves_double_precision(self):
        payload = geocode_service.official_geo_payload(
            " 서울특별시  중구 세종대로 1 ", "37.560123456789", "126.830123456789"
        )
        self.assertEqual(payload, {
            "주소정규화": "서울특별시 중구 세종대로 1",
            "행정구역": "", "위도": 37.560123456789,
            "경도": 126.830123456789, "지오코딩상태": "ok",
        })

    def test_invalid_or_half_pair_does_not_retain_old_coordinates(self):
        for lat, lng in ((None, "126.8"), ("37.5", None), ("nan", "126.8"),
                         ("126.8", "37.5"), ("", "126.8")):
            with self.subTest(lat=lat, lng=lng):
                payload = geocode_service.official_geo_payload("서울 중구", lat, lng)
                self.assertEqual((payload["위도"], payload["경도"], payload["지오코딩상태"]),
                                 (None, None, "not_found"))

    def test_progress_endpoint_has_stable_idle_shape(self):
        state = geocode_service.idle_progress()
        self.assertEqual((state["state"], state["running"], state["remaining_missing"]),
                         ("idle", False, 0))
        self.assertFalse(hasattr(geocode_service, "get_kakao_rest_api_key"))


if __name__ == "__main__":
    unittest.main()
