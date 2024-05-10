from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
import datetime
from time import sleep
import pandas as pd
import settings.settings as settings
import logger

def Crawling_title(driver):
    attemps = 0
    while attemps <= int(settings.max_retry_attemps):
        try:
            driver.get(settings.myreporturl)
            sleep(3)
            driver.save_screenshot(f'./logs/{str(datetime.datetime.now()).replace(":","_")[:19]}_.png')
            # response_code = driver.execute_script("return document.readyState")
            # print(response_code)
            break
        except:
            logger.LoggerFactory.logbot.warning("마이페이지 접속 불가")
            sleep(int(settings.retry_interval))
            attemps += 1

    ## 기간 시작일, 종료일 비우기
    start_date_path = WebDriverWait(driver, 60).until(
        EC.presence_of_element_located((By.ID, "C_FRM_DATE"))
        )
    start_date_path.clear() # 시작일 비우기
    sleep(1)
    start_date_path.send_keys(Keys.CONTROL + "a")  # 입력란의 내용을 선택합니다.
    sleep(1)
    start_date_path.send_keys(Keys.DELETE)  # 선택된 내용을 삭제합니다.
    logger.LoggerFactory.logbot.debug("검색 시작일 비우기")

    end_date_path = WebDriverWait(driver, 60).until(
        EC.presence_of_element_located((By.ID, "C_TO_DATE"))
        )
    end_date_path.clear() # 종료일 비우기
    sleep(1)
    end_date_path.send_keys(Keys.CONTROL + "a")  # 입력란의 내용을 선택합니다.
    sleep(1)
    end_date_path.send_keys(Keys.DELETE)  # 선택된 내용을 삭제합니다.
    logger.LoggerFactory.logbot.debug("검색 종료일 비우기")

    search_button = WebDriverWait(driver, 60).until(
        EC.presence_of_element_located((By.XPATH, "//button[@class='button btnSearch']"))
        )
    sleep(1)
    search_button.click()
    logger.LoggerFactory.logbot.debug("검색 버튼 클릭")

    ## 30개씩 보기
    page_size_select = WebDriverWait(driver, 60).until(
        EC.presence_of_element_located((By.ID, "pageSize"))
        )
    pageselect = Select(page_size_select)
    pageselect.select_by_value("30")
    logger.LoggerFactory.logbot.debug("30개씩 보기 선택 완료")
    sleep(3)

    ## 마지막 버튼 눌러 총 페이지 수 확인하기
    last_page_button = WebDriverWait(driver, 60).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, 'li.footable-page-nav a[title="마지막"]'))
        ) # 마지막 페이지
    last_page_button.click()
    logger.LoggerFactory.logbot.debug("마지막 페이지 버튼 클릭")
    driver.implicitly_wait(2)
    driver.refresh()
    driver.implicitly_wait(2)

    last_page_xpath = f'//*[@id="{settings.titletable}"]/tfoot/tr[@class="footable-paging"]/td/ul[@class="pagination"]/li[@class="footable-page visible active"]/a[@class="footable-page-link"]' # 마지막 페이지

    last_page = WebDriverWait(driver, 60).until(
        EC.presence_of_element_located((By.XPATH, last_page_xpath))
        )
    last_page_num = int(last_page.text)
    logger.LoggerFactory.logbot.debug(f"마지막 페이지 번호{last_page_num}")

    ## 처음 페이지로 이동
    first_page_button = driver.find_element(By.CSS_SELECTOR, 'li.footable-page-nav a[title="처음"]') # 처음 페이지
    first_page_button.click()
    logger.LoggerFactory.logbot.debug("처음 페이지로 복귀")

    ## 리스트 생성
    cols = ["ID", "상태", "신고번호", "신고명", "신고일"] # 컬럼명 생성
    titlelist = [] # Dataframe 변환을 위한 리스트 생성

    ## 크롤링 시작
    for i in range(last_page_num):
        driver.refresh()
        table = driver.find_element(By.ID, settings.titletable)
        tbody = table.find_element(By.TAG_NAME, 'tbody')
        tfoot = table.find_element(By.TAG_NAME, 'tfoot')
        rows = tbody.find_elements(By.TAG_NAME, 'tr')
        cNo = tbody.find_elements(By.CSS_SELECTOR, 'td.bbs_subject input[name="cNo"]')
        next_button = tfoot.find_element(By.CSS_SELECTOR, 'li.footable-page-nav a[title="다음"]') # 다음 페이지 버튼

        for index, row in enumerate(rows):
            link = cNo[index].get_attribute('value').strip() # 각 신고건마다 가진 value값
            property = row.find_elements(By.TAG_NAME, 'td') # 개별 행 데이터 리스트로 반환
            report = property[0].text.split(')') # 1열 데이터가 상태+번호+신고명 섞여있어 분리해야 함
            state = report[0].split('(')[0].strip() # 1열에서 상태 추출(진행, 답변완료 등)
            reportnumber = report[0].split('(')[1].strip() # 1열에서 신고번호 추출(SPP-)
            reporttitle = report[1].strip() # 1열에서 신고명 추출(진로변경 위반, 끼어들기 금지 위반 등)
            date = property[1].text.strip() # 신고일 추출

            # 리스트로 저장
            titlelist = [link, state, reportnumber, reporttitle, date]
            logger.LoggerFactory.logbot.debug(titlelist)
            df = pd.DataFrame([titlelist], columns=cols)
            yield df 
            # linklist.append(link) # 개별 신고건 링크 개별 추출
        
        if i <= last_page_num:
            next_button.click()
        else:
            break