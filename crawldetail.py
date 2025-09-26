from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from time import sleep
from bs4 import BeautifulSoup
import pandas as pd
import re
import settings.settings as settings
import logger

def _parse_details(report_soup, result_soup=None):
    """Helper function to parse details from soup objects."""
    
    # --- Parse Report Content Table (Mandatory) ---
    report_text = report_soup.get_text().translate(str.maketrans('０１２３４５６７８９，', '0123456789,'))

    # Find the '내용' (content) cell, which contains the key details, to avoid parsing incorrect data from other parts of the page.
    content_th = report_soup.find('th', string='내용')
    content_text = ""
    if content_th:
        content_td = content_th.find_next_sibling('td')
        if content_td:
            # Use get_text with a separator to handle <br> tags, which are common in this field.
            content_text = content_td.get_text(separator='\n').translate(str.maketrans('０１２３４５６７８９，', '0123456789,'))

    # Parse entry, car number, date, and time from the dedicated '내용' text.
    entry_match = re.search(r'본 신고는 안전신문고 앱의 (.*?) 메뉴로 접수된 신고입니다', content_text)
    entry_value = entry_match.group(1).strip() if entry_match else ""
    
    car_number_match = re.search(r'차량번호\s*:\s*(.*?)(?=\s*발생일자|\n)', content_text)
    car_number_value = car_number_match.group(1).strip() if car_number_match else ""

    occurrence_date_match = re.search(r'발생일자\s*:\s*(\d{4}.\d{1,2}.\d{1,2})', content_text)
    occurrence_date_value = occurrence_date_match.group(1).strip() if occurrence_date_match else ""

    occurrence_time_match = re.search(r'발생시각\s*:\s*(\d{2}:\d{2})', content_text)
    occurrence_time_value = occurrence_time_match.group(1).strip() if occurrence_time_match else ""

    violation_location_th = report_soup.find('th', string='신고발생지역')
    violation_location_value = ""
    if violation_location_th:
        violation_location_td = violation_location_th.find_next_sibling('td')
        if violation_location_td and violation_location_td.find('p'):
            violation_location_value = violation_location_td.find('p').get_text(strip=True)

    # Extract '진행상황' to check for '취하'
    progress_status_th = report_soup.find('th', string='진행상황')
    progress_status = ""
    if progress_status_th:
        progress_status_td = progress_status_th.find_next_sibling('td')
        if progress_status_td:
            progress_status = progress_status_td.get_text(strip=True)

    # --- Parse Processing Result Table (Optional) ---
    if result_soup:
        result_text = result_soup.get_text().translate(str.maketrans('０１２３４５６７８９，', '0123456789,'))

        violation_law_match = re.search(r'도로교통법\s*제\d+조', result_text)
        violation_law_value = violation_law_match.group(0).strip() if violation_law_match else ""

        processing_status_th = result_soup.find('th', string='처리상태')
        processing_status_text = ""
        if processing_status_th:
            processing_status_td = processing_status_th.find_next_sibling('td')
            if processing_status_td:
                processing_status_text = processing_status_td.get_text(strip=True)
        
        processing_finish_text = "N"
        if processing_status_text in ["수용", "불수용", "일부수용", "기타"]:
            processing_finish_text = "Y"

        processing_agency_th = result_soup.find('th', string='처리기관')
        processing_agency_text = ""
        if processing_agency_th:
            processing_agency_td = processing_agency_th.find_next_sibling('td')
            if processing_agency_td:
                processing_agency_text = processing_agency_td.get_text(strip=True)
        
        person_in_charge_th = result_soup.find('th', string='담당자')
        person_in_charge_text = ""
        if person_in_charge_th:
            person_in_charge_td = person_in_charge_th.find_next_sibling('td')
            if person_in_charge_td:
                person_in_charge_text = person_in_charge_td.get_text(strip=True)

        response_date_th = result_soup.find('th', string='답변일')
        response_date_text = ""
        if response_date_th:
            response_date_td = response_date_th.find_next_sibling('td')
            if response_date_td:
                response_date_text = response_date_td.get_text(strip=True)

        fine_entry = ""
        if (entry_value == "버스전용차로 위반(일반도로)" or entry_value == "쓰레기, 폐기물") and processing_status_text == "수용":
            fine_entry = "과태료"

        penalty_matches = re.search(r'범칙금\s+([\d,]+)\s*원, 벌점\s+(\d{0,4})\s*점', result_text)
        fine_matches = re.search(r'과태료\s+([\d,]+)\s*원', result_text)

        penalty_amount = ""
        penalty_points = ""
        fine_amount = ""

        if penalty_matches:
            penalty_amount = "범칙금: " + penalty_matches.group(1) + "원"
            penalty_points = "벌점: " + penalty_matches.group(2) + "점"
        elif fine_matches:
            fine_amount = "과태료: " + fine_matches.group(1) + "원"
        
        final_penalty = penalty_amount or fine_amount or fine_entry

    # --- Set default values if result table is not found ---
    else:
        violation_law_value = ""
        processing_status_text = "처리중"
        processing_finish_text = "N"
        processing_agency_text = ""
        person_in_charge_text = ""
        response_date_text = ""
        final_penalty = ""
        penalty_points = ""

    # --- Final determination of completion status ---
    if progress_status == "취하":
        processing_finish_text = "Y"
        if not processing_status_text or processing_status_text == "처리중":
            processing_status_text = "취하"

    # Return a dictionary of parsed values
    return {
        "processing_status": processing_status_text,
        "car_number": car_number_value,
        "violation_law": violation_law_value,
        "penalty_amount": final_penalty,
        "penalty_points": penalty_points,
        "processing_agency": processing_agency_text,
        "person_in_charge": person_in_charge_text,
        "response_date": response_date_text,
        "occurrence_date": occurrence_date_value,
        "occurrence_time": occurrence_time_value,
        "violation_location": violation_location_value,
        "processing_finish": processing_finish_text,
    }

def Crawling_detail(driver, list):
    """Crawls the detail page for each report link."""
    for link in list:
        path = f"{settings.mysafereporturl}/{link}"
        logger.LoggerFactory.logbot.debug(path)
        driver.get(path)
        
        try:
            logger.LoggerFactory.logbot.debug("Waiting for report content table to load...")
            report_table_xpath = "//div[contains(@class, 'singo') and .//th[text()='신고번호']]"
            report_table_element = WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.XPATH, report_table_xpath))
            )
            report_soup = BeautifulSoup(report_table_element.get_attribute('outerHTML'), 'html.parser')

            # Try to find the result table, but don't fail if it's not there
            result_soup = None
            try:
                result_table_xpath = "//div[contains(@class, 'singo') and .//th[text()='처리내용']]"
                result_table_element = WebDriverWait(driver, 5).until(
                    EC.presence_of_element_located((By.XPATH, result_table_xpath))
                )
                result_soup = BeautifulSoup(result_table_element.get_attribute('outerHTML'), 'html.parser')
                logger.LoggerFactory.logbot.debug("Processing result table found.")
            except:
                logger.LoggerFactory.logbot.debug("Processing result table not found. Assuming report is in progress.")

            # Parse all details using the helper function
            details = _parse_details(report_soup, result_soup)

            # Create DataFrame
            cols = ["ID", "처리상태", "차량번호", "위반법규", "범칙금_과태료", "벌점", "처리기관", "담당자", "답변일", "발생일자", "발생시각", "위반장소", "종결여부"]
            
            detaillist = [
                link,
                details["processing_status"],
                details["car_number"],
                details["violation_law"],
                details["penalty_amount"],
                details["penalty_points"],
                details["processing_agency"],
                details["person_in_charge"],
                details["response_date"],
                details["occurrence_date"],
                details["occurrence_time"],
                details["violation_location"],
                details["processing_finish"],
            ]
            
            logger.LoggerFactory.logbot.debug(detaillist)
            df = pd.DataFrame([detaillist], columns=cols)
            yield df

        except Exception as e:
            logger.LoggerFactory.logbot.error(f"Error processing link {link}: {e}")
            continue
