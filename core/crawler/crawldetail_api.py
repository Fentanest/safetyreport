"""API 방식 신고 상세 크롤러.

기본 경로:
- direct_login(curl_cffi + Bearer 토큰) 기반 API 호출

fallback 경로:
- Selenium driver의 로그인 세션을 이용한 브라우저 컨텍스트($.get) API 호출
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
    try:
        r = direct_login.request_with_retry(session, "GET", url, timeout=20)
    except Exception as e:
        return {"error": f"network: {e}"}
    if r.status_code == 401:
        direct_login.get_valid_token(force_refresh=True)
        new_session, _ = direct_login.make_authorized_session()
        try:
            r = direct_login.request_with_retry(new_session, "GET", url, timeout=20)
        except Exception as e:
            return {"error": f"network after relogin: {e}"}
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}"}
    try:
        return r.json()
    except Exception as e:
        return {"error": f"JSON parse: {e}"}


def _ensure_browser_context(driver):
    if driver is None:
        raise RuntimeError("브라우저 API fallback에는 Selenium driver가 필요합니다.")
    if "safetyreport.go.kr" not in (driver.current_url or ""):
        driver.get(settings.myreporturl)
        sleep(2)


def _fetch_detail_via_browser(driver, c_no):
    script = """
    var callback = arguments[arguments.length - 1];
    var reportId = arguments[0];
    $.get('/api/v1/portal/mypage/mysafereport/' + reportId)
     .done(function(data) { callback(data); })
     .fail(function(jqXHR, textStatus, errorThrown) {
        callback({error: textStatus + ' ' + errorThrown});
     });
    """
    return driver.execute_async_script(script, str(c_no))


def crawl_details(driver=None, list=None, browser_fallback: bool = False):
    if list is None:
        list = []

    if browser_fallback:
        logger.LoggerFactory.logbot.info("API 방식으로 상세 데이터를 호출합니다. (Selenium 브라우저 fallback)")
        _ensure_browser_context(driver)
        fetch_detail = lambda link: _fetch_detail_via_browser(driver, link)
        satisfaction_client = driver
    else:
        logger.LoggerFactory.logbot.info("API 방식으로 상세 데이터를 호출합니다. (direct_login 세션)")
        session, _ = direct_login.make_authorized_session()
        fetch_detail = lambda link: _fetch_detail(session, link)
        satisfaction_client = session

    for link in list:
        logger.LoggerFactory.logbot.debug(f"[API] Fetching details for ID: {link}")

        data = fetch_detail(link)
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
                score, cause = satisfaction_fetcher.fetch_score_via_api(satisfaction_client, spp_no)
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
