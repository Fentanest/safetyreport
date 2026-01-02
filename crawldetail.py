from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup
import pandas as pd
import re
import settings.settings as settings
import logger

def _parse_report_content_table(report_soup):
    """Helper function to parse details from the Report Content Table."""

    content_th = report_soup.find('th', string='내용')
    content_text = ""
    if content_th:
        content_td = content_th.find_next_sibling('td')
        if content_td:
            content_text = content_td.get_text(separator='\n').translate(str.maketrans('０１２３４５６７８９，', '0123456789,'))

    entry_match = re.search(r'본 신고는 안전신문고 (?:앱의|포털의) (.*?) 메뉴로 접수된 신고입니다', content_text)
    entry_value = entry_match.group(1).strip() if entry_match else ""

    car_number_match = re.search(r'차량번호\s*:\s*(.*?)(?=\n|\(위)', content_text)
    car_number_value = re.sub(r'\s+', '', car_number_match.group(1)) if car_number_match else ""

    occurrence_date_match = re.search(r'발생일자\s*:\s*(\d{4}.\d{1,2}.\d{1,2})', content_text)
    occurrence_date_value = occurrence_date_match.group(1).strip().replace('.', '-') if occurrence_date_match else ""

    occurrence_time_match = re.search(r'발생시각\s*:\s*(\d{2}:\d{2})', content_text)
    occurrence_time_value = occurrence_time_match.group(1).strip() if occurrence_time_match else ""

    violation_location_th = report_soup.find('th', string='신고발생지역')
    violation_location_value = ""
    if violation_location_th:
        violation_location_td = violation_location_th.find_next_sibling('td')
        if violation_location_td and violation_location_td.find('p'):
            violation_location_value = violation_location_td.find('p').get_text(strip=True)

    progress_status_th = report_soup.find('th', string='진행상황')
    progress_status = ""
    if progress_status_th:
        progress_status_td = progress_status_th.find_next_sibling('td')
        if progress_status_td:
            progress_status = progress_status_td.get_text(strip=True)

    report_content = ""
    if content_text:
        parts = re.split(r'\*\s*차량번호', content_text, 1)
        if parts:
            report_content = parts[0].strip()

    attachment_th = report_soup.find('th', string='첨부파일')
    attachment_files = ""
    attached_photos = ""
    map_image = ""
    if attachment_th:
        attachment_td = attachment_th.find_next_sibling('td')
        if attachment_td:
            if "6개월 지난 신고건의 경우 첨부파일을 삭제하고 있습니다." in attachment_td.get_text():
                attachment_files = ""
                attached_photos = ""
            else:
                image_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp']
                image_urls = []
                other_urls = []
                map_urls = []
                links = attachment_td.find_all('a')
                for link in links:
                    href = link.get('href')
                    data_title = link.get('data-title')
                    url = None
                    if href and href.startswith('/fileDown/singo'):
                        url = f"https://www.safetyreport.go.kr{href}"
                    elif data_title and data_title.startswith('/fileDown/singo'):
                        url = f"https://www.safetyreport.go.kr{data_title}"
                    
                    if url:
                        if "MAPIMG" in url:
                            map_urls.append(url)
                        elif any(url.lower().endswith(ext) for ext in image_extensions):
                            image_urls.append(url)
                        else:
                            other_urls.append(url)

                attachment_files = "\n".join(other_urls)
                attached_photos = "\n".join(image_urls)
                map_image = "\n".join(map_urls)

    return {
        "entry_value": entry_value,
        "car_number": car_number_value,
        "occurrence_date": occurrence_date_value,
        "occurrence_time": occurrence_time_value,
        "violation_location": violation_location_value,
        "progress_status": progress_status,
        "report_content": report_content,
        "attachment_files": attachment_files,
        "attached_photos": attached_photos,
        "map_image": map_image,
    }

def _parse_processing_result_table(result_soup, entry_value):
    """Helper function to parse details from the Processing Result Table."""
    result_text = result_soup.get_text().translate(str.maketrans('０１２３４５６７８９，', '0123456789,'))

    processing_content_th = result_soup.find('th', string='처리내용')
    processing_content = ""
    if processing_content_th:
        processing_content_td = processing_content_th.find_next_sibling('td')
        if processing_content_td:
            processing_content = processing_content_td.get_text(separator='\n').strip()

    violation_law_match = re.search(r'도로교통법\s*제\d+조(?:\s*제?\d{1,2}항)?', result_text)
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
    if ("버스전용차로 위반" in entry_value or "쓰레기, 폐기물" in entry_value or "불법주정차신고" in entry_value) and processing_status_text == "수용":
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

    if "교통질서 안내장" in processing_content:
        final_penalty = '경고'

    return {
        "processing_status": processing_status_text,
        "violation_law": violation_law_value,
        "penalty_amount": final_penalty,
        "penalty_points": penalty_points,
        "processing_agency": processing_agency_text,
        "person_in_charge": person_in_charge_text,
        "response_date": response_date_text,
        "processing_content": processing_content,
        "processing_finish": processing_finish_text,
    }

def _parse_details(report_soup, result_soup=None):
    """Helper function to parse details from soup objects."""
    
    report_details = _parse_report_content_table(report_soup)
    
    processing_details = {}
    if result_soup:
        processing_details = _parse_processing_result_table(result_soup, report_details["entry_value"])
    else:
        # Set default values if result table is not found
        processing_details = {
            "processing_status": "처리중",
            "violation_law": "",
            "penalty_amount": "",
            "penalty_points": "",
            "processing_agency": "",
            "person_in_charge": "",
            "response_date": "",
            "processing_content": "",
            "processing_finish": "N",
        }

    # Final determination of completion status
    if report_details["progress_status"] == "취하":
        processing_details["processing_finish"] = "Y"
        if not processing_details["processing_status"] or processing_details["processing_status"] == "처리중":
            processing_details["processing_status"] = "취하"

    # Merge all details
    all_details = {**report_details, **processing_details}
    
    # Remove entry_value as it's an intermediate value
    all_details.pop("entry_value", None)
    all_details.pop("progress_status", None) # progress_status is now handled internally

    return all_details
def crawl_details(driver, list):
    """Crawls the detail page for each report link."""
    for link in list:
        path = f"{settings.mysafereporturl}/{link}"
        logger.LoggerFactory.logbot.debug(path)
        driver.get(path)
        driver.refresh()
        WebDriverWait(driver, 20).until(
            lambda d: d.execute_script('return document.readyState') == 'complete'
        )
        
        try:
            logger.LoggerFactory.logbot.debug("Waiting for report content table to load...")
            report_table_xpath = "//div[contains(@class, 'singo') and .//th[text()='신고번호']]"
            report_table_element = WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.XPATH, report_table_xpath))
            )
            report_soup = BeautifulSoup(report_table_element.get_attribute('outerHTML'), 'html.parser')

            # --- Optimization: Check progress status early ---
            progress_status_th = report_soup.find('th', string='진행상황')
            progress_status = ""
            if progress_status_th:
                progress_status_td = progress_status_th.find_next_sibling('td')
                if progress_status_td:
                    progress_status = progress_status_td.get_text(strip=True)

            result_soup = None
            # If the report is not in progress or withdrawn, wait for the result table
            if progress_status not in ['진행', '취하']:
                try:
                    result_table_xpath = "//div[contains(@class, 'singo') and .//th[text()='처리내용']]"
                    result_table_element = WebDriverWait(driver, 5).until(
                        EC.presence_of_element_located((By.XPATH, result_table_xpath))
                    )
                    result_soup = BeautifulSoup(result_table_element.get_attribute('outerHTML'), 'html.parser')
                    logger.LoggerFactory.logbot.debug("Processing result table found.")
                except Exception:
                    logger.LoggerFactory.logbot.debug("Processing result table not found, but was expected.")
            else:
                logger.LoggerFactory.logbot.debug(f"Skipping result table wait for status: {progress_status}")

            # Parse all details using the helper function
            details = _parse_details(report_soup, result_soup)

            # Create DataFrame
            cols = ["ID", "처리상태", "차량번호", "위반법규", "범칙금_과태료", "벌점", "처리기관", "담당자", "답변일", "발생일자", "발생시각", "위반장소", "종결여부", "신고내용", "처리내용", "지도", "첨부사진", "첨부파일"]
            
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
                details["report_content"],
                details["processing_content"],
                details["map_image"],
                details["attached_photos"],
                details["attachment_files"],
            ]
            
            logger.LoggerFactory.logbot.info(detaillist)
            df = pd.DataFrame([detaillist], columns=cols)
            yield df

        except Exception as e:
            logger.LoggerFactory.logbot.error(f"Error processing link {link}: {e}")
            continue
