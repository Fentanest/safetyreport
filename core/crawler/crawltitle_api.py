"""API 방식 신고 목록 크롤러.

기본 경로:
- direct_login(curl_cffi + Bearer 토큰) 기반 API 호출

fallback 경로:
- Selenium driver의 로그인 세션을 이용한 브라우저 컨텍스트($.get) API 호출
"""
import datetime
from time import sleep
import pandas as pd
import settings.settings as settings
from core.utils import logger
from services.parser import _C_NOW_STATUS
from core.crawler import direct_login
from core.crawler.api_client import ensure_browser_context, get_authorized_json
from core.crawler.title_pipeline import build_title_dataframe


_LIST_URL = "https://www.safetyreport.go.kr/api/v1/portal/mypage/mysafereport"

def _fetch_api_page(session, start_row, end_row):
    params = {
        "startRowNum": start_row,
        "endRowNum": end_row,
        "C_FRM_DATE": "2014-01-01",
        "C_TO_DATE": datetime.datetime.now().strftime("%Y-%m-%d"),
        "state": "",
        "seachType": "tit",
        "C_RELATION2": 1,
        "searchKeyWord": "",
    }
    payload, session = get_authorized_json(session, _LIST_URL, params=params, timeout=20)
    return payload, session


def _fetch_api_page_via_browser(driver, start_row, end_row):
    script = f"""
    var callback = arguments[arguments.length - 1];
    $.get('/api/v1/portal/mypage/mysafereport', {{
        startRowNum: {start_row},
        endRowNum: {end_row},
        C_FRM_DATE: '2014-01-01',
        C_TO_DATE: '{datetime.datetime.now().strftime("%Y-%m-%d")}',
        state: '',
        seachType: 'tit',
        C_RELATION2: 1,
        searchKeyWord: ''
    }})
    .done(function(data) {{ callback(data); }})
    .fail(function(jqXHR, textStatus, errorThrown) {{
        callback({{error: textStatus + ' ' + errorThrown, result: []}});
    }});
    """
    return driver.execute_async_script(script)


def crawl_titles(driver=None, page_range=None, browser_fallback: bool = False):
    if browser_fallback:
        logger.LoggerFactory.logbot.info("API 방식으로 목록 데이터를 호출합니다. (Selenium 브라우저 fallback)")
        ensure_browser_context(driver)
        fetch_page = lambda start_row, end_row: _fetch_api_page_via_browser(driver, start_row, end_row)
    else:
        logger.LoggerFactory.logbot.info("API 방식으로 목록 데이터를 호출합니다. (direct_login 세션)")
        session, _ = direct_login.make_authorized_session()
        def fetch_page(start_row, end_row):
            nonlocal session
            payload, session = _fetch_api_page(session, start_row, end_row)
            return payload

    all_title_dfs = []
    page_size = 200
    first_payload = fetch_page(1, page_size)
    if "error" in first_payload or "result" not in first_payload:
        logger.LoggerFactory.logbot.error(f"API 목록 호출 실패: {first_payload.get('error', 'No result')}")
        return [], 0

    tot_cnt = first_payload.get("totalCnt", 0)
    if tot_cnt == 0:
        logger.LoggerFactory.logbot.warning("조회된 신고 내역이 없습니다.")
        return [], 0

    last_page_num = (tot_cnt + page_size - 1) // page_size
    logger.LoggerFactory.logbot.info(f"API 확인됨: 총 {tot_cnt}건 ({last_page_num}페이지 분량)")

    last_crawled_page = 0
    pages_to_crawl = page_range if page_range else range(1, last_page_num + 1)

    for page_num in pages_to_crawl:
        last_crawled_page = page_num
        start_row = (page_num - 1) * page_size + 1
        end_row = min(page_num * page_size, tot_cnt)

        logger.LoggerFactory.logbot.info(f"API 목록 로드 중: {page_num} 페이지 ({start_row}~{end_row}건)")

        payload = fetch_page(start_row, end_row)
        results = payload.get("result", [])

        if not results:
            break

        page_dfs = []
        for item in results:
            c_no = str(item.get("C_NO", ""))
            spp_no = item.get("STTEMNT_NO", "")
            title_full = item.get("C_A_TITLE", "")
            title = title_full.split(")", 1)[-1].strip() if ")" in title_full else title_full.strip()
            date = item.get("C_DATE", "")
            c_now = item.get("C_NOW", 0)
            score = int(item.get("STSFDG_SCORE", 0))
            try:
                c_now = int(float(c_now))
            except Exception:
                pass

            state = _C_NOW_STATUS.get(c_now, str(c_now))

            poll_status = ""
            if score > 0:
                poll_status = "참여 완료"
            elif c_now in (10, 11, 14, 15):
                poll_status = "참여 가능"
            elif c_now == 20 or c_now == 30:
                poll_status = "참여 불가"
            else:
                poll_status = "답변 대기"

            page_dfs.append(
                build_title_dataframe(
                    c_no,
                    state,
                    spp_no,
                    title,
                    date,
                    poll_status,
                )
            )

        all_title_dfs.extend(page_dfs)
        sleep(0.5)

    return all_title_dfs, last_crawled_page
