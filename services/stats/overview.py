"""통계 요약 카드·월별 추이 응답 조립(EO R-05)."""

import pandas as pd

import settings.settings as app_settings
from services.stats.common import _normalize_mode, _sanitize_jsonable
from services.stats.metrics import _summarize_overview_frame
from services.stats.reads import _apply_stats_law_filter, _apply_stats_row_filters, _exclude_withdraw_rows, _load_stats_frames


def get_stats_overview(engine, filters=None, mode: str = "canonical"):
    """통계 화면 요약 카드 + 월별 추이(신고일/답변일 기준 각각).

    `get_agency_stats` 와 같은 행(필터·대표건·취하 제외·법규)을 쓰므로 카드와 기관표가 같은 데이터 집합을 본다.
    평균 처리일은 기관 평균을 합치지 않고 원자료에서 직접 계산하며 표본 수(`avg_days_count`)를 함께 내려준다.
    """
    available_years, df_t, df_p, df_o = _load_stats_frames(engine, filters, mode)
    return _compute_stats_overview(available_years, df_t, df_p, df_o, filters, mode)


def _compute_stats_overview(available_years, df_t, df_p, df_o, filters=None, mode: str = "canonical"):
    def _filtered(df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        df = _apply_stats_row_filters(df, filters)
        df = _exclude_withdraw_rows(df)
        return _apply_stats_law_filter(df, filters)

    frames = {"traffic": _filtered(df_t), "parking": _filtered(df_p), "other": _filtered(df_o)}
    non_empty = [frame for frame in frames.values() if not frame.empty]
    combined = pd.concat(non_empty, ignore_index=True) if non_empty else pd.DataFrame()
    payload = {key: _summarize_overview_frame(frame) for key, frame in frames.items()}
    payload["all"] = _summarize_overview_frame(combined)
    payload.update({
        "available_years": available_years,
        "year_basis": "답변일",
        "exclude_withdraw": bool(app_settings.exclude_withdraw),
        "dedupe_mode": _normalize_mode(mode),
    })
    return _sanitize_jsonable(payload)
