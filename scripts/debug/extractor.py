import os
import sys
import json

root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

import settings.settings as settings
from core.utils import logger
from core.crawler import login
import services.parser as doc_parser
from sqlalchemy import create_engine, select
from core.database.models import title_table, metadata

from selenium import webdriver
from selenium.webdriver.chrome.webdriver import WebDriver as ChromeWebDriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup
import platform, shutil
import time


def _create_isolated_driver():
    """프로덕션 Chrome 설정과 완전히 분리된 독립 headless 드라이버를 생성합니다.
    remote/hub 모드를 사용하지 않으므로 실행 중인 서버에 영향을 주지 않습니다."""
    options = webdriver.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--incognito")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--enable-javascript")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    options.add_argument('--disable-blink-features=AutomationControlled')

    machine = platform.machine().lower()
    if ('aarch64' in machine or 'arm64' in machine) and platform.system() != 'Windows':
        candidates = ['/usr/bin/chromedriver', '/usr/lib/chromium/chromedriver']
        cd_path = next((p for p in candidates if shutil.which(p) or __import__('os').path.isfile(p)), None)
        if cd_path:
            service = Service(cd_path)
        else:
            from webdriver_manager.chrome import ChromeDriverManager
            service = Service(ChromeDriverManager().install())
        for bin_path in ['/usr/bin/chromium', '/usr/bin/chromium-browser']:
            if __import__('os').path.isfile(bin_path):
                options.binary_location = bin_path
                break
    else:
        from webdriver_manager.chrome import ChromeDriverManager
        service = Service(ChromeDriverManager().install())

    return ChromeWebDriver(service=service, options=options)


def lookup_id_by_report_number(engine, report_number: str):
    with engine.connect() as conn:
        result = conn.execute(
            select(title_table.c.ID).where(title_table.c.신고번호 == report_number)
        ).first()
        return result[0] if result else None


def crawl_via_api(driver, record_id):
    """API 방식으로 원시 JSON과 파싱 결과 반환."""
    script = f"""
    var callback = arguments[arguments.length - 1];
    $.get('/api/v1/portal/mypage/mysafereport/{record_id}')
     .done(function(data) {{ callback(data); }})
     .fail(function(jqXHR, textStatus, errorThrown) {{ callback({{error: textStatus + ' ' + errorThrown}}); }});
    """
    if "safetyreport.go.kr" not in driver.current_url:
        driver.get(settings.myreporturl)
        time.sleep(2)

    raw = driver.execute_async_script(script)

    if "error" in raw or "result" not in raw:
        return raw, None, None

    result_data = raw["result"]
    parsed = doc_parser.parse_json_details(result_data)
    entry_value = parsed.get("entry_value", "")
    return raw, result_data, parsed


def crawl_via_selenium(driver, record_id):
    """Selenium 방식으로 원시 HTML과 파싱 결과 반환."""
    url = f"{settings.mysafereporturl}/{record_id}"
    driver.get(url)
    driver.refresh()
    WebDriverWait(driver, 20).until(
        lambda d: d.execute_script('return document.readyState') == 'complete'
    )

    report_table_xpath = "//div[contains(@class, 'singo') and .//th[text()='신고번호']]"
    report_table_element = WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.XPATH, report_table_xpath))
    )
    report_soup = BeautifulSoup(report_table_element.get_attribute('outerHTML'), 'html.parser')

    progress_status_th = report_soup.find('th', string='진행상황')
    progress_status = ""
    if progress_status_th:
        td = progress_status_th.find_next_sibling('td')
        if td:
            progress_status = td.get_text(strip=True)

    result_soup = None
    if progress_status not in ['진행', '취하']:
        try:
            result_table_xpath = "//div[contains(@class, 'singo') and .//th[text()='처리내용']]"
            WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.XPATH, result_table_xpath))
            )
            result_table_elements = driver.find_elements(By.XPATH, result_table_xpath)
            result_soup = BeautifulSoup(result_table_elements[-1].get_attribute('outerHTML'), 'html.parser')
        except Exception:
            pass

    raw_html = driver.page_source
    parsed = doc_parser.parse_details(driver, report_soup, result_soup)
    return raw_html, parsed


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python extractor.py <신고번호|내부ID>")
        print("  예: python extractor.py SPP-2604-1234567")
        print("  예: python extractor.py 59216726")
        sys.exit(1)

    input_arg = sys.argv[1]

    logger.LoggerFactory.create_logger()
    print("--- 디버그 스크립트 시작 ---")

    engine = create_engine(f"sqlite:///{settings.db_path}", connect_args={"check_same_thread": False})

    if not input_arg.lstrip('-').isdigit():
        report_number = input_arg
        record_id = lookup_id_by_report_number(engine, report_number)
        if record_id is None:
            print(f"[오류] DB에서 신고번호 '{report_number}'를 찾을 수 없습니다.")
            sys.exit(1)
        print(f"신고번호 {report_number} → 내부 ID: {record_id}")
    else:
        record_id = input_arg
        print(f"내부 ID: {record_id}")

    out = settings.logpath
    driver = None

    try:
        print("드라이버 생성 및 로그인... (독립 headless 세션, 서버 무영향)")
        driver = _create_isolated_driver()
        login.login_mysafety(driver=driver)
        print("로그인 완료.")

        # --- API 방식 ---
        print("\n[1/2] API 방식 크롤링 중...")
        raw_json, result_data, api_parsed = crawl_via_api(driver, record_id)

        api_raw_path = os.path.join(out, f"{record_id}_api_raw.json")
        with open(api_raw_path, 'w', encoding='utf-8') as f:
            json.dump(raw_json, f, ensure_ascii=False, indent=2)
        print(f"  원시 JSON 저장: {api_raw_path}")

        if api_parsed:
            api_parsed_path = os.path.join(out, f"{record_id}_api_parsed.txt")
            with open(api_parsed_path, 'w', encoding='utf-8') as f:
                for k, v in api_parsed.items():
                    f.write(f"{k}: {v}\n")
            print(f"  파싱 결과 저장: {api_parsed_path}")
        else:
            print(f"  [경고] API 파싱 실패: {raw_json.get('error', '알 수 없음')}")

        # --- Selenium 방식 ---
        print("\n[2/2] Selenium 방식 크롤링 중...")
        raw_html, legacy_parsed = crawl_via_selenium(driver, record_id)

        legacy_html_path = os.path.join(out, f"{record_id}_legacy_raw.html")
        with open(legacy_html_path, 'w', encoding='utf-8') as f:
            f.write(raw_html)
        print(f"  원시 HTML 저장: {legacy_html_path}")

        legacy_parsed_path = os.path.join(out, f"{record_id}_legacy_parsed.txt")
        with open(legacy_parsed_path, 'w', encoding='utf-8') as f:
            for k, v in legacy_parsed.items():
                f.write(f"{k}: {v}\n")
        print(f"  파싱 결과 저장: {legacy_parsed_path}")

        # --- 차이 비교 ---
        if api_parsed and legacy_parsed:
            print("\n--- [비교] API vs Selenium 차이 ---")
            all_keys = set(api_parsed) | set(legacy_parsed)
            diffs = []
            for k in sorted(all_keys):
                a = str(api_parsed.get(k, ""))
                b = str(legacy_parsed.get(k, ""))
                if a != b:
                    diffs.append((k, a, b))
                    print(f"  {k}:")
                    print(f"    API     : {a[:120]}")
                    print(f"    Selenium: {b[:120]}")
            if not diffs:
                print("  차이 없음.")

            diff_path = os.path.join(out, f"{record_id}_diff.txt")
            with open(diff_path, 'w', encoding='utf-8') as f:
                if diffs:
                    for k, a, b in diffs:
                        f.write(f"[{k}]\nAPI     : {a}\nSelenium: {b}\n\n")
                else:
                    f.write("차이 없음.\n")
            print(f"\n  비교 결과 저장: {diff_path}")

    except Exception as e:
        import traceback
        print(f"\n예기치 않은 오류: {e}")
        traceback.print_exc()
        if driver:
            err_path = os.path.join(out, f"{record_id}_error.html")
            with open(err_path, 'w', encoding='utf-8') as f:
                f.write(driver.page_source)
            print(f"에러 페이지 소스 저장: {err_path}")

    finally:
        if driver:
            driver.quit()
            print("\n드라이버 종료.")
        print("--- 디버그 스크립트 종료 ---")
