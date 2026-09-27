"""기존 지도 저장 열과 호환되는 공식 신고 좌표 헬퍼.

좌표는 안전신문고 상세 응답에서 읽는다. 주소를 외부 서비스로 변환하지 않는다.
"""
from __future__ import annotations

import math
import re


def normalize_address(value: str | None) -> str:
    text = str(value or "").strip()
    return re.sub(r"\s+", " ", text) if text else ""


def official_geo_payload(address: str | None, lat, lng) -> dict:
    normalized = normalize_address(address)
    try:
        lat, lng = float(lat), float(lng)
        valid = math.isfinite(lat) and math.isfinite(lng) and 32 <= lat <= 39.5 and 124 <= lng <= 132
    except (TypeError, ValueError):
        valid = False
    return {
        "주소정규화": normalized,
        "행정구역": "",
        "위도": lat if valid else None,
        "경도": lng if valid else None,
        "지오코딩상태": "ok" if valid else "not_found" if normalized else "",
    }


def idle_progress() -> dict:
    """구 API의 응답 모양을 유지한다. 주소 변환 작업은 실행하지 않는다."""
    return {
        "state": "idle", "running": False, "total": 0, "processed": 0,
        "updated": 0, "not_found": 0, "remaining_missing": 0,
        "progress_pct": 0.0, "error_message": "", "started_at": 0,
        "finished_at": 0, "heartbeat_at": 0, "lease_owner": "",
        "has_saved_coordinates": False,
    }
