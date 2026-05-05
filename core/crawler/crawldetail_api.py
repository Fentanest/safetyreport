"""API 방식 신고 상세 크롤러.

기본 경로:
- direct_login(curl_cffi + Bearer 토큰) 기반 API 호출

fallback 경로:
- Selenium driver의 로그인 세션을 이용한 브라우저 컨텍스트($.get) API 호출
"""
from time import sleep
from core.utils import logger
import services.parser as doc_parser
from services import satisfaction_fetcher
from core.crawler import direct_login
from core.crawler.api_client import ensure_browser_context, get_authorized_json
from core.crawler.detail_pipeline import build_detail_result


_DETAIL_URL = "https://www.safetyreport.go.kr/api/v1/portal/mypage/mysafereport"


def _fetch_detail(session, c_no):
    url = f"{_DETAIL_URL}/{c_no}"
    payload, session = get_authorized_json(session, url, timeout=20)
    return payload, session


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


def crawl_details(driver=None, report_ids=None, browser_fallback: bool = False):
    if report_ids is None:
        report_ids = []

    if browser_fallback:
        logger.LoggerFactory.logbot.info("API 방식으로 상세 데이터를 호출합니다. (Selenium 브라우저 fallback)")
        ensure_browser_context(driver)
        fetch_detail = lambda link: _fetch_detail_via_browser(driver, link)
        satisfaction_client = driver
    else:
        logger.LoggerFactory.logbot.info("API 방식으로 상세 데이터를 호출합니다. (direct_login 세션)")
        session, _ = direct_login.make_authorized_session()
        def fetch_detail(link):
            nonlocal session
            payload, session = _fetch_detail(session, link)
            return payload
        satisfaction_client = session

    for link in report_ids:
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

            yield build_detail_result(
                link,
                details,
                progress_status=details.get("progress_status", ""),
                satisfaction_client=satisfaction_client,
                satisfaction_fetcher=satisfaction_fetcher.fetch_score_via_api,
            )
            sleep(0.3)

        except Exception as e:
            logger.LoggerFactory.logbot.error(f"Error processing link {link} via API: {e}")
            continue
