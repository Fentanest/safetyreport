from __future__ import annotations

import settings.settings as app_settings


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

