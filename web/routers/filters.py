from __future__ import annotations

import settings.settings as app_settings
from services import fine_estimate
from services.agency_registry import display_agency_records  # noqa: F401 — 웹 화면 공용 표시명 변환


def default_dedupe_mode() -> str:
    return "canonical" if app_settings.use_representative_records else "raw"


def normalize_dedupe_mode(value: str | None, *, default: str | None = None) -> str:
    resolved_default = default or default_dedupe_mode()
    normalized = (value or resolved_default).strip().lower()
    return normalized if normalized in {"raw", "canonical"} else resolved_default


def normalize_map_category(value: str | None, taxonomy: int = 3) -> str:
    """taxonomy=3(기본, 예전과 같음): all·traffic·parking·other. taxonomy=5: all·report_category.CATEGORIES."""
    from services import report_category

    normalized = (value or "all").strip().lower()
    allowed = {"all", *report_category.CATEGORIES} if taxonomy == 5 else {"all", *report_category.LEGACY_CATEGORIES}
    return normalized if normalized in allowed else "all"


# 목록 화면 전용 표시값(docs/design/statistics-spec.md §4-1·§4-2, PROJECT_RULES §3-2): 추정은 DB 에 저장하지
# 않고 조회마다 계산한다. 판정은 services/fine_estimate.estimate_if_unknown 을 그대로 쓴다(재구현 금지) —
# 로컬 통계(services/stats/metrics.py)·공유 payload(services/community_capture.py)와 같은 입력 열
# (fine_estimate.FINE_ESTIMATE_FIELDS)만 record 에서 골라 넘긴다. 원래 web/routers/data.py 전용이던
# _with_fine_estimates(380534b)를 여기로 옮겨 watchlist_route.py·rating_route.py·duplicate_route.py·
# dashboard.py 가 함께 쓴다. 모바일 API(web/routers/api_route.py, exact_values=True 또는
# services/stats 의 고정 열 whitelist)는 이 함수를 거치지 않으므로 계약에 영향이 없다.
def with_fine_estimates(records: list[dict]) -> list[dict]:
    for record in records:
        fields = {key: record.get(key) for key in fine_estimate.FINE_ESTIMATE_FIELDS}
        # 금액 오타 보정(2026-10-12): 원문이 깨진 금액이면 법정 금액과 맞는 후보로 읽은 표시용 문구. 원문은 그대로 둔다.
        record["fine_corrected_text"] = fine_estimate.corrected_fine_text(record.get("범칙금_과태료"), fields)
        result = fine_estimate.estimate_if_unknown(record.get("범칙금_과태료"), fields)
        record["fine_estimate_amount"] = result["amount"] if result else None
        record["fine_estimate_basis"] = result["basis"] if result else None
    return records

