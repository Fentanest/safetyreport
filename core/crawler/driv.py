from selenium import webdriver
from selenium.webdriver.chrome.webdriver import WebDriver as ChromeWebDriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import settings.settings as settings
from core.utils import logger
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
import platform
import os
import shutil
import sys


def _arm64_service():
    """ARM64(Raspberry Pi 등)에서 시스템 chromium-driver 경로를 반환."""
    candidates = [
        '/usr/bin/chromedriver',
        '/usr/lib/chromium/chromedriver',
        '/usr/lib/chromium-browser/chromedriver',
    ]
    path = next((p for p in candidates if os.path.isfile(p)), None)
    if path is None:
        path = shutil.which('chromedriver')
    if path:
        logger.LoggerFactory.logbot.info(f"ARM64: 시스템 chromedriver 사용 → {path}")
        return Service(path, env=_get_clean_env())
    logger.LoggerFactory.logbot.warning("ARM64: 시스템 chromedriver 미발견, webdriver_manager로 시도합니다.")
    return None


def _get_service():
    machine = platform.machine().lower()
    if ('aarch64' in machine or 'arm64' in machine) and os.name != 'nt':
        svc = _arm64_service()
        if svc:
            return svc
    return Service(ChromeDriverManager().install(), env=_get_clean_env())


def _get_clean_env():
    """PyInstaller 환경 변수를 제거하여 시스템 바이너리(브라우저)가 시스템 라이브러리를 사용하도록 함."""
    env = os.environ.copy()
    # PyInstaller로 패키징된 환경에서 실행 중인 경우
    if getattr(sys, 'frozen', False):
        # 시스템 브라우저가 앱 번들의 라이브러리 대신 시스템 라이브러리를 찾도록 유도
        for var in ['LD_LIBRARY_PATH', 'LIBPATH', 'SHLIB_PATH', 'PYTHONPATH']:
            if var in env:
                del env[var]
    return env


def create_driver():
    options = webdriver.ChromeOptions()
    # settings.headless 값에 따라 헤드리스 모드 적용
    if settings.headless:
        options.add_argument("--headless=new")
    
    options.add_argument("--no-sandbox")
    options.add_argument("--incognito")
    options.add_argument("--nogpu")
    options.add_argument("--disable-gpu")
    options.add_argument("--enable-javascript")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    options.add_argument('--disable-blink-features=AutomationControlled')
    
    # ARM64(Raspberry Pi): 시스템 chromium 바이너리 경로 설정
    machine = platform.machine().lower()
    if ('aarch64' in machine or 'arm64' in machine) and os.name != 'nt':
        for bin_path in ['/usr/bin/chromium', '/usr/bin/chromium-browser']:
            if os.path.isfile(bin_path):
                options.binary_location = bin_path
                logger.LoggerFactory.logbot.info(f"ARM64: chromium 바이너리 → {bin_path}")
                break

    mode = getattr(settings, 'chrome_mode', 'hub')
    if mode == 'desktop':
        logger.LoggerFactory.logbot.info("데스크톱 크롬을 사용합니다.")
        service = _get_service()
        driver = ChromeWebDriver(service=service, options=options)
    elif mode == 'remote':
        remote_val = str(settings.remote_debug_port).strip()
        if ':' in remote_val:
            debug_addr = remote_val
        else:
            debug_addr = f"127.0.0.1:{remote_val}"

        logger.LoggerFactory.logbot.info(f"원격 디버깅 모드 통신 (주소: {debug_addr})")
        options.add_experimental_option("debuggerAddress", debug_addr)
        service = _get_service()
        driver = ChromeWebDriver(service=service, options=options)
    else: # hub
        logger.LoggerFactory.logbot.info(f"Selenium Hub를 사용합니다: {settings.remotepath}")
        driver = webdriver.Remote(command_executor=settings.remotepath, options=options)
        
    driver.maximize_window()
    driver.get("https://www.whatismybrowser.com/detect/what-is-my-user-agent/")
    
    user_agent_element = None
    try:
        user_agent_element = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.ID, 'detected_value'))
        ) # User_agent 값 추출
    except Exception as e:
        logger.LoggerFactory.logbot.warning(f"User agent를 가져오는 데 실패했습니다: {e}")

    if user_agent_element:
        logger.LoggerFactory.logbot.debug(f"before: {user_agent_element.text}")
        user_agent_text = user_agent_element.text.replace("HeadlessChrome","Chrome")
        logger.LoggerFactory.logbot.debug(f"after: {user_agent_text}")
        options.add_argument(f'user-agent={user_agent_text}')

    return driver