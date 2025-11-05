import sys
import driv
import login
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup
import time
import logger
import crawldetail # Import the refactored module

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python debug_extractor.py <신고번호>")
        sys.exit(1)
    report_id = sys.argv[1]

    logger.LoggerFactory.create_logger()
    print("--- 디버그 스크립트 시작 ---")
    print(f"대상 신고번호: {report_id}")

    driver = None
    try:
        print("드라이버 생성 및 로그인...")
        driver = driv.create_driver()
        login.login_mysafety(driver=driver)
        print("로그인 완료.")

        url = f"https://www.safetyreport.go.kr/#mypage/mysafereport/{report_id}"
        print(f"URL로 이동 중: {url}...")
        driver.get(url)
        
        print("페이지 콘텐츠 로딩 대기 중...")
        time.sleep(5)

        # Find the mandatory report content table
        report_table_element = WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.XPATH, "//div[contains(@class, 'singo') and .//th[text()='신고번호']]"))
        )
        report_soup = BeautifulSoup(report_table_element.get_attribute('outerHTML'), 'html.parser')
        print("--- [성공] 1번 테이블 '신고내용' 찾음 ---")

        # Find the optional result table
        result_soup = None
        try:
            result_table_element = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.XPATH, "//div[contains(@class, 'singo') and .//th[text()='처리내용']]"))
            )
            result_soup = BeautifulSoup(result_table_element.get_attribute('outerHTML'), 'html.parser')
            print("--- [성공] 2번 테이블 '처리결과' 찾음 ---")
        except Exception:
            print("--- [정보] 2번 테이블 '처리결과' 없음 ---")

        # Call the centralized parsing function from crawldetail
        print("\n--- 파싱 결과 ---")
        details = crawldetail._parse_details(report_soup, result_soup)
        
        # Print the results neatly
        for key, value in details.items():
            print(f"  - {key}: {value}")

    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}")
        if driver:
            print("\n--- FULL PAGE SOURCE ON ERROR ---")
            print(driver.page_source)

    finally:
        if driver:
            driver.quit()
            print("\nDriver closed.")
        print("--- 디버그 스크립트 종료 ---")