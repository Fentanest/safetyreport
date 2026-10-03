"""Agency/region registry service — snapshot-backed display/stat helpers.

shared/agency-region-registry 스냅샷을 읽어 원문 기관명·코드를 현행 표시로 푼다.
원문 열을 덮어쓰지 않으며, 미확정 행은 원문 표시를 그대로 쓴다.
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


def resolve_display_agency(code, name, answered_at=None) -> tuple[str | None, str]:
    """(현행 표시명, resolution_status). 미확정이면 (원문 strip, 'unresolved').

    현행 표시는 registry as_of_date 기준(resolve_current_agency)으로 계산한다.
    answered_at(답변일)은 과거 식별용으로만 쓰고 현행 표시에는 쓰지 않는다
    (검수 REVIEW2 중간-2: 과거 답변이 과거명으로 남던 문제).
    """
    code = _text(code)
    name = _text(name)
    if code is None and name is None:
        return None, "unresolved"
    from resolve import resolve_current_agency as _resolve_current

    resolution = _resolve_current(code, name, snapshot())
    return display_agency(name, resolution), str(resolution.get("resolution_status"))


def resolve_stats_agency(code, name) -> tuple[str, str]:
    """통계·목록·지도에 동일한 (표시명, 집계 키)를 제공한다. 원문은 변경하지 않는다."""
    from resolve import resolve_current_agency

    code_text = _text(code)
    raw = _text(name) or ""
    resolution = resolve_current_agency(code_text, raw, snapshot())
    status = resolution.get("resolution_status")
    current = resolution.get("current_agency_name")
    if status in ("resolved", "resolved_as_of_date") and current:
        return current, resolution["agency_stat_key"]
    if status == "historical":
        return current or "", resolution["agency_stat_key"]
    return raw, f"src:{code_text or '-'}:{raw}"
