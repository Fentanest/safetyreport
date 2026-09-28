"""Agency/region registry service — snapshot-backed display/stat helpers.

shared/agency-region-registry 스냅샷을 읽어 원문 기관명·코드를 현행 표시로 푼다.
원문 열을 덮어쓰지 않으며, 미확정 행은 기존 표시(normalize_police_agency)를
그대로 쓴다 — 호출자가 폴백한다. 통계 그룹 키 변경·서버↔모바일 parity 변경은
공식 전체자료 대조 뒤 별도 작업으로 한다(보고서 참조).
"""
from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

REGISTRY_ROOT = Path(__file__).resolve().parents[1] / "shared" / "agency-region-registry"
sys.path.insert(0, str(REGISTRY_ROOT / "resolvers"))

from resolve import Snapshot, display_agency  # noqa: E402


@lru_cache(maxsize=1)
def snapshot() -> Snapshot:
    return Snapshot.load(REGISTRY_ROOT)


def _text(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and value != value:  # NaN
        return None
    text = str(value).strip()
    return text or None


def resolve_display_agency(code, name, answered_at) -> tuple[str | None, str]:
    """(현행 표시명, resolution_status). 미확정이면 (원문 strip, 'unresolved')."""
    code = _text(code)
    name = _text(name)
    date = _text(answered_at)
    date = date[:10] if date else None
    if code is None and name is None:
        return None, "unresolved"
    from resolve import resolve_agency as _resolve

    resolution = _resolve(code, name, date, snapshot())
    return display_agency(name, resolution), str(resolution.get("resolution_status"))
