from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import settings.settings as settings
import logger

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