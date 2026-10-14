"""기관·담당자·법규 통계표 응답 조립(EO R-05)."""

import pandas as pd

from services import fine_estimate
from services.law_values import available_laws as list_laws, read_laws
from services.report_cache import cached
from services.stats.common import _normalize_mode, _sanitize_jsonable
from services.stats.metrics import _build_stats_tables, _estimated_fine_totals, _fine_amounts, _prepare_metrics
from services.stats.overview import _compute_stats_overview
from services.stats.reads import _apply_stats_law_filter, _apply_stats_row_filters, _exclude_withdraw_rows, _load_stats_frames


def get_agency_stats(engine, filters=None, mode: str = "canonical", taxonomy: int = 3):
    available_years, frames = _load_stats_frames(engine, filters, mode, taxonomy)
    # 웹 get_stats_page 와 같이 날짜·처분·금액 파생값을 한 번만 계산한다(모바일 /api/v1/stats, 기술일지 B-08).
    # 결과는 같다(test_stats_page_matches_separate_calls).
    frames = {category: _prepare_metrics(df) for category, df in frames.items()}
    return _compute_agency_stats(available_years, frames, filters, mode)


@cached
def get_stats_page(engine, filters=None, mode: str = "canonical", taxonomy: int = 3):
    """웹 통계 화면: 표(`get_agency_stats`)와 요약·차트(`get_stats_overview`)를 한 번 읽은 같은 프레임으로 만든다.

    두 결과는 각 공개 함수와 같다(테스트 `test_stats_page_matches_separate_calls`). 표 계산이 프레임 열을 고치므로 복사본을 넘긴다.
    """
    available_years, frames = _load_stats_frames(engine, filters, mode, taxonomy)
    frames = {category: _prepare_metrics(df) for category, df in frames.items()}
    overview = _compute_stats_overview(available_years, frames, filters, mode)
    records = _compute_agency_stats(available_years, {category: df.copy() for category, df in frames.items()}, filters, mode)
    return records, overview


def _compute_agency_stats(available_years, frames, filters=None, mode: str = "canonical"):
    def calc_stats(df, category):
        empty_payload = {
            "by_agency": [],
            "by_person": [],
            "police_by_agency": [],
            "police_by_person": [],
            "other_by_agency": [],
            "other_by_person": [],
            "by_law": [],
            "total_fine_amount": 0,
            "available_laws": [],
        }
        if df.empty:
            return empty_payload

        df = _apply_stats_row_filters(df, filters)

        df = _exclude_withdraw_rows(df)

        if "위반법규" in df.columns:
            values = df["위반법규"].fillna("").astype(str)
            available_laws = list_laws(values)
            has_empty_law = bool(values.map(lambda value: not read_laws(value)).any())
        else:
            available_laws = []
            has_empty_law = False

        df = _apply_stats_law_filter(df, filters)

        if df.empty:
            # 법규 필터로 이 카테고리가 비어도 법규 선택지는 유지한다(모바일 computeStats 와 같음, 2026-09-25 동등성 검사)
            return _sanitize_jsonable({
                **empty_payload,
                **_estimated_fine_totals(df),
                "available_laws": available_laws,
                "has_empty_law": has_empty_law,
            })

        stats_agency, stats_person, stats_law = _build_stats_tables(df, category)

        category_total_fine = int(_fine_amounts(df).sum())
        category_estimates = _estimated_fine_totals(df)

        def _sort(items, key="total"):
            if not items:
                return []
            frame = pd.DataFrame(items)
            # 동점 결정성: total 내림차순, 표시명·키 오름차순(모바일과 같은 규칙).
            by = [key] + [c for c in ("agency", "person", "agency_key", "law", "month") if c in frame.columns]
            ascending = [False] + [True] * (len(by) - 1)
            return frame.sort_values(by=by, ascending=ascending).to_dict("records")

        all_agency = _sort(stats_agency)
        all_person = _sort(stats_person)
        all_law = _sort(stats_law)
        return _sanitize_jsonable({
            "by_agency": all_agency,
            "by_person": all_person,
            "police_by_agency": [item for item in all_agency if "경찰" in item["agency"]],
            "police_by_person": [item for item in all_person if "경찰" in item["agency"]],
            "other_by_agency": [item for item in all_agency if "경찰" not in item["agency"]],
            "other_by_person": [item for item in all_person if "경찰" not in item["agency"]],
            "by_law": all_law,
            "total_fine_amount": category_total_fine,
            **category_estimates,
            "estimate_rule_version": fine_estimate.RULE_VERSION,
            "available_laws": available_laws,
            "has_empty_law": has_empty_law,
        })

    results = {category: calc_stats(df, category) for category, df in frames.items()}
    traffic_df = frames.get("traffic")
    return _sanitize_jsonable({
        **results,
        "available_years": available_years,
        "traffic_total_fine": int(_fine_amounts(traffic_df).sum()) if traffic_df is not None and not traffic_df.empty else 0,
        "dedupe_mode": _normalize_mode(mode),
    })
