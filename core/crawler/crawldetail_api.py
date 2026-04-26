"""API 방식 신고 상세 크롤러 (Selenium 불필요).

기존: Selenium driver의 jQuery 컨텍스트로 $.get 호출
변경: curl_cffi + Bearer 토큰으로 직접 호출 → driver 불필요
"""
import pandas as pd
from time import sleep
import settings.settings as settings
from core.utils import logger
import services.parser as doc_parser
from services import satisfaction_fetcher
from core.crawler import direct_login


_DETAIL_URL = "https://www.safetyreport.go.kr/api/v1/portal/mypage/mysafereport"


def _fetch_detail(session, c_no):
    url = f"{_DETAIL_URL}/{c_no}"
    r = session.get(url, timeout=20)
    if r.status_code == 401:
        direct_login.get_valid_token(force_refresh=True)
        new_session, _ = direct_login.make_authorized_session()
        r = new_session.get(url, timeout=20)
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}"}
    try:
        return r.json()
    except Exception as e:
        return {"error": f"JSON parse: {e}"}


def crawl_details(driver=None, list=None):
    """driver 인자는 호환성 유지용 — 무시됨 (API 방식은 driver 불필요)."""
    if list is None:
        list = []

    session, _ = direct_login.make_authorized_session()

    for link in list:
        logger.LoggerFactory.logbot.debug(f"[API] Fetching details for ID: {link}")

        data = _fetch_detail(session, link)
        if "error" in data or "result" not in data:
            logger.LoggerFactory.logbot.error(
                f"Error fetching JSON API for {link}: {data.get('error', 'No result key')}"
            )
            continue

        try:
            result_data = data["result"]
            details = doc_parser.parse_json_details(result_data)

            cols = ["ID", "처리상태", "차량번호", "위반법규", "범칙금_과태료", "벌점",
                    "처리기관", "담당자", "답변일", "발생일자", "발생시각", "위반장소",
                    "종결여부", "신고내용", "처리내용", "지도", "첨부사진", "첨부파일"]

            detaillist = [
                link,
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
                details["processing_finish"],
                details["report_content"],
                details["processing_content"],
                details["map_image"],
                details["attached_photos"],
                details["attachment_files"],
            ]

            logger.LoggerFactory.logbot.info(str(detaillist))
            df = pd.DataFrame([detaillist], columns=cols)
            entry_value = details.get("entry_value", "")
            from core.database.database import category_from_entry_value
            category = category_from_entry_value(entry_value)

            # 만족도조사 점수+사유 보강 (참여 완료로 판정된 건 한정)
            title_fields = details.get("title_fields") or {}
            if title_fields.get("만족도조사여부") == "참여 완료" and settings.phone_number:
                spp_no = title_fields.get("신고번호", "")
                score, cause = satisfaction_fetcher.fetch_score_via_api(session, spp_no)
                if score:
                    title_fields["별점"] = score
                    title_fields["별점사유"] = cause
                else:
                    title_fields["만족도조사여부"] = "참여 가능"
                    title_fields["별점"] = None
                    title_fields["별점사유"] = ""
                    logger.LoggerFactory.logbot.info(
                        f"[satisfaction] {spp_no} 미참여 확인 → '참여 가능'으로 재분류"
                    )

            yield (df, category, entry_value, details.get("progress_status", ""), title_fields)
            sleep(0.3)

        except Exception as e:
            logger.LoggerFactory.logbot.error(f"Error processing link {link} via API: {e}")
            continue
