"""API 방식 신고 목록 크롤러.

기본 경로:
- direct_login(curl_cffi + Bearer 토큰) 기반 API 호출

fallback 경로:
- Selenium driver의 로그인 세션을 이용한 브라우저 컨텍스트($.get) API 호출
"""
import datetime
import re
import threading
from time import sleep
import pandas as pd
import settings.settings as settings
from core.utils import logger
from services.parser import _C_NOW_STATUS
from core.crawler import direct_login
from core.crawler.api_client import ensure_browser_context, get_authorized_json
from core.crawler.title_pipeline import build_title_dataframe


_LIST_URL = "https://www.safetyreport.go.kr/api/v1/portal/mypage/mysafereport"

# 공식 목록 API 제한(2026-10-24 실측, 응답 `request_limited`): 조회 기간 최대 1년(`list_date_range_too_large`),
# 한 번에 최대 50건(`list_page_size_too_large`). 아래 함수들은 전체 목록의 행 번호를 받아 연도 구간·50건 단위로
# 나눠 받은 뒤 예전 한 번 조회와 같은 모양(`result` 최신순, `totalCnt` 전체 건수)으로 이어 붙인다.
_PAGE_LIMIT = 50
_START_YEAR = 2014
_INNER_DELAY = 0.2  # 한 페이지(최대 200건)를 나눠 받는 요청 사이 간격(초)


def _list_windows(today: datetime.date | None = None, years=None) -> list[tuple[str, str]]:
    """2014-01-01 ~ 오늘을 달력 연도 구간으로 나눈다(최신 연도 먼저 — 사이트의 최신순과 같은 이어 붙임 순서).

    years 를 주면 그 연도 구간만 남긴다(DB 에 없는 신고번호를 찾을 때)."""
    today = today or datetime.date.today()
    return [
        (f"{y}-01-01", today.isoformat() if y == today.year else f"{y}-12-31")
        for y in range(today.year, _START_YEAR - 1, -1)
        if years is None or y in years
    ]


_REPORT_NUMBER = re.compile(r"^(?:SPP-)?(\d{2})(\d{2})-\d+$")


def report_number_years(numbers) -> set[int] | None:
    """신고번호(`SPP-YYMM-…`, 접두어 없이도)들의 접수 연도. 번호의 YY 는 신고일 연도와 같다(2026-10-24 실측 3,162건 전부).

    12월·1월 번호는 연말·연초 경계를 대비해 이웃 연도도 넣는다. 하나라도 형식이 아니면 None — 호출자는 목록 전체를 받는다."""
    years: set[int] = set()
    for number in numbers:
        m = _REPORT_NUMBER.match(str(number).strip().upper())
        if not m or not 1 <= int(m.group(2)) <= 12:
            return None
        year, month = 2000 + int(m.group(1)), int(m.group(2))
        years.add(year)
        if month == 12:
            years.add(year + 1)
        if month == 1:
            years.add(year - 1)
    return years or None


def _payload_problem(payload) -> bool:
    if not isinstance(payload, dict) or "error" in payload or not isinstance(payload.get("result"), list):
        return True
    total = payload.get("totalCnt")
    return isinstance(total, bool) or not isinstance(total, int) or total < 0


class _WindowedList:
    """구간별 전체 건수를 한 번 읽어 두고, 전체 행 번호를 (구간, 구간 안 행 번호)로 나눠 받는다."""

    def __init__(self, today: datetime.date | None = None, years=None):
        self.windows = _list_windows(today, years)
        self.totals: list[int] | None = None

    def page(self, fetch_window, start_row: int, end_row: int):
        requests = 0

        def fetch(window, a, b):
            nonlocal requests
            if requests and _INNER_DELAY:
                sleep(_INNER_DELAY)
            requests += 1
            return fetch_window(window, a, b)

        if self.totals is None:
            totals = []
            for window in self.windows:
                payload = fetch(window, 1, 1)
                if _payload_problem(payload):
                    return payload if isinstance(payload, dict) else {"error": "invalid payload"}
                totals.append(payload["totalCnt"])
            self.totals = totals
        rows: list = []
        offset = 0
        for i, window in enumerate(self.windows):
            total = self.totals[i]
            a, hi = max(start_row, offset + 1), min(end_row, offset + total)
            while a <= hi:
                b = min(a + _PAGE_LIMIT - 1, hi)
                payload = fetch(window, a - offset, b - offset)
                if _payload_problem(payload):
                    return payload if isinstance(payload, dict) else {"error": "invalid payload"}
                if payload["totalCnt"] != total:
                    # 받는 사이 그 연도의 건수가 바뀌었다 — 바뀐 전체 건수를 돌려줘 호출부의 페이지 검증이 실패로 잡게 한다.
                    changed = sum(self.totals) - total + payload["totalCnt"]
                    self.totals = None
                    return {"result": rows + list(payload["result"]), "totalCnt": changed}
                rows.extend(payload["result"])
                a = b + 1
            offset += total
        return {"result": rows, "totalCnt": offset}


def _window_params(window, start_row, end_row) -> dict:
    return {
        "startRowNum": start_row,
        "endRowNum": end_row,
        "C_FRM_DATE": window[0],
        "C_TO_DATE": window[1],
        "state": "",
        "seachType": "tit",
        "C_RELATION2": 1,
        "searchKeyWord": "",
    }


# 서버 프로세스 안의 1회 전송 목록 확인(스레드)과 다른 수집이 겹쳐도 구간 건수를 서로 덮어쓰지 않게 스레드마다 둔다.
_listing_state = threading.local()


def _listing(kind: str, start_row: int, years=None) -> _WindowedList:
    """크롤 한 번은 항상 1행부터 시작한다 — 그때 구간 건수를 새로 읽는다."""
    listing = getattr(_listing_state, kind, None)
    if start_row == 1 or listing is None or listing.windows != _list_windows(years=years):
        listing = _WindowedList(years=years)
        setattr(_listing_state, kind, listing)
    return listing


def _fetch_api_page(session, start_row, end_row, years=None):
    holder = {"session": session}

    def fetch_window(window, a, b):
        payload, holder["session"] = get_authorized_json(
            holder["session"], _LIST_URL, params=_window_params(window, a, b), timeout=20)
        return payload

    payload = _listing("direct", start_row, years).page(fetch_window, start_row, end_row)
    return payload, holder["session"]


def _fetch_api_page_via_browser(driver, start_row, end_row, years=None):
    def fetch_window(window, a, b):
        script = f"""
    var callback = arguments[arguments.length - 1];
    $.get('/api/v1/portal/mypage/mysafereport', {{
        startRowNum: {a},
        endRowNum: {b},
        C_FRM_DATE: '{window[0]}',
        C_TO_DATE: '{window[1]}',
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

    return _listing("browser", start_row, years).page(fetch_window, start_row, end_row)


def crawl_titles(driver=None, page_range=None, browser_fallback: bool = False, progress: dict | None = None,
                 session_override=None, stopped=None, years=None):
    """반환은 그대로 ([df...], 마지막 페이지). progress dict 를 주면 수집 경과를 채운다.
    years 를 주면 그 연도 목록만 받는다(전체·페이지·list_ok 도 그 연도 기준).

    progress 키: total(총 건수·첫 페이지 실패면 None), pages_expected(받을 페이지 번호 목록),
    pages_ok(성공 페이지), pages_failed(실패·잘린 페이지), first_error, list_ok(전 페이지 성공).
    초기화 크롤(start.py --rebuild)이 전 페이지 성공 판정에 쓴다.
    """
    def _note(**fields):
        if progress is not None:
            progress.update(fields)

    if browser_fallback:
        logger.LoggerFactory.logbot.info("API 방식으로 목록 데이터를 호출합니다. (Selenium 브라우저 fallback)")
        ensure_browser_context(driver)
        if years is None:
            fetch_page = lambda start_row, end_row: _fetch_api_page_via_browser(driver, start_row, end_row)
        else:
            fetch_page = lambda start_row, end_row: _fetch_api_page_via_browser(driver, start_row, end_row, years)
    else:
        logger.LoggerFactory.logbot.info("API 방식으로 목록 데이터를 호출합니다. (direct_login 세션)")
        session = session_override
        if session is None:
            session, _ = direct_login.make_authorized_session()
        def fetch_page(start_row, end_row):
            nonlocal session
            if years is None:
                payload, session = _fetch_api_page(session, start_row, end_row)
            else:
                payload, session = _fetch_api_page(session, start_row, end_row, years)
            return payload

    all_title_dfs = []
    page_size = 200
    try:
        first_payload = fetch_page(1, page_size)
    except Exception as exc:
        logger.LoggerFactory.logbot.error(f"API 목록 첫 페이지 호출 실패: {exc}")
        _note(total=None, pages_expected=[], pages_ok=[], pages_failed=[1],
              first_error=f"network: {exc}", list_ok=False)
        return [], 0
    if not isinstance(first_payload, dict):
        first_payload = {"error": "invalid payload"}
    if "error" in first_payload or not isinstance(first_payload.get("result"), list):
        logger.LoggerFactory.logbot.error(f"API 목록 호출 실패: {first_payload.get('error', 'No result')}")
        _note(total=None, pages_expected=[], pages_ok=[], pages_failed=[1],
              first_error=str(first_payload.get("error", "No result")), list_ok=False)
        return [], 0

    tot_cnt = first_payload.get("totalCnt")
    if isinstance(tot_cnt, bool) or not isinstance(tot_cnt, int) or tot_cnt < 0 or (
            tot_cnt == 0 and first_payload['result']):
        _note(total=None, pages_expected=[], pages_ok=[], pages_failed=[1],
              first_error="invalid totalCnt", list_ok=False)
        return [], 0
    if tot_cnt == 0:
        logger.LoggerFactory.logbot.warning("조회된 신고 내역이 없습니다.")
        _note(total=0, pages_expected=[], pages_ok=[], pages_failed=[],
              first_error=None, list_ok=True)
        return [], 0

    last_page_num = (tot_cnt + page_size - 1) // page_size
    logger.LoggerFactory.logbot.info(f"API 확인됨: 총 {tot_cnt}건 ({last_page_num}페이지 분량)")

    last_crawled_page = 0
    pages_to_crawl = list(page_range) if page_range else list(range(1, last_page_num + 1))
    if len(set(pages_to_crawl)) != len(pages_to_crawl) or any(
            isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= last_page_num
            for page in pages_to_crawl):
        raise ValueError("invalid page range")
    pages_ok: list[int] = []
    pages_failed: list[int] = []
    first_error: str | None = None
    seen_ids = set()

    for page_num in pages_to_crawl:
        if stopped is not None and stopped():
            _note(list_ok=False, pages_failed=[page_num], first_error='cancelled');return all_title_dfs,last_crawled_page
        last_crawled_page = page_num
        start_row = (page_num - 1) * page_size + 1
        end_row = min(page_num * page_size, tot_cnt)

        logger.LoggerFactory.logbot.info(f"API 목록 로드 중: {page_num} 페이지 ({start_row}~{end_row}건)")

        try:
            payload = first_payload if page_num == 1 else fetch_page(start_row, end_row)
        except Exception as exc:
            logger.LoggerFactory.logbot.warning(f"페이지 {page_num} 호출 실패: {exc}")
            pages_failed.append(page_num)
            if first_error is None:
                first_error = f"network: {exc}"
            continue
        if not isinstance(payload, dict) or 'error' in payload or not isinstance(payload.get('result'), list):
            pages_failed.append(page_num)
            first_error = first_error or 'invalid page payload'
            continue
        results = payload['result']
        page_valid = payload.get('totalCnt') == tot_cnt and len(results) == end_row - start_row + 1

        page_dfs = []
        for item in results:
            if not isinstance(item, dict) or item.get('C_NO') in (None, '') or str(item['C_NO']) in seen_ids:
                page_valid = False
                continue
            c_no = str(item.get("C_NO", ""))
            seen_ids.add(c_no)
            spp_no = item.get("STTEMNT_NO", "")
            title_full = str(item.get("C_A_TITLE") or "")
            title = title_full.split(")", 1)[-1].strip() if ")" in title_full else title_full.strip()
            date = item.get("C_DATE", "")
            c_now = item.get("C_NOW", 0)
            try:
                score = int(item.get("STSFDG_SCORE") or 0)
            except (TypeError, ValueError, OverflowError):
                page_valid = False
                continue
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
        if page_valid:
            pages_ok.append(page_num)
        else:
            pages_failed.append(page_num)
            first_error = first_error or 'short, repeated, malformed or changed-total page'
        sleep(0.5)

    if page_range:
        expected = list(page_range)
        list_ok = not pages_failed and pages_ok == expected
    else:
        expected = list(range(1, last_page_num + 1))
        list_ok = not pages_failed and pages_ok == expected
    _note(total=tot_cnt, pages_expected=expected, pages_ok=pages_ok,
          pages_failed=pages_failed, first_error=first_error, list_ok=list_ok)
    return all_title_dfs, last_crawled_page
