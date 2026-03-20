from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup
import pandas as pd
import re
import os
import requests
import settings.settings as settings
import logger

import services.parser as doc_parser

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
            details = doc_parser.parse_details(driver, report_soup, result_soup)

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
            category = "traffic" if "자동차·교통위반" in details.get("entry_value", "") else "other"
            yield (df, category)

        except Exception as e:
            logger.LoggerFactory.logbot.error(f"Error processing link {link}: {e}")
            continue
