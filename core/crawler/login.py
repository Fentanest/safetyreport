from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import datetime
import settings.settings as settings
from time import sleep
from core.utils import logger

_LOGIN_USERNAME_LOCATOR = (By.NAME, 'username')
_LOGIN_PASSWORD_LOCATOR = (By.NAME, 'password')
_MYREPORT_READY_LOCATORS = (
    (By.ID, "C_FRM_DATE"),
    (By.ID, settings.titletable),
)


def _has_element(driver, locator) -> bool:
    try:
        return bool(driver.find_elements(*locator))
    except Exception:
        return False


def _looks_like_login_page(driver) -> bool:
    try:
        current_url = (driver.current_url or "").lower()
    except Exception:
        current_url = ""

    return "#/main/login/login" in current_url or _has_element(driver, _LOGIN_USERNAME_LOCATOR)


def is_logged_in(driver) -> bool:
    try:
        current_url = (driver.current_url or "").lower()
        if "#/mypage/" in current_url or "#mypage/" in current_url:
            return True
    except Exception:
        pass

    for locator in _MYREPORT_READY_LOCATORS:
        if _has_element(driver, locator):
            return True

    return False


def wait_for_logged_in(driver, timeout: int = 15) -> bool:
    try:
        WebDriverWait(driver, timeout).until(lambda d: is_logged_in(d))
        return True
    except Exception:
        return is_logged_in(driver)


def _set_input_value(driver, element, value: str) -> None:
    element.clear()
    element.send_keys(value)
    if element.get_attribute("value") == value:
        return

    driver.execute_script(
        """
        const input = arguments[0];
        const value = arguments[1];
        input.focus();
        input.value = value;
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
        """,
        element,
        value,
    )


def login_mysafety(driver):
    settings._instance.load()
    if not settings.username or not settings.password:
        logger.LoggerFactory.logbot.error(
            f"로그인 자격증명이 비어 있습니다. config_path={settings.config_path}"
        )
        return False

    logger.LoggerFactory.logbot.info(
        f"설정 기반 Selenium 로그인 시도: config_path={settings.config_path}, username_set={bool(settings.username)}"
    )

    attemps = 0
    while attemps <= int(settings.max_retry_attemps):
        try:
            driver.get(settings.loginurl)
            ## 로그인
            id_input = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located(_LOGIN_USERNAME_LOCATOR)
                )
            _set_input_value(driver, id_input, settings.username)

            pw_input = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located(_LOGIN_PASSWORD_LOCATOR)
                )
            _set_input_value(driver, pw_input, settings.password)

            driver.execute_script("javascript:LoginUtil.login(1);")
            logger.LoggerFactory.logbot.debug("로그인 자바스크립트 실행")
            sleep(3)
            driver.get(settings.myreporturl)

            if wait_for_logged_in(driver, timeout=15):
                logger.LoggerFactory.logbot.info("Selenium UI 로그인 성공")
                return True

            logger.LoggerFactory.logbot.warning(
                f"로그인 후에도 마이페이지 인증 상태가 확인되지 않았습니다. current_url={driver.current_url}"
            )
        except Exception as e:
            logger.LoggerFactory.logbot.warning(f"로그인 창 접속 또는 로그인 처리 실패: {e}")
            sleep(int(settings.retry_interval))
            attemps += 1

    logger.LoggerFactory.logbot.error("Selenium UI 로그인 최종 실패")
    return False
