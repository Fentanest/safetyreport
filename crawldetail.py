from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from time import sleep
from bs4 import BeautifulSoup
import pandas as pd
import re
import settings.settings as settings
import logger

def Crawling_detail(driver, list):
    # 개별 신고결과 페이지url 만들기
    attemps = 0
    for link in list:
        path = settings.mysafereporturl + '/' + str(link)
        logger.LoggerFactory.logbot.debug(path)
        # 만들어진 링크 접속
        driver.get(path)
        sleep(1)

        table_xpath = f'//*[@id="contents"]/div[@class="{settings.singotable}" and div[@id!="splmntDivBody"]' # 처리결과 테이블
        while attemps <= int(settings.max_retry_attemps):
            try:
                table = WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.XPATH, (table_xpath)[2]))
                    )
                break
            except:
                logger.LoggerFactory.logbot.warning("개별 신고 페이지 접속 불가")
                sleep(int(settings.retry_interval))
                attemps += 1

        page_source = table.get_attribute('outerHTML')
        soup = BeautifulSoup(page_source, 'html.parser')
        soup_text = soup.get_text()

        # 신고 기본 정보 획득
        # 신고메뉴 확인
        entry = re.search(r'신고-(.*) 메뉴', soup_text)

        if entry:
            entry_value = entry.group(1).strip()
        else:
            entry_value = ''

        # 차량번호는 제목이 짧을 경우 제목에 기입되기 때문에, 재수없으면 2개 이상이 걸리는 경우가 있어 findall사용
        car_number = re.findall(r'\*\s차량번호\s*:\s*(\w*)', soup_text) # 차량번호

        if car_number:
            car_number = car_number[-1]
            car_number_value = car_number.strip()
        else:
            car_number_value = ""

        occurrence_date = re.search(r'\*\s발생일자\s*:\s*(\d{4}.\d{1,2}.\d{1,2})', soup_text) # 발생일자

        if occurrence_date:
            occurrence_date_value = occurrence_date.group(1)
            occurrence_date_value = occurrence_date_value.strip()
        else:
            occurrence_date_value = ""

        occurrence_time = re.search(r'\*\s발생시각\s*:\s*(\d{2}:\d{2})', soup_text) # 발생시각

        if occurrence_time:
            occurrence_time_value = occurrence_time.group(1)
            occurrence_time_value = occurrence_time_value.strip()
        else:
            occurrence_time_value = ""

        violation_law = re.search('<<(.*?)>>', soup_text) # 위반법규
        
        if violation_law:
            violation_law_value = violation_law.group(1)
            violation_law_value = violation_law_value.strip()
        else:
            violation_law_value = ""

        violation_location_th = soup.find('th', text='신고발생지역')

        if violation_location_th:
            violation_location_td = violation_location_th.find_next_sibling('td', colspan="3")
            if violation_location_td:
                violation_location_value = violation_location_td.find('p').get_text(strip=True)  # strip=True로 공백 제거
            else:
                violation_location_value = ""
        else:
            violation_location_value = ""

        # 처리상태 찾기
        processing_status_th = soup.find('th', string='처리상태')

        if processing_status_th:
            # 처리상태의 다음 형제인 td 태그 찾기
            processing_status_td = processing_status_th.find_next_sibling('td')
            processing_status_text = processing_status_td.get_text(strip=True)  # strip=True로 공백 제거
        else:
            processing_status_text = ""   
        
        if processing_status_text == "수용" or processing_status_text == "불수용" or processing_status_text == "일부수용" or processing_status_text == "기타":
                processing_finish_text = "Y"
        else:
                processing_finish_text = "N"
        
        # 처리기관 찾기
        processing_agency_th = soup.find('th', string='처리기관')

        if processing_agency_th:
            # 처리기관의 다음 형제인 td 태그 찾기
            processing_agency_td = processing_agency_th.find_next_sibling('td')
            processing_agency_text = processing_agency_td.get_text(strip=True)  # strip=True로 공백 제거
        else:
            processing_agency_text = ""
                
        # 담당자 찾기
        person_in_charge_th = soup.find('th', string='담당자')

        if person_in_charge_th:
            # 담당자의 다음 형제인 td 태그 찾기
            person_in_charge_td = person_in_charge_th.find_next_sibling('td')
            person_in_charge_text = person_in_charge_td.get_text(strip=True)  # strip=True로 공백 제거
        else:
            person_in_charge_text = ""
        
        # 답변일 찾기
        response_date_th = soup.find('th', string='답변일')

        if response_date_th:
            # 답변일의 다음 형제인 td 태그 찾기
            response_date_td = response_date_th.find_next_sibling('td')
            response_date_text = response_date_td.get_text(strip=True)  # strip=True로 공백 제거
        else:
            response_date_text = ""

        # 담배 투기(쓰레기, 폐기물)메뉴, 버스위반 메뉴는 담당자마다 답변 형식이 달라 일괄적으로 처리
        if (entry_value == "버스전용차로 위반(일반도로)" or entry_value == "쓰레기, 폐기물") and processing_status_text == "수용":
            fine_entry = "과태료"
        else:
            fine_entry = ""

        # "범칙금" 및 "벌점" // "과태료" 정보 추출
        penalty_matches = re.search(r'범칙금\s(\d*,\d*)원, 벌점\s(\d{0,4})점', soup_text)
        fine_matches = re.search(r'과태료\s(\d*,\d*)원', soup_text)

        x = ""

        cols = ["ID", "처리상태", "차량번호", "위반법규", "범칙금_과태료", "벌점", "처리기관", "담당자", "답변일", "발생일자", "발생시각", "위반장소", "종결여부"]
        detaillist = []

        if penalty_matches:
            penalty_amount = "범칙금: " + penalty_matches.group(1)+"원"
            penalty_points = "벌점: " + penalty_matches.group(2)+"점"
            detaillist = [
                link, processing_status_text, car_number_value, violation_law_value, penalty_amount, penalty_points, processing_agency_text, person_in_charge_text, response_date_text, occurrence_date_value, occurrence_time_value, violation_location_value, processing_finish_text
                ]
            logger.LoggerFactory.logbot.debug(detaillist)
            df = pd.DataFrame([detaillist], columns=cols)
            yield df
        elif fine_matches:
            fine_amount = "과태료: " + fine_matches.group(1)+"원"
            detaillist = [
                link, processing_status_text, car_number_value, violation_law_value, fine_amount, x, processing_agency_text, person_in_charge_text, response_date_text, occurrence_date_value, occurrence_time_value, violation_location_value, processing_finish_text
                ]
            logger.LoggerFactory.logbot.debug(detaillist)
            df = pd.DataFrame([detaillist], columns=cols)
            yield df
        elif fine_entry == "과태료":
            detaillist = [
                link, processing_status_text, car_number_value, violation_law_value, fine_entry, x, processing_agency_text, person_in_charge_text, response_date_text, occurrence_date_value, occurrence_time_value, violation_location_value, processing_finish_text
                ]
            logger.LoggerFactory.logbot.debug(detaillist)
            df = pd.DataFrame([detaillist], columns=cols)
            yield df
        else:
            detaillist = [
                link, processing_status_text, car_number_value, violation_law_value, x, x, processing_agency_text, person_in_charge_text, response_date_text, occurrence_date_value, occurrence_time_value, violation_location_value, processing_finish_text
                ]
            logger.LoggerFactory.logbot.debug(detaillist)
            df = pd.DataFrame([detaillist], columns=cols)
            yield df