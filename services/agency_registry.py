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


def display_agency_records(records: list[dict]) -> list[dict]:
    """웹 화면 전용: 각 행의 `처리기관`을 현행 표시명으로 바꾼다(제자리, 같은 리스트 반환).

    유형별 목록(report_query_service._get_records_from_connection)과 같은 규칙이다 —
    확인된 코드면 현행명, 아니면 원문. 서로 다른 부서코드(예: 경비교통과·교통과)로 들어온
    같은 기관이 화면마다 다르게 보이지 않게 한다. 모바일·확장 API 응답에는 쓰지 않는다(원문 계약).
    """
    cache: dict[tuple, str] = {}
    for record in records:
        if "처리기관" not in record:
            continue
        name = record.get("처리기관")
        pair = (_text(record.get("처리기관코드")), _text(name))
        if pair not in cache:
            cache[pair] = resolve_display_agency(*pair)[0] or (name if isinstance(name, str) else "")
        record["처리기관"] = cache[pair]
    return records


def with_current_agency_fields(records: list[dict]) -> list[dict]:
    """API 하위호환 추가 필드: `agency_name_current`(현행 표시명)·`agency_key`(통계 집계 키).

    원문 `처리기관`은 그대로 둔다. 크롬 확장이 담당자를 기관 키로 묶고 현행명을 보이는 데 쓴다
    (cloud my-reports 계약과 같은 이름). 제자리 변경, 같은 리스트 반환.
    """
    cache: dict[tuple, tuple[str, str]] = {}
    for record in records:
        if "처리기관" not in record:
            continue
        name = record.get("처리기관")
        pair = (_text(record.get("처리기관코드")), _text(name))
        if pair not in cache:
            if pair == (None, None):
                cache[pair] = ("", "")
            else:
                display = resolve_display_agency(*pair)[0] or pair[1] or ""
                cache[pair] = (display, resolve_stats_agency(*pair)[1])
        record["agency_name_current"], record["agency_key"] = cache[pair]
    return records


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
