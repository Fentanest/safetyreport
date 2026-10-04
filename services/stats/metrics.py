"""파생 지표와 순수 집계(EO R-05): 금액·처리일·별점·처분 분류·요약 카드·상세표 행. 입력 프레임을 바꾸지 않는다."""
import re
from datetime import datetime

import pandas as pd

from core.utils.fallback import note_fallback
from services import report_policy
from services import fine_estimate
from services.stats.common import _UNASSIGNED_PERSON_VALUES, _ratio_item, _round_half_up, _text_or_empty


def _extract_fine_amount(text) -> int:
    if not text:
        return 0
    text = str(text)
    if "과태료" not in text:
        return 0
    # '40.000원' 처럼 점을 천 단위 구분자로 쓴 답변도 있다. 모바일 `extractFineAmount` 와 같은 규칙.
    match = re.search(r"([\d,.]+)\s*원", text)
    if match:
        digits = re.sub(r"[,.]", "", match.group(1))
        return int(digits) if digits.isdigit() else 0
    return 0



def _is_fine_amount_unknown(text) -> bool:
    """과태료 처분인데 금액을 읽을 수 없는 경우(0원과 구분, statistics-spec S-05)."""
    return "과태료" in str(text or "") and _extract_fine_amount(text) == 0


def _count_fine_amount_unknown(group_df: pd.DataFrame) -> int:
    if '_metric_unknown_fine' in group_df:
        return int(group_df['_metric_unknown_fine'].sum())
    if "범칙금_과태료" not in group_df.columns:
        return 0
    return int(group_df["범칙금_과태료"].apply(_is_fine_amount_unknown).sum())


def _fine_amounts(frame):
    if '_metric_fine_amount' in frame:
        return frame['_metric_fine_amount']
    return frame['범칙금_과태료'].apply(_extract_fine_amount)


def _prepare_metrics(frame):
    """요청 소유 프레임에서 원문·통계 의미를 바꾸지 않고 파생값을 한 번 계산한다."""
    if frame.empty:
        return frame
    frame = frame.copy()
    text = frame.get('범칙금_과태료', pd.Series('', index=frame.index)).fillna('')
    frame['_metric_fine_amount'] = text.apply(_extract_fine_amount)
    frame['_metric_unknown_fine'] = text.astype(str).str.contains('과태료', regex=False) & (frame['_metric_fine_amount'] == 0)
    frame['_metric_rating'] = pd.to_numeric(frame.get('별점', pd.Series(None, index=frame.index)), errors='coerce')
    frame['_metric_status'] = _stats_status_series(frame)
    try:
        end = pd.to_datetime(frame['답변일'].astype(str).str.slice(0, 10), errors='coerce', format='%Y-%m-%d')
        start = pd.to_datetime(frame['신고일'].astype(str).str.slice(0, 10), errors='coerce', format='%Y-%m-%d')
        days = (end - start).dt.days
        frame['_metric_days'] = days.where((days >= 0) & frame['_metric_status'].isin(report_policy.COMPLETED_STATUSES))
    except (KeyError, TypeError, ValueError):
        frame['_metric_days'] = float('nan')
    for name, mask in _stats_row_disposition_masks(frame).items():
        frame['_metric_disposition_' + name] = mask
    frame['_metric_estimated_amount'] = 0
    frame['_metric_estimated_count'] = 0
    columns = [c for c in ('category','entry_value','신고명','위반법규','차량번호','사진_첫촬영','사진_끝촬영','발생시각') if c in frame]
    eligible = frame.loc[frame['_metric_unknown_fine'], columns].fillna('')
    if columns and not eligible.empty:
        combinations = eligible.drop_duplicates()
        estimates = {}
        for values in combinations.itertuples(index=False, name=None):
            result = fine_estimate.estimate(dict(zip(columns, values)))
            estimates[values] = result['amount'] if result is not None else None
        amounts = [estimates[values] for values in eligible.itertuples(index=False, name=None)]
        frame.loc[eligible.index, '_metric_estimated_amount'] = [amount or 0 for amount in amounts]
        frame.loc[eligible.index, '_metric_estimated_count'] = [int(amount is not None) for amount in amounts]
    return frame


def _calc_avg_days_with_count(group_df):
    """(평균 처리일, 유효 표본 수). 표본 = 완료 신고 중 두 날짜가 모두 유효하고 차이 ≥ 0 인 행."""
    # S-10: 처리기간은 처리가 끝난 신고만. 이송 답변일이 붙은 처리중 신고·취하는 넣지 않는다.
    if '_metric_days' in group_df:
        days = group_df['_metric_days'].dropna()
        return (_round_half_up(float(days.mean()), 1) if len(days) > 0 else None), int(len(days))
    group_df = group_df[_stats_status_series(group_df).isin(report_policy.COMPLETED_STATUSES)]
    try:
        # 2026-09-24 사용자 결정: 처리일 = 답변일(날짜) − 신고일(날짜). 신고 시각은 버린다(12/30 23:40 → 1/2 = 3일).
        # 모바일 overview `_parse_overview_date` 와 같은 정의.
        d_end = pd.to_datetime(group_df["답변일"].astype(str).str.slice(0, 10), errors="coerce", format="%Y-%m-%d")
        d_start = pd.to_datetime(group_df["신고일"].astype(str).str.slice(0, 10), errors="coerce", format="%Y-%m-%d")
        days = (d_end - d_start).dt.days.dropna()
        days = days[days >= 0]
        return (_round_half_up(float(days.mean()), 1) if len(days) > 0 else None), int(len(days))
    except Exception as exc:
        note_fallback("report_stats.average_processing_days", exc)
        return None, 0


def _calc_avg_days(group_df):
    return _calc_avg_days_with_count(group_df)[0]


def _calc_avg_rating(group_df):
    if "별점" not in group_df.columns:
        return None, 0
    ratings = (group_df['_metric_rating'] if '_metric_rating' in group_df else pd.to_numeric(group_df["별점"], errors="coerce")).dropna()
    ratings = ratings[(ratings >= 1) & (ratings <= 5)]
    if len(ratings) == 0:
        return None, 0
    return _round_half_up(float(ratings.mean()), 2), int(len(ratings))


def _build_stats_tables(df: pd.DataFrame, category: str | None = None):
    """필터가 끝난 한 카테고리 프레임 → (기관별, 담당자별, 법규별) 행 목록. 모바일 `LocalDbService._buildCategory` 와 같은 규칙.
    입력 프레임은 바꾸지 않는다(사본에서 정리한다 — EO R-05)."""
    df = df.copy()
    # S-10: 표 포함 여부는 처리상태가 아니라 기관·담당자 값이 있는지로 정한다.
    # 배정된 처리중 신고도 기관/담당자 행에 들어가고 `in_progress` 로 따로 센다.
    df["처리기관"] = df.get("처리기관", pd.Series("", index=df.index, dtype="object")).fillna("").astype(str).str.strip()
    df["담당자"] = df.get("담당자", pd.Series("", index=df.index, dtype="object")).fillna("").astype(str).str.strip()
    df["범칙금_과태료"] = df.get("범칙금_과태료", pd.Series("", index=df.index, dtype="object")).fillna("")
    # 표시 미적용 경로: 원문 표시 그대로 묶는 src 키.
    if "_agency_key" not in df.columns:
        df["_agency_key"] = "src:-:" + df["처리기관"]
    if category and "category" not in df.columns:
        df["category"] = category
    # 2026-09-28 사용자 결정: 기관·담당자·법규 표는 답변이 완료된 신고만(처리중·보완요청·이송·취하는 넣지 않는다).
    # 처리중은 답변이 없어 처리기관·담당자도 없는 게 정상이다(실제 DB 처리중 83건 전부 기관 없음). 모바일 buildStatsCategory 와 같은 규칙.
    completed = _stats_status_series(df).isin(report_policy.COMPLETED_STATUSES)
    df = df[completed].copy()
    df_agency = df[df["처리기관"] != ""]
    df_person = df_agency[~df_agency["담당자"].isin(_UNASSIGNED_PERSON_VALUES)]

    def _row_metrics(group):
        total = len(group)
        counts = _stats_row_disposition_counts(group)
        avg_rating, rating_count = _calc_avg_rating(group)

        def _pct(key):
            return _round_half_up((counts[key] / total) * 100, 1) if total > 0 else 0

        avg_days, avg_days_count = _calc_avg_days_with_count(group)
        return {
            "total": total,
            "avg_days": avg_days,
            # 2026-09-28 추가: 평균 처리기간 표본 수. 표 합계 행이 행 평균을 이 수로 가중해 전체 평균을 낸다(행 수·총 건수 가중 아님).
            "avg_days_count": avg_days_count,
            "total_fine_amount": int(_fine_amounts(group).sum()),
            "fine_amount_unknown": _count_fine_amount_unknown(group),
            **_estimated_fine_totals(group),
            **{key: counts[key] for key in counts},
            **{f"{key}_pct": _pct(key) for key in counts},
            "avg_rating": avg_rating,
            "rating_count": rating_count,
        }

    stats_person = [
        {"agency": display, "agency_key": key, "person": person, **_row_metrics(group)}
        for (key, display, person), group in (
            df_person.assign(_person=df_person["담당자"])
            .groupby(["_agency_key", "처리기관", "_person"], sort=False)
        )
    ]
    stats_agency = [
        {"agency": display, "agency_key": key, **_row_metrics(group)}
        for (key, display), group in (
            df_agency.groupby(["_agency_key", "처리기관"], sort=False)
        )
    ]
    stats_person.sort(key=lambda r: (r["agency"], r["person"], r["agency_key"]))
    stats_agency.sort(key=lambda r: (r["agency"], r["agency_key"]))

    stats_law = []
    if "위반법규" in df.columns:
        df_law = df.copy()
        df_law["위반법규"] = df_law["위반법규"].fillna("").astype(str)
        df_law = df_law[df_law["위반법규"].str.strip() != ""]
        stats_law = [
            {"law": law, **_row_metrics(group)}
            for law, group in df_law.groupby("위반법규")
        ]
    return stats_agency, stats_person, stats_law


def _parse_overview_date(value):
    text = _text_or_empty(value)
    if len(text) < 10:
        return None
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _summarize_overview_frame(df: pd.DataFrame) -> dict:
    """통계 요약 카드/월별 추이용 집계. 모바일 Standalone `LocalDbService.computeStatsOverview` 와 같은 정의."""
    total = int(len(df))
    statuses = report_policy.status_series(df) if "처리상태" in df.columns else pd.Series(dtype=str)
    report_dates = [_parse_overview_date(v) for v in (df["신고일"] if "신고일" in df.columns else [])]
    answer_dates = [_parse_overview_date(v) for v in (df["답변일"] if "답변일" in df.columns else [])]
    if len(report_dates) < total:
        report_dates += [None] * (total - len(report_dates))
    if len(answer_dates) < total:
        answer_dates += [None] * (total - len(answer_dates))

    day_samples = []
    reversed_count = 0
    reported_by_month: dict[str, int] = {}
    answered_by_month: dict[str, int] = {}
    completed_flags = [status in report_policy.COMPLETED_STATUSES for status in statuses]
    if len(completed_flags) < total:
        completed_flags += [False] * (total - len(completed_flags))
    for reported, answered, is_completed in zip(report_dates, answer_dates, completed_flags):
        if reported is not None:
            key = reported.strftime("%Y-%m")
            reported_by_month[key] = reported_by_month.get(key, 0) + 1
        if answered is not None:
            key = answered.strftime("%Y-%m")
            answered_by_month[key] = answered_by_month.get(key, 0) + 1
        # S-10: 평균 처리기간은 완료 신고만(기관표 `_calc_avg_days` 와 같은 기준).
        if is_completed and reported is not None and answered is not None:
            days = (answered - reported).days
            if days < 0:
                reversed_count += 1
            else:
                day_samples.append(days)

    def _count(predicate) -> int:
        return int(sum(1 for status in statuses if predicate(status)))

    # 2026-09-28 통계 개편(추가 필드). 모바일 `LocalDbService.summarizeOverviewRows` 와 같은 정의.
    fine_texts = (
        df["범칙금_과태료"].fillna("").astype(str).tolist() if "범칙금_과태료" in df.columns else [""] * total
    )
    # 답변월 기준 과태료 건수: 월별 처리 추이의 보조 계열(같은 답변일 기준). 과태료 = 처분 문구에 '과태료'.
    answered_fine_by_month: dict[str, int] = {}
    for answered, fine_text in zip(answer_dates, fine_texts):
        if answered is not None and "과태료" in fine_text:
            key = answered.strftime("%Y-%m")
            answered_fine_by_month[key] = answered_fine_by_month.get(key, 0) + 1

    return {
        "total": total,
        "completed": _count(lambda s: s in report_policy.COMPLETED_STATUSES),
        "accept": _count(lambda s: s == "수용"),
        "partial": _count(lambda s: s == "일부수용"),
        "reject": _count(lambda s: s in report_policy.REJECT_STATUSES),
        "supplement": _count(lambda s: s == report_policy.SUPPLEMENT_STATUS),
        "processing": _count(lambda s: s in report_policy.PROCESSING_STATUSES),
        "withdraw": _count(lambda s: s == report_policy.WITHDRAWN_STATUS),
        "avg_days": _round_half_up(sum(day_samples) / len(day_samples), 1) if day_samples else None,
        "avg_days_count": len(day_samples),
        "reversed_date_count": reversed_count,
        "undated_report_count": int(sum(1 for d in report_dates if d is None)),
        "monthly_reported": [{"month": k, "count": v} for k, v in sorted(reported_by_month.items())],
        "monthly_answered": [{"month": k, "count": v} for k, v in sorted(answered_by_month.items())],
        "monthly_answered_fine": [{"month": k, "count": v} for k, v in sorted(answered_fine_by_month.items())],
        "disposition": _overview_disposition(df),
        "fine_amount": _overview_fine_amount(df),
        "report_types": _overview_report_types(df),
        "violation_laws": _overview_violation_laws(df),
        "result_distribution": {
            "accept": _count(lambda s: s == "수용"),
            "partial": _count(lambda s: s == "일부수용"),
            "reject": _count(lambda s: s in report_policy.REJECT_STATUSES),
            "unknown": _count(lambda s: s == report_policy.ANSWERED_UNKNOWN_STATUS),
        },
    }


def _overview_disposition(df: pd.DataFrame) -> dict[str, int]:
    """카테고리 전체(기관 유무와 무관) 처분 분류. 통계표 행과 같은 규칙(`_stats_row_disposition_counts`).

    과태료·경고/범칙금·불수용/기타는 한 신고에 겹칠 수 있다(문구에 과태료와 범칙금이 함께 있는 등).
    `overlap` = 세 항목 합 − 셋 중 하나 이상에 해당하는 신고 수. 0 이 아니면 세 항목 합이 신고 수보다 크다.
    """
    keys = ("fines", "warnings", "rejects", "unconfirmed", "in_progress", "disposition_unknown", "no_penalty", "unclassified")
    if df.empty:
        return {**{key: 0 for key in keys}, "overlap": 0}
    counts = _stats_row_disposition_counts(df)
    masks = report_policy.dashboard_disposition_masks(df)
    decided = int((masks["fines"] | masks["warnings"] | masks["rejects"]).sum())
    result = {key: int(counts.get(key, 0)) for key in keys}
    result["overlap"] = result["fines"] + result["warnings"] + result["rejects"] - decided
    return result


def _overview_fine_amount(df: pd.DataFrame) -> dict[str, int]:
    """확정(원문 금액)과 추정(법정 최저 기준) 과태료를 따로 센다. 둘을 더한 값은 내려주지 않는다(PROJECT_RULES §3-2)."""
    if df.empty or "범칙금_과태료" not in df.columns:
        return {"confirmed_amount": 0, "confirmed_count": 0, "unknown_count": 0, "estimated_amount": 0, "estimated_count": 0}
    fine_series = df["범칙금_과태료"].fillna("")
    amounts = _fine_amounts(df)
    has_fine = fine_series.astype(str).str.contains("과태료", na=False)
    estimates = _estimated_fine_totals(df)
    return {
        "confirmed_amount": int(amounts.sum()),
        # 확정 건수 = 금액을 읽은 과태료 건(모바일 기관 카드 `과태료 − 금액 미확인` 과 같은 값)
        "confirmed_count": int((has_fine & (amounts > 0)).sum()),
        "unknown_count": _count_fine_amount_unknown(df),
        "estimated_amount": estimates["estimated_fine_amount"],
        "estimated_count": estimates["estimated_fine_count"],
    }


def _overview_report_types(df: pd.DataFrame) -> list[dict]:
    """위반 유형(신고명 원문, 앞뒤 공백 제거) 건수. 전체 목록을 건수 내림차순·이름 오름차순으로. 빈 신고명은 name ''."""
    if df.empty or "신고명" not in df.columns:
        return []
    names = df["신고명"].fillna("").astype(str).str.strip()
    counts: dict[str, int] = {}
    for name in names:
        counts[name] = counts.get(name, 0) + 1
    return [{"name": name, "count": count} for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]


def _overview_violation_laws(df: pd.DataFrame) -> list[dict]:
    """One report per exact stored normalized law string (including multi-law strings).

    This preserves the existing exact law filter and list drilldown population.
    Missing law is its own bucket; report titles never supply law information.
    """
    if df.empty:
        return []
    laws = df.get("위반법규", pd.Series("", index=df.index)).fillna("").astype(str).str.strip()
    return [{"name": name, "filter": name or "__없음__", "count": int(count)}
            for name, count in sorted(laws.value_counts().items(), key=lambda item: (-item[1], item[0]))]


def _disposition_counts(group_df: pd.DataFrame) -> dict[str, int]:
    """기관 상세 4분류(report_policy.dashboard_disposition). 처리중을 '미확인'에 둔다 — 통계표 8분류와 의도적으로 다름."""
    return {name: int(mask.sum()) for name, mask in report_policy.dashboard_disposition_masks(group_df).items()}


def _stats_status_series(group_df: pd.DataFrame) -> pd.Series:
    if '_metric_status' in group_df:
        return group_df['_metric_status']
    return report_policy.status_series(group_df)


def _stats_row_disposition_counts(group_df: pd.DataFrame) -> dict[str, int]:
    """통계표(기관/담당자/법규) 행 처분 분류. S-10: 처리중은 '미분류'에 섞지 않고 `in_progress` 로 뺀다.

    처리중 = 완료(수용·일부수용·불수용·기타·답변완료)도 취하도 아닌 상태(처리중·진행·검토중·보완요청·이송·빈 값 등).
    모바일 Standalone `_AgencyAgg` 와 같은 정의(report_policy.table_disposition, contracts/report-policy-vectors.json).
    """
    if '_metric_disposition_fines' in group_df:
        return {name: int(group_df['_metric_disposition_' + name].sum()) for name in (
            'fines', 'warnings', 'rejects', 'unconfirmed', 'in_progress',
            'disposition_unknown', 'no_penalty', 'unclassified')}
    return {name: int(mask.sum()) for name, mask in _stats_row_disposition_masks(group_df).items()}


def _stats_row_disposition_masks(group_df):
    """통계표 8분류(report_policy.table_disposition). 미리 계산한 `_metric_status` 가 있으면 그것을 쓴다."""
    return report_policy.table_disposition_masks(group_df, status=_stats_status_series(group_df))


def _estimated_fine_totals(group_df: pd.DataFrame) -> dict[str, int]:
    """금액 없는 과태료 행의 법정 최저 기준 추정 합계. 확정 금액(`total_fine_amount`)과 섞지 않는다."""
    if '_metric_estimated_amount' in group_df:
        return {'estimated_fine_amount': int(group_df['_metric_estimated_amount'].sum()),
                'estimated_fine_count': int(group_df['_metric_estimated_count'].sum())}
    if group_df.empty or "범칙금_과태료" not in group_df.columns:
        return {"estimated_fine_amount": 0, "estimated_fine_count": 0}
    amount = 0
    count = 0
    unknown = group_df['범칙금_과태료'].apply(_is_fine_amount_unknown)
    columns = [c for c in ('category','entry_value','신고명','위반법규','차량번호','사진_첫촬영','사진_끝촬영','발생시각') if c in group_df]
    if not unknown.any() or not columns:
        return {"estimated_fine_amount": 0, "estimated_fine_count": 0}
    eligible = group_df.loc[unknown, columns].fillna('')
    combinations = eligible.groupby(columns, dropna=False, sort=False).size().reset_index(name='_weight')
    for record in combinations.to_dict(orient="records"):
        result = fine_estimate.estimate(record)
        if result is None:
            continue
        amount += result["amount"] * record['_weight']
        count += record['_weight']
    return {"estimated_fine_amount": int(amount), "estimated_fine_count": int(count)}


def _build_status_breakdown(group_df: pd.DataFrame) -> list[dict]:
    status_series = report_policy.breakdown_status_series(group_df)
    processing_mask = status_series == report_policy.PROCESSING_LABEL
    ordered = [
        _ratio_item("수용", int((status_series == "수용").sum()), len(group_df)),
        _ratio_item("일부수용", int((status_series == "일부수용").sum()), len(group_df)),
        _ratio_item("불수용", int((status_series == "불수용").sum()), len(group_df)),
        _ratio_item("기타", int((status_series == "기타").sum()), len(group_df)),
        _ratio_item("답변완료", int((status_series == "답변완료").sum()), len(group_df)),
        _ratio_item("보완요청", int((status_series == "보완요청").sum()), len(group_df)),
        _ratio_item("처리중", int(processing_mask.sum()), len(group_df)),
        _ratio_item("취하", int((status_series == report_policy.WITHDRAWN_STATUS).sum()), len(group_df)),
        _ratio_item("이송", int((status_series == "이송").sum()), len(group_df)),
    ]
    return [item for item in ordered if item["count"] > 0]


def _build_disposition_breakdown(group_df: pd.DataFrame) -> list[dict]:
    counts = _disposition_counts(group_df)

    ordered = [
        _ratio_item("과태료", counts["fines"], len(group_df)),
        _ratio_item("경고/범칙금", counts["warnings"], len(group_df)),
        _ratio_item("불수용/기타", counts["rejects"], len(group_df)),
        _ratio_item("미확인", counts["unconfirmed"], len(group_df)),
    ]
    return [item for item in ordered if item["count"] > 0]


def _build_agency_breakdown(group_df: pd.DataFrame) -> list[dict]:
    if "처리기관" not in group_df.columns:
        return []
    frame = group_df.copy()
    frame["처리기관"] = frame["처리기관"].fillna("").astype(str).map(lambda value: value.strip())
    frame = frame[frame["처리기관"] != ""]
    if frame.empty:
        return []
    if "_agency_key" not in frame.columns:
        frame["_agency_key"] = "src:-:" + frame["처리기관"]
    grouped = frame.groupby(["_agency_key", "처리기관"], sort=False).size().reset_index(name="count")
    total = int(len(frame))
    results = []
    for _, row in grouped.iterrows():
        results.append({
            "name": str(row["처리기관"]),
            "agency_key": str(row["_agency_key"]),
            "count": int(row["count"]),
            "pct": round((int(row["count"]) / total) * 100, 1) if total > 0 else 0,
        })
    results.sort(key=lambda item: (-item["count"], item["name"], item["agency_key"]))
    return results
