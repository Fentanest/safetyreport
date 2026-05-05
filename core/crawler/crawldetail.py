from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup
import settings.settings as settings
from core.utils import logger

import services.parser as doc_parser
from services import satisfaction_fetcher
from core.crawler.detail_pipeline import build_detail_result


def _get_latest_result_table_soup(driver):
    """처리결과 테이블이 여러 개인 경우 마지막 테이블을 최신 답변으로 사용한다."""
    result_table_xpath = "//div[contains(@class, 'singo')]//table[.//th[text()='처리내용']]"
    WebDriverWait(driver, 5).until(
        EC.presence_of_element_located((By.XPATH, result_table_xpath))
    )
    result_table_elements = driver.find_elements(By.XPATH, result_table_xpath)
    if not result_table_elements:
        return None, 0

    latest_table = result_table_elements[-1]
    return BeautifulSoup(latest_table.get_attribute('outerHTML'), 'html.parser'), len(result_table_elements)

def crawl_details(driver, report_ids):
    """Crawls the detail page for each report link."""
    for link in report_ids:
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
                    result_soup, result_table_count = _get_latest_result_table_soup(driver)
                    logger.LoggerFactory.logbot.debug(
                        f"Processing result table found (using last of {result_table_count})."
                    )
                except Exception:
                    logger.LoggerFactory.logbot.debug("Processing result table not found, but was expected.")
            else:
                logger.LoggerFactory.logbot.debug(f"Skipping result table wait for status: {progress_status}")

            # Parse all details using the helper function
            page_soup = BeautifulSoup(driver.page_source, 'html.parser')
            details = doc_parser.parse_details(driver, report_soup, result_soup, page_soup=page_soup)

            yield build_detail_result(
                link,
                details,
                progress_status=progress_status,
                satisfaction_client=driver,
                satisfaction_fetcher=satisfaction_fetcher.fetch_score_via_selenium_page,
            )

        except Exception as e:
            logger.LoggerFactory.logbot.error(f"Error processing link {link}: {e}")
            continue
