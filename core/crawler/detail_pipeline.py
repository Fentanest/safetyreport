from __future__ import annotations

import pandas as pd

import settings.settings as settings
from core.database.database import category_from_entry_value
from core.utils import logger


DETAIL_COLUMNS = [
    "ID",
    "처리상태",
    "차량번호",
    "위반법규",
    "범칙금_과태료",
    "벌점",
    "처리기관",
    "담당자",
    "답변일",
    "발생일자",
    "발생시각",
    "위반장소",
    "주소정규화",
    "행정구역",
    "위도",
    "경도",
    "지오코딩상태",
    "종결여부",
    "신고내용",
    "처리내용",
    "지도",
    "첨부사진",
    "첨부파일",
    "보완횟수",
    "보완_미응답",
    "보완_요청자",
    "보완_요청일시",
    "보완_완료일시",
    "보완_요청_내용",
    "보완_신고자_의견",
]


def build_detail_dataframe(report_id: str, details: dict):
    supplement = details.get("supplement_summary") or {}
    row_data = [
        report_id,
        details["processing_status"],
        details["car_number"],
        details["violation_law"],
        details["penalty_amount"],
        details["penalty_points"],
        details["processing_agency"],
        details["person_in_charge"],
        details["response_date"],
        details["occurrence_date"],
        details["occurrence_time"],
        details["violation_location"],
        "",
        "",
        None,
        None,
        "",
        details["processing_finish"],
        details["report_content"],
        details["processing_content"],
        details["map_image"],
        details["attached_photos"],
        details["attachment_files"],
        int(supplement.get("count") or 0),
        supplement.get("is_open") or "N",
        supplement.get("requester") or "",
        supplement.get("requested_at") or "",
        supplement.get("completed_at") or "",
        supplement.get("request_text") or "",
        supplement.get("reporter_opinion") or "",
    ]
    logger.LoggerFactory.logbot.info(row_data)
    return pd.DataFrame([row_data], columns=DETAIL_COLUMNS)


def enrich_title_fields_with_satisfaction(
    title_fields: dict | None,
    *,
    satisfaction_client,
    satisfaction_fetcher,
):
    current = dict(title_fields or {})
    if current.get("만족도조사여부") != "참여 완료" or not settings.phone_number:
        return current

    spp_no = current.get("신고번호", "")
    lookup_result = satisfaction_fetcher(satisfaction_client, spp_no)
    score, cause = lookup_result
    if score:
        current["별점"] = score
        current["별점사유"] = cause
        return current

    if not getattr(lookup_result, "confirmed", False):
        logger.LoggerFactory.logbot.warning(
            f"[satisfaction] {spp_no} 조회 실패 -> 기존 '참여 완료' 상태 유지"
        )
        return current

    current["만족도조사여부"] = "참여 가능"
    current["별점"] = None
    current["별점사유"] = ""
    logger.LoggerFactory.logbot.info(
        f"[satisfaction] {spp_no} 미참여 확인 -> '참여 가능'으로 재분류"
    )
    return current


def build_detail_result(
    report_id: str,
    details: dict,
    *,
    progress_status: str,
    satisfaction_client,
    satisfaction_fetcher,
):
    entry_value = details.get("entry_value", "")
    category = category_from_entry_value(entry_value)
    raw_content = details.get("raw_content", "")
    raw_type = details.get("raw_type", "")
    title_fields = enrich_title_fields_with_satisfaction(
        details.get("title_fields"),
        satisfaction_client=satisfaction_client,
        satisfaction_fetcher=satisfaction_fetcher,
    )
    return (
        build_detail_dataframe(report_id, details),
        category,
        entry_value,
        progress_status,
        title_fields,
        raw_content,
        raw_type,
    )
