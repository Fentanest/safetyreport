from selenium import webdriver
from selenium.webdriver.chrome.webdriver import WebDriver as ChromeWebDriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import settings.settings as settings
from core.utils import logger
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

def create_driver():
    options = webdriver.ChromeOptions()
    # options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--incognito")
    options.add_argument("--nogpu")
    options.add_argument("--disable-gpu")
    options.add_argument("--enable-javascript")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    options.add_argument('--disable-blink-features=AutomationControlled')
    
    mode = getattr(settings, 'chrome_mode', 'hub')
    if mode == 'desktop':
        logger.LoggerFactory.logbot.info("데스크톱 크롬을 사용합니다.")
        service = Service(ChromeDriverManager().install())
        driver = ChromeWebDriver(service=service, options=options)
    elif mode == 'remote':
        remote_val = str(settings.remote_debug_port).strip()
        if ':' in remote_val:
            debug_addr = remote_val
        else:
            debug_addr = f"127.0.0.1:{remote_val}"
            
        logger.LoggerFactory.logbot.info(f"원격 디버깅 모드 통신 (주소: {debug_addr})")
        options.add_experimental_option("debuggerAddress", debug_addr)
        service = Service(ChromeDriverManager().install())
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