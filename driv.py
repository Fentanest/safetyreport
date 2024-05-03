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
    # options.binary_location = '/usr/bin/google-chrome-stable'
    driver = webdriver.Remote(command_executor=settings.remotepath, options=options)
    # driver.set_window_size(1920, 1080)
    driver.maximize_window()
    driver.get("https://www.whatismybrowser.com/detect/what-is-my-user-agent/")
    try:
        user_agent = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.ID, 'detected_value'))
            ) # User_agent 값 추출
    except:
        pass
    
    logger.LoggerFactory.logbot.debug("before:", user_agent.text)
    user_agent = user_agent.text.replace("HeadlessChrome","Chrome")
    logger.LoggerFactory.logbot.debug("after: ", user_agent)
    options.add_argument(f'user-agent={user_agent}')
    return(driver)