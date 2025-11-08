from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.common.keys import Keys
import datetime
from time import sleep
import pandas as pd
import settings.settings as settings
import logger

def _scrape_current_page(driver):
    """Scrapes the data from the currently loaded page."""
    page_dfs = []
    found_in_progress = False
    cols = ["ID", "상태", "신고번호", "신고명", "신고일"]

    try:
        table = driver.find_element(By.ID, settings.titletable)
        tbody = table.find_element(By.TAG_NAME, 'tbody')
        rows = tbody.find_elements(By.TAG_NAME, 'tr')

        if not rows or (len(rows) == 1 and "데이터가 없습니다" in rows[0].text):
            logger.LoggerFactory.logbot.warning("현재 페이지에 데이터가 없습니다.")
            return [], False

        cNo_elements = tbody.find_elements(By.CSS_SELECTOR, 'td.bbs_subject input[name="cNo"]')

        for index, row in enumerate(rows):
            try:
                link = cNo_elements[index].get_attribute('value').strip()
                property_cells = row.find_elements(By.TAG_NAME, 'td')
                report_text = property_cells[0].text
                date = property_cells[1].text.strip()

                state_part, title_part = report_text.split(')', 1)
                state = state_part.split('(')[0].strip()
                reportnumber = state_part.split('(')[1].strip()
                reporttitle = title_part.strip()

                if state == '진행':
                    found_in_progress = True

                titlelist = [link, state, reportnumber, reporttitle, date]
                df = pd.DataFrame([titlelist], columns=cols)
                page_dfs.append(df)
            except IndexError:
                logger.LoggerFactory.logbot.warning(f"페이지의 {index+1}번째 행 파싱 중 오류가 발생했습니다.")
                continue
    except Exception as e:
        logger.LoggerFactory.logbot.error(f"현재 페이지를 스크래핑하는 중 오류 발생: {e}")

    return page_dfs, found_in_progress

def crawl_titles(driver, use_minimal_crawl=False, page_range=None):
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    attemps = 0
    while attemps <= int(settings.max_retry_attemps):
        try:
            driver.get(settings.myreporturl)
            sleep(3)
            break
        except Exception as e:
            logger.LoggerFactory.logbot.warning(f"마이페이지 접속 불가: {e}")
            sleep(int(settings.retry_interval))
            attemps += 1

    # Initial setup
    start_date_path = WebDriverWait(driver, 60).until(EC.presence_of_element_located((By.ID, "C_FRM_DATE")))
    start_date_path.clear()
    start_date_path.send_keys("2016-01-01")
    end_date_path = WebDriverWait(driver, 60).until(EC.presence_of_element_located((By.ID, "C_TO_DATE")))
    end_date_path.clear()
    end_date_path.send_keys(today)
    search_button = WebDriverWait(driver, 60).until(EC.presence_of_element_located((By.XPATH, "//button[@class='button btnSearch']")))
    search_button.send_keys(Keys.ENTER)
    sleep(5)
    page_size_select = WebDriverWait(driver, 60).until(EC.presence_of_element_located((By.ID, "pageSize")))
    Select(page_size_select).select_by_value("30")
    sleep(3)

    # Get total page number
    page_info_element = WebDriverWait(driver, 60).until(EC.presence_of_element_located((By.CSS_SELECTOR, 'p.bbs_info.fl_left')))
    last_page_num = int(page_info_element.text.split('/')[1].replace('페이지', '').strip())
    logger.LoggerFactory.logbot.debug(f"마지막 페이지 번호: {last_page_num}")

    all_title_dfs = []
    empty_page_count = 0
    last_crawled_page = 0

    # If page_range is specified, use the old method
    if page_range:
        logger.LoggerFactory.logbot.info(f"지정된 페이지 범위 {page_range}에 대해 크롤링합니다.")
        current_page = 1
        for page_num in page_range:
            last_crawled_page = page_num
            if current_page != page_num:
                try:
                    page_link = driver.find_element(By.XPATH, f'//a[text()="{page_num}"]')
                    page_link.click()
                    sleep(3)
                    current_page = page_num
                except NoSuchElementException:
                    logger.LoggerFactory.logbot.warning(f"페이지 링크 '{page_num}'을(를) 찾을 수 없습니다.")
                    last_crawled_page -=1
                    break
                except Exception as e:
                    logger.LoggerFactory.logbot.error(f"{page_num} 페이지로 이동 중 예상치 못한 오류 발생: {e}")
                    continue
            
            dfs, _ = _scrape_current_page(driver)
            all_title_dfs.extend(dfs)
        return all_title_dfs, last_crawled_page

    # --- New "Next" button logic for full crawl ---
    
    # Process page 1
    logger.LoggerFactory.logbot.debug("1 페이지 처리 중...")
    dfs, found_in_progress = _scrape_current_page(driver)
    all_title_dfs.extend(dfs)
    last_crawled_page = 1
    if use_minimal_crawl and not found_in_progress:
        empty_page_count += 1

    # Loop for subsequent pages by clicking "Next"
    for page_num in range(2, last_page_num + 1):
        if use_minimal_crawl and empty_page_count >= int(settings.max_empty_pages):
            logger.LoggerFactory.logbot.info(f"{settings.max_empty_pages} 페이지 동안 '진행' 상태의 신고가 없어 크롤링을 조기 종료합니다.")
            break
        
        try:
            logger.LoggerFactory.logbot.debug(f"{page_num} 페이지로 이동합니다 (다음 버튼 클릭)...")
            # Wait for the loading overlay to disappear
            WebDriverWait(driver, 20).until(
                EC.invisibility_of_element_located((By.CSS_SELECTOR, "div.loading-backdrop"))
            )
            # Wait for the "Next" button to be clickable and then click it
            next_button = WebDriverWait(driver, 20).until(
                EC.element_to_be_clickable((By.XPATH, '//a[@title="다음으로 이동"]'))
            )
            next_button.click()
            sleep(2) # Allow a brief moment for JS to potentially render
        except (NoSuchElementException, TimeoutException):
            logger.LoggerFactory.logbot.info("'다음' 버튼을 찾거나 클릭할 수 없어 크롤링을 종료합니다.")
            break
        except Exception as e:
            logger.LoggerFactory.logbot.error(f"{page_num} 페이지로 이동 중 예상치 못한 오류 발생: {e}")
            break

        dfs, found_in_progress = _scrape_current_page(driver)
        all_title_dfs.extend(dfs)
        last_crawled_page = page_num

        if use_minimal_crawl:
            if not found_in_progress:
                empty_page_count += 1
            else:
                empty_page_count = 0
    
    return all_title_dfs, last_crawled_page