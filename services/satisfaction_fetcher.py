"""만족도조사 점수+사유 조회 모듈.

API 방식: /api/v1/portal/statistics/satisfactionstatistics/score/{spp}/{phone}
레거시 방식: Selenium으로 comptSatisfaction.html 진입 → DOM 추출

반환: (score: int|None, cause: str)
  - score: 1~5. 미참여/조회 실패 시 None
  - cause: 불만족 사유 텍스트 (없으면 빈 문자열)
"""

from typing import Optional, Tuple
import settings.settings as settings
from core.utils import logger

_API_URL = "https://www.safetyreport.go.kr/api/v1/portal/statistics/satisfactionstatistics/score/{spp}/{phone}"
_PAGE_URL = "https://www.safetyreport.go.kr/html/common/popup/comptSatisfaction.html?seq={spp}&pn={phone}"


_SCORE_URL = "https://www.safetyreport.go.kr/api/v1/portal/statistics/satisfactionstatistics/score/{spp}/{phone}"


def fetch_score_via_api(session_or_driver, spp_no: str) -> Tuple[Optional[int], str]:
    """만족도조사 점수+사유 조회.

    session_or_driver:
      - curl_cffi Session 인스턴스 (driver-free 호출, 권장)
      - Selenium driver 객체 (레거시 호환 — execute_async_script로 대체)
    """
    phone = settings.phone_number
    if not phone or not spp_no:
        return None, ""

    # curl_cffi 세션 모드 — get 메서드 보유 + execute_async_script 미보유로 식별
    if hasattr(session_or_driver, "get") and not hasattr(session_or_driver, "execute_async_script"):
        try:
            url = _SCORE_URL.format(spp=spp_no, phone=phone)
            r = session_or_driver.get(url, timeout=10)
            if r.status_code != 200:
                return None, ""
            data = r.json()
            if not data or "result" not in data or not data["result"]:
                return None, ""
            score_raw = data["result"].get("STSFDG_SCORE", 0)
            score = int(score_raw) if score_raw else 0
            cause = (data["result"].get("STSFDG_CAUSE") or "").strip()
            return (score if score > 0 else None), cause
        except Exception as e:
            if logger.LoggerFactory.logbot:
                logger.LoggerFactory.logbot.debug(f"[satisfaction] HTTP 조회 실패 {spp_no}: {e}")
            return None, ""

    # 레거시: Selenium driver 모드 (jQuery 컨텍스트)
    script = """
    var callback = arguments[arguments.length - 1];
    var spp = arguments[0];
    var phone = arguments[1];
    $.get('/api/v1/portal/statistics/satisfactionstatistics/score/' + spp + '/' + phone)
     .done(function(data) { callback(data); })
     .fail(function(jqXHR, textStatus, errorThrown) { callback({error: textStatus + ' ' + errorThrown}); });
    """
    try:
        data = session_or_driver.execute_async_script(script, spp_no, phone)
        if not data or "error" in data or "result" not in data or not data["result"]:
            return None, ""
        score_raw = data["result"].get("STSFDG_SCORE", 0)
        score = int(score_raw) if score_raw else 0
        cause = (data["result"].get("STSFDG_CAUSE") or "").strip()
        return (score if score > 0 else None), cause
    except Exception as e:
        if logger.LoggerFactory.logbot:
            logger.LoggerFactory.logbot.debug(f"[satisfaction] API 조회 실패 {spp_no}: {e}")
        return None, ""


def fetch_score_via_selenium_page(driver, spp_no: str, timeout: int = 8) -> Tuple[Optional[int], str]:
    """레거시 백업 경로: 만족도 팝업 페이지를 띄워 DOM에서 점수+사유 추출.

    API 경로가 막혔을 때 사용. driver는 안전신문고에 이미 로그인된 세션을 권장하나,
    이 팝업 자체는 인증 불필요라 새 driver여도 작동."""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import TimeoutException

    phone = settings.phone_number
    if not phone or not spp_no:
        return None, ""
    url = _PAGE_URL.format(spp=spp_no, phone=phone)
    try:
        driver.get(url)
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input[name='STSFDG_SCORE']:checked"))
        )
        elem = driver.find_element(By.CSS_SELECTOR, "input[name='STSFDG_SCORE']:checked")
        value = elem.get_attribute("value")
        score = int(value) if value and value.isdigit() else 0
        cause = ""
        try:
            cause = driver.find_element(By.ID, "STSFDG_CAUSE").text.strip()
        except Exception:
            pass
        return (score if score > 0 else None), cause
    except TimeoutException:
        # 미참여 또는 본인이 아닌 신고건 → 어떤 라디오도 체크되지 않음
        return None, ""
    except Exception as e:
        logger.LoggerFactory.logbot.debug(f"[satisfaction] Selenium 조회 실패 {spp_no}: {e}")
        return None, ""
