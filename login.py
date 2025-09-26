from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import datetime
import settings.settings as settings
from time import sleep
import logger

def login_mysafety(driver):
    attemps = 0
    while attemps <= int(settings.max_retry_attemps):
        try:
            driver.get(settings.loginurl)
            driver.save_screenshot(f'./logs/{str(datetime.datetime.now()).replace(":","_")[:19]}_.png')
            ## 로그인
            id_input = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.NAME, 'username'))
                )
            id_input.send_keys(settings.username)

            pw_input = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.NAME, 'password'))
                )
            pw_input.send_keys(settings.password)

            driver.execute_script("javascript:LoginUtil.login(1);")
            logger.LoggerFactory.logbot.debug("로그인 자바스크립트 실행")
            sleep(5)
            driver.save_screenshot(f'./logs/{str(datetime.datetime.now()).replace(":","_")[:19]}_.png')
            break
        except:
            logger.LoggerFactory.logbot.warning("로그인 창 접속 불가")
            sleep(int(settings.retry_interval))
            attemps += 1