from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import datetime
import settings.settings as settings
from time import sleep

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
            # login_button=driver.find_element(By.XPATH, '//input[@onclick="javascript:LoginUtil.login(1);"][@type="button"]')
            # login_button.click()
            sleep(5)
            driver.save_screenshot(f'./logs/{str(datetime.datetime.now()).replace(":","_")[:19]}_.png')
            print("로그인 성공")
            break
        except:
            print("로그인 창 접속 불가")
            sleep(int(settings.retry_interval))
            attemps += 1