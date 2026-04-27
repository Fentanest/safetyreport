"""API 방식 신고 목록 크롤러 (Selenium 불필요).

기존: Selenium driver의 jQuery 컨텍스트로 $.get 호출
변경: curl_cffi + Bearer 토큰으로 직접 호출 → driver 불필요

direct_login 모듈이 토큰 발급/갱신 담당.
"""
import datetime
from time import sleep
import pandas as pd
import settings.settings as settings
from core.utils import logger
from services.parser import _C_NOW_STATUS
from core.crawler import direct_login


_LIST_URL = "https://www.safetyreport.go.kr/api/v1/portal/mypage/mysafereport"


def _fetch_api_page(session, start_row, end_row):
    """API 직접 호출. session은 direct_login.make_authorized_session()의 결과.
    errno=104 등 일시 오류는 direct_login.request_with_retry가 silent 3회 재시도."""
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
    try:
        r = direct_login.request_with_retry(
            session, "GET", _LIST_URL, params=params, timeout=20
        )
    except Exception as e:
        return {"error": f"network: {e}", "result": []}
    if r.status_code == 401:
        # 토큰 만료 → 강제 갱신 후 1회 재시도
        direct_login.get_valid_token(force_refresh=True)
        new_session, _ = direct_login.make_authorized_session()
        try:
            r = direct_login.request_with_retry(
                new_session, "GET", _LIST_URL, params=params, timeout=20
            )
        except Exception as e:
            return {"error": f"network after relogin: {e}", "result": []}
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}", "result": []}
    return r.json()


def crawl_titles(driver=None, page_range=None):
    """driver 인자는 호환성 유지용 — 무시됨 (API 방식은 driver 불필요)."""
    logger.LoggerFactory.logbot.info("API 방식으로 목록 데이터를 호출합니다. (Selenium 미사용)")

    session, _ = direct_login.make_authorized_session()

    all_title_dfs = []
    page_size = 200
    cols = ["ID", "상태", "신고번호", "신고명", "신고일", "만족도조사여부", "감시목록"]

    first_payload = _fetch_api_page(session, 1, page_size)
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

        payload = _fetch_api_page(session, start_row, end_row)
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

            titlelist = [c_no, state, spp_no, title, date, poll_status, "N"]
            df = pd.DataFrame([titlelist], columns=cols)
            page_dfs.append(df)

        all_title_dfs.extend(page_dfs)
        sleep(0.5)

    return all_title_dfs, last_crawled_page
