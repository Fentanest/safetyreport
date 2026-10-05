"""통계 서비스 공개 진입점(facade, EO R-05).

구현은 services/stats/ 로 나눴다: reads(원천 읽기·snapshot·투영·행 필터) → metrics(파생 지표·순수 집계) →
dashboard·agency·overview·map(응답 조립). 예전 이름(내부 도우미 포함)을 그대로 다시 내보낸다.
"""
from services.stats.common import (  # noqa: F401
    _MAP_COLUMNS, _MAP_MISSING_COLUMNS, _MAP_TARGET_KEYS, _REPORT_FIELDS, _STATS_COLUMNS, _UNASSIGNED_PERSON_VALUES, _first_nonempty_value, _int_or_default, _is_finite_number, _normalize_map_category_value, _normalize_mode, _ratio_item, _recent_answer_sort_key, _round_half_up, _row_to_dict, _sanitize_jsonable, _text_or_empty,
)
from services.stats.reads import (  # noqa: F401
    _apply_registry_agency_display, _apply_stats_law_filter, _apply_stats_row_filters, _build_select_for_columns, _build_stats_query, _ensure_id_column, _exclude_withdraw_rows, _last_sync_label, _load_available_years, _load_map_records_frame, _load_stats_frames, _read_stats_frame, get_last_sync_label,
)
from services.stats.metrics import (  # noqa: F401
    _build_agency_breakdown, _build_disposition_breakdown, _build_stats_tables, _build_status_breakdown, _calc_avg_days, _calc_avg_days_with_count, _calc_avg_rating, _count_fine_amount_unknown, _disposition_counts, _estimated_fine_totals, _extract_fine_amount, _fine_amounts, _is_fine_amount_unknown, _overview_disposition, _overview_fine_amount, _overview_report_types, _overview_violation_laws, _parse_overview_date, _prepare_metrics, _stats_row_disposition_counts, _stats_row_disposition_masks, _stats_status_series, _summarize_overview_frame,
)
from services.stats.dashboard import (  # noqa: F401
    get_dashboard_stats,
)
from services.stats.agency import (  # noqa: F401
    _compute_agency_stats, get_agency_stats, get_stats_page,
)
from services.stats.overview import (  # noqa: F401
    _compute_stats_overview, get_stats_overview,
)
from services.stats.map import (  # noqa: F401
    _aggregate_map_points, apply_pin_basis, get_report_map_missing_groups, get_report_map_missing_summary, get_report_map_stats,
    normalize_pin_basis,
)
