"""통계 공통 상수·JSON 정리·작은 도우미."""
import math
from decimal import ROUND_HALF_UP, Decimal

import pandas as pd

from services import duplicate_group_service


_STATS_COLUMNS = [
    "ID",
    "신고명",
    "신고번호",
    "신고일",
    "답변일",
    "처리기관",
    "처리기관코드",
    "담당자",
    "처리상태",
    "범칙금_과태료",
    "위반법규",
    "위반장소",
    "발생일자",
    "발생시각",
    "별점",
    "synced_at",
    "차량번호",
    "사진_첫촬영",
    "사진_끝촬영",
]

_MAP_COLUMNS = [
    "ID",
    "신고번호",
    "신고명",
    "신고일",
    "답변일",
    "처리상태",
    "범칙금_과태료",
    "위반장소",
    # 통계 화면과 같은 조건(법규·발생일시·담당자)으로 지도를 좁힐 때 쓰는 열(2026-09-28)
    "위반법규",
    "발생일자",
    "발생시각",
    "담당자",
    "주소정규화",
    "행정구역",
    "위도",
    "경도",
    "처리기관",
    "처리기관코드",
]


def _round_half_up(value, digits: int) -> float:
    """통계 표시용 반올림. Python round()(짝수 쪽 반올림)와 달리 x.x5 를 올린다.

    float 의 정확한 값 기준이라 모바일 Dart `toStringAsFixed` 와 같은 결과가 나온다(통계 요약·기관표 전용).
    """
    return float(Decimal(value).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP))

_REPORT_FIELDS = [
    "ID",
    "신고번호",
    "신고명",
    "신고일",
    "답변일",
    "처리기관",
    "처리기관코드",
    "담당자",
    "처리상태",
    "범칙금_과태료",
    "벌점",
    "차량번호",
    "위반법규",
    "위반장소",
    "발생일자",
    "발생시각",
    "신고내용",
    "처리내용",
    "첨부사진",
    "첨부파일",
    "지도",
    "만족도조사여부",
    "별점",
    "별점사유",
    "감시목록",
    "synced_at",
    "보완횟수",
    "보완_미응답",
    "보완_요청자",
    "보완_요청일시",
    "보완_완료일시",
    "보완_요청_내용",
    "보완_신고자_의견",
    "사진_첫촬영",
    "사진_끝촬영",
    "사진_촬영수",
]

_MAP_MISSING_COLUMNS = list(dict.fromkeys(_REPORT_FIELDS + [
    "주소정규화",
    "행정구역",
    "위도",
    "경도",
]))


def _sanitize_jsonable(value):
    if isinstance(value, dict):
        return {key: _sanitize_jsonable(inner) for key, inner in value.items()}
    if isinstance(value, list):
        return [_sanitize_jsonable(inner) for inner in value]
    if isinstance(value, tuple):
        return [_sanitize_jsonable(inner) for inner in value]
    if pd.isna(value):
        return None
    return value


def _row_to_dict(row) -> dict:
    data = {}
    for field in _REPORT_FIELDS:
        value = row.get(field, "")
        if pd.isna(value):
            value = ""
        data[field] = value
    data["ID"] = str(data["ID"])
    data["결과"] = data["처리상태"]
    return data


def _text_or_empty(value) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()


def _int_or_default(value, default: int = -1) -> int:
    if value is None or pd.isna(value):
        return default
    if isinstance(value, str) and not value.strip():
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default


def _recent_answer_sort_key(item):
    synced_at = _int_or_default(item.get("synced_at"))
    report_number = _text_or_empty(item.get("신고번호"))
    response_date = _text_or_empty(item.get("답변일"))
    if synced_at >= 0:
        return (1, synced_at, report_number, "")
    return (0, response_date, report_number, "")


_normalize_mode = duplicate_group_service.normalize_mode


def _ratio_item(label: str, count: int, total: int) -> dict:
    safe_total = max(int(total), 0)
    safe_count = max(int(count), 0)
    return {
        "label": label,
        "count": safe_count,
        "pct": round((safe_count / safe_total) * 100, 1) if safe_total > 0 else 0,
    }


_UNASSIGNED_PERSON_VALUES = {"", "미지정"}


def _first_nonempty_value(group_df: pd.DataFrame, column: str) -> str:
    if column not in group_df.columns:
        return ""
    series = group_df[column].fillna("").astype(str)
    for value in series:
        text = value.strip()
        if text:
            return text
    return ""


def _normalize_map_category_value(category: str | None) -> str:
    normalized = (category or "all").strip().lower()
    return normalized if normalized in {"all", "traffic", "parking", "other"} else "all"


def _is_finite_number(value) -> bool:
    if value is None or pd.isna(value):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


_MAP_TARGET_KEYS = ("targetAgency", "targetPerson", "targetAgencyKey", "completedOnly")
