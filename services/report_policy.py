"""신고 상태·처분 규칙의 정본(EO R-01).

같은 규칙을 SQL·pandas·브라우저 JS·모바일 Dart 가 각각 쓰므로, 정책 이름과 순수 판정을 여기 두고
각 구현은 `contracts/report-policy-vectors.json`(서버·모바일 바이트 동일)으로 같은 결과를 내는지 검사한다.
모바일 정본은 lib/services/report_policy.dart, 웹 화면은 web/static/ui/report-policy.js.

입력 축: 처리상태(canonical 표시 상태, 원본 `상태` 와 다름), 범칙금_과태료(확정 처분 원문), category, entry_value.
값은 앞뒤 공백(세 언어가 공통으로 떼는 문자)을 떼고 비교한다. None 은 빈 문자열이다.

정책 이름
- display_status: 목록 필터·별점 대상 판정. 진행/진행중/검토중/처리중 → '처리중'. badge_key: 상태 배지 색.
- breakdown_status: 기관 상세 상태 분포·지도 묶음. display_status 에 더해 빈 값도 '처리중'.
- is_completed / is_processing: 요약 카드·대시보드. 처리중은 좁은 집합(빈 값·보완요청·이송 제외).
- table_disposition: 통계표(기관·담당자·법규) 행 8분류. in_progress 는 넓은 정의(완료도 취하도 아님).
- dashboard_disposition: 기관 상세의 4분류. 처리중을 따로 빼지 않고 '미확인'에 둔다(통계표와 의도적으로 다름).
- traffic_dashboard: 대시보드 교통 막대 4계열(교통 분류만).
- list_status_filter / list_fine_filter: 목록·드릴다운 URL 필터.
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import func

# Python strip()·Dart trim()·JS trim() 이 공통으로 떼는 문자(rating_eligibility._TRIM_CHARS 에 \r 을 더한 집합)
TRIM_CHARS = " \t\n\x0b\x0c\r\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"

PROCESSING_LABEL = "처리중"
COMPLETED_STATUSES = frozenset({"수용", "불수용", "일부수용", "기타", "답변완료"})
PROCESSING_STATUSES = frozenset({"처리중", "진행", "진행중", "검토중"})
REJECT_STATUSES = frozenset({"불수용", "기타"})
WITHDRAWN_STATUS = "취하"
SUPPLEMENT_STATUS = "보완요청"
ANSWERED_UNKNOWN_STATUS = "답변완료"
FINE_UNKNOWN_TEXT = "미확인"

# SQL 에 넣을 때 순서를 고정한다(쿼리 문자열이 매번 같게).
COMPLETED_ORDER = ("수용", "불수용", "일부수용", "기타", "답변완료")
PROCESSING_ORDER = ("처리중", "진행", "진행중", "검토중")
REJECT_ORDER = ("불수용", "기타")

TABLE_DISPOSITION_KEYS = ("fines", "warnings", "rejects", "unconfirmed", "in_progress",
                          "disposition_unknown", "no_penalty", "unclassified")
DASHBOARD_DISPOSITION_KEYS = ("fines", "warnings", "rejects", "unconfirmed")
TRAFFIC_DASHBOARD_KEYS = ("fine", "penalty", "reject", "unconfirmed")

# 과태료가 붙을 수 있는 유형(교통위반·불법주정차·쓰레기 메뉴). 그 밖(시설물 등)은 처분 대상 아님.
_ELIGIBLE_CATEGORIES = frozenset({"traffic", "parking"})
_ELIGIBLE_MENUS = ("자동차·교통위반", "불법주정차신고", "쓰레기, 폐기물")
# 일부수용 + 처분 없음을 '과태료 미확인'으로 보는 메뉴(파서 `_PARTIAL_UNKNOWN_MENUS` 와 같음, 주정차 분류 포함).
_PARTIAL_UNKNOWN_CATEGORIES = frozenset({"parking"})
_PARTIAL_UNKNOWN_MENUS = ("불법주정차신고", "버스전용차로 위반", "쓰레기, 폐기물")


# ── 순수 판정 ────────────────────────────────────────────────────────────────

def norm(value) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip(TRIM_CHARS)


def display_status(status) -> str:
    text = norm(status)
    return PROCESSING_LABEL if text in PROCESSING_STATUSES else text


def breakdown_status(status) -> str:
    text = display_status(status)
    return PROCESSING_LABEL if text == "" else text


def badge_key(status) -> str:
    """상태 배지 색 이름: accept/partial/reject/processing/supplement/withdraw/default."""
    text = norm(status)
    if text == "수용":
        return "accept"
    if text == "일부수용":
        return "partial"
    if text in REJECT_STATUSES:
        return "reject"
    if text in PROCESSING_STATUSES:
        return "processing"
    if text == SUPPLEMENT_STATUS:
        return "supplement"
    if text == WITHDRAWN_STATUS:
        return "withdraw"
    return "default"


def is_completed(status) -> bool:
    return norm(status) in COMPLETED_STATUSES


def is_processing(status) -> bool:
    return norm(status) in PROCESSING_STATUSES


def is_reject(status) -> bool:
    return norm(status) in REJECT_STATUSES


def is_withdrawn(status) -> bool:
    return norm(status) == WITHDRAWN_STATUS


def has_fine(fine) -> bool:
    return "과태료" in norm(fine)


def has_warning(fine) -> bool:
    text = norm(fine)
    return "경고" in text or "범칙금" in text


def is_fine_unknown(fine) -> bool:
    return norm(fine) == FINE_UNKNOWN_TEXT


def penalty_eligible(category, entry_value) -> bool:
    entry = norm(entry_value)
    return norm(category) in _ELIGIBLE_CATEGORIES or any(menu in entry for menu in _ELIGIBLE_MENUS)


def partial_unknown_menu(category, entry_value) -> bool:
    entry = norm(entry_value)
    return norm(category) in _PARTIAL_UNKNOWN_CATEGORIES or any(menu in entry for menu in _PARTIAL_UNKNOWN_MENUS)


def table_disposition(row) -> dict[str, bool]:
    status, fine = norm(row.get("처리상태")), norm(row.get("범칙금_과태료"))
    category, entry = row.get("category"), row.get("entry_value")
    fines, warnings, rejects = has_fine(fine), has_warning(fine), status in REJECT_STATUSES
    decided = fines or warnings or rejects
    completed = status in COMPLETED_STATUSES
    in_progress = not decided and not completed and status != WITHDRAWN_STATUS
    unconfirmed = not decided and not in_progress
    unknown = unconfirmed and (
        fine == FINE_UNKNOWN_TEXT or (status == "일부수용" and fine == "" and partial_unknown_menu(category, entry)))
    no_penalty = unconfirmed and not unknown and not penalty_eligible(category, entry) and completed
    return {"fines": fines, "warnings": warnings, "rejects": rejects, "unconfirmed": unconfirmed,
            "in_progress": in_progress, "disposition_unknown": unknown, "no_penalty": no_penalty,
            "unclassified": unconfirmed and not unknown and not no_penalty}


def dashboard_disposition(row) -> dict[str, bool]:
    fine, status = row.get("범칙금_과태료"), row.get("처리상태")
    fines, warnings, rejects = has_fine(fine), has_warning(fine), is_reject(status)
    return {"fines": fines, "warnings": warnings, "rejects": rejects,
            "unconfirmed": not (fines or warnings or rejects)}


def traffic_dashboard(row) -> dict[str, bool]:
    traffic = norm(row.get("category")) == "traffic"
    fine, status = row.get("범칙금_과태료"), row.get("처리상태")
    return {"fine": traffic and has_fine(fine), "penalty": traffic and has_warning(fine),
            "reject": traffic and is_reject(status),
            "unconfirmed": traffic and is_fine_unknown(fine) and not is_reject(status)}


def list_status_filter(name: str, status) -> bool:
    text = norm(status)
    if name == PROCESSING_LABEL:
        return text in PROCESSING_STATUSES
    if name == "완료":
        return text in COMPLETED_STATUSES
    if name == "불수용":
        return text in REJECT_STATUSES
    return text == norm(name)


def list_fine_filter(name: str, fine, status) -> bool:
    if name == "과태료":
        return has_fine(fine)
    if name == "경고":
        return has_warning(fine)
    if name == FINE_UNKNOWN_TEXT:
        return is_fine_unknown(fine) and not is_reject(status)
    return True


# ── pandas 어댑터 ────────────────────────────────────────────────────────────

def text_series(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series("", index=df.index, dtype="object")
    return df[column].fillna("").astype(str).str.strip(TRIM_CHARS)


def status_series(df: pd.DataFrame) -> pd.Series:
    return text_series(df, "처리상태")


def penalty_eligible_mask(df: pd.DataFrame) -> pd.Series:
    category, entry = text_series(df, "category"), text_series(df, "entry_value")
    mask = category.isin(_ELIGIBLE_CATEGORIES)
    for menu in _ELIGIBLE_MENUS:
        mask |= entry.str.contains(menu, regex=False)
    return mask


def partial_unknown_menu_mask(df: pd.DataFrame) -> pd.Series:
    category, entry = text_series(df, "category"), text_series(df, "entry_value")
    mask = category.isin(_PARTIAL_UNKNOWN_CATEGORIES)
    for menu in _PARTIAL_UNKNOWN_MENUS:
        mask |= entry.str.contains(menu, regex=False)
    return mask


def table_disposition_masks(df: pd.DataFrame, status: pd.Series | None = None) -> dict[str, pd.Series]:
    fine = text_series(df, "범칙금_과태료")
    status = status_series(df) if status is None else status
    fines = fine.str.contains("과태료", regex=False)
    warnings = fine.str.contains("경고", regex=False) | fine.str.contains("범칙금", regex=False)
    rejects = status.isin(REJECT_STATUSES)
    decided = fines | warnings | rejects
    completed = status.isin(COMPLETED_STATUSES)
    in_progress = ~decided & ~completed & (status != WITHDRAWN_STATUS)
    unconfirmed = ~decided & ~in_progress
    unknown = unconfirmed & (
        (fine == FINE_UNKNOWN_TEXT) | ((status == "일부수용") & (fine == "") & partial_unknown_menu_mask(df)))
    no_penalty = unconfirmed & ~unknown & ~penalty_eligible_mask(df) & completed
    return {"fines": fines, "warnings": warnings, "rejects": rejects, "unconfirmed": unconfirmed,
            "in_progress": in_progress, "disposition_unknown": unknown, "no_penalty": no_penalty,
            "unclassified": unconfirmed & ~unknown & ~no_penalty}


def dashboard_disposition_masks(df: pd.DataFrame) -> dict[str, pd.Series]:
    fine, status = text_series(df, "범칙금_과태료"), status_series(df)
    fines = fine.str.contains("과태료", regex=False)
    warnings = fine.str.contains("경고", regex=False) | fine.str.contains("범칙금", regex=False)
    rejects = status.isin(REJECT_STATUSES)
    return {"fines": fines, "warnings": warnings, "rejects": rejects, "unconfirmed": ~(fines | warnings | rejects)}


def traffic_dashboard_masks(df: pd.DataFrame) -> dict[str, pd.Series]:
    traffic = text_series(df, "category") == "traffic"
    fine, status = text_series(df, "범칙금_과태료"), status_series(df)
    rejects = status.isin(REJECT_STATUSES)
    return {"fine": traffic & fine.str.contains("과태료", regex=False),
            "penalty": traffic & (fine.str.contains("경고", regex=False) | fine.str.contains("범칙금", regex=False)),
            "reject": traffic & rejects,
            "unconfirmed": traffic & (fine == FINE_UNKNOWN_TEXT) & ~rejects}


def display_status_series(df: pd.DataFrame) -> pd.Series:
    status = status_series(df)
    return status.where(~status.isin(PROCESSING_STATUSES), PROCESSING_LABEL)


def breakdown_status_series(df: pd.DataFrame) -> pd.Series:
    status = display_status_series(df)
    return status.where(status != "", PROCESSING_LABEL)


# ── SQL(SQLAlchemy) 어댑터 ───────────────────────────────────────────────────

def sql_norm(column):
    """SQLite trim 은 기본으로 공백(U+0020)만 뗀다. 같은 문자 집합을 넘겨 순수 판정과 맞춘다."""
    return func.trim(func.coalesce(column, ""), TRIM_CHARS)


def sql_list_status_filter(column, name: str):
    value = sql_norm(column)
    if name == PROCESSING_LABEL:
        return value.in_(PROCESSING_ORDER)
    if name == "완료":
        return value.in_(COMPLETED_ORDER)
    if name == "불수용":
        return value.in_(REJECT_ORDER)
    return value == norm(name)


def sql_list_fine_filter(fine_column, status_column, name: str):
    fine = sql_norm(fine_column)
    if name == "과태료":
        return fine.contains("과태료")
    if name == "경고":
        return fine.contains("경고") | fine.contains("범칙금")
    if name == FINE_UNKNOWN_TEXT:
        return (fine == FINE_UNKNOWN_TEXT) & sql_norm(status_column).not_in(REJECT_ORDER)
    return None


def sql_not_withdrawn(column):
    return sql_norm(column) != WITHDRAWN_STATUS
