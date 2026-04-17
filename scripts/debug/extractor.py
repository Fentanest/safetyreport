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


_CHROMIUM_BINS = ['/usr/bin/chromium', '/usr/bin/chromium-browser']
_CHROMEDRIVER_BINS = ['/usr/bin/chromedriver', '/usr/bin/chromium-driver',
                      '/usr/lib/chromium/chromedriver', '/usr/lib/chromium-browser/chromedriver']

def _is_docker():
    return os.path.isfile('/.dockerenv')

def _create_debug_driver():
    """디버그 전용 드라이버.

    - Docker: 내장 Chromium 헤드리스 직접 사용 (Hub 미사용 → 네트워크 트래픽 없음)
    - 그 외: chrome_mode 설정을 따르되 whatismybrowser.com 로딩 생략
    """
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

    if _is_docker():
        # Docker: 이미지 내장 Chromium + 시스템 chromedriver 직접 사용
        chromium = next((p for p in _CHROMIUM_BINS if os.path.isfile(p)), None)
        chromedriver = next((p for p in _CHROMEDRIVER_BINS if os.path.isfile(p)), None) \
                       or shutil.which('chromedriver')
        if not chromedriver:
            raise RuntimeError("시스템 chromedriver를 찾을 수 없습니다.")
        if chromium:
            options.binary_location = chromium
        print(f"  [Docker] Chromium: {chromium or '기본값'}, chromedriver: {chromedriver}")
        return ChromeWebDriver(service=Service(chromedriver), options=options)

    mode = getattr(settings, 'chrome_mode', 'hub')

    if mode == 'remote':
        remote_val = str(settings.remote_debug_port).strip()
        debug_addr = remote_val if ':' in remote_val else f"127.0.0.1:{remote_val}"
        options.add_experimental_option("debuggerAddress", debug_addr)
        from webdriver_manager.chrome import ChromeDriverManager
        service = Service(ChromeDriverManager().install())
        return ChromeWebDriver(service=service, options=options)

    elif mode == 'hub':
        return webdriver.Remote(command_executor=settings.remotepath, options=options)

    else:  # desktop
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
    page_soup = BeautifulSoup(raw_html, 'html.parser')
    parsed = doc_parser.parse_details(driver, report_soup, result_soup, page_soup=page_soup)
    return raw_html, parsed


def _resolve_id(engine, input_arg: str):
    if not input_arg.lstrip('-').isdigit():
        rid = lookup_id_by_report_number(engine, input_arg)
        if rid is None:
            print(f"[오류] DB에서 신고번호 '{input_arg}'를 찾을 수 없습니다.")
            return None
        print(f"신고번호 {input_arg} → 내부 ID: {rid}")
        return str(rid)
    print(f"내부 ID: {input_arg}")
    return input_arg


def _process_one(driver, record_id, out):
    print(f"\n{'='*50}")
    print(f"[처리] ID: {record_id}")

    # --- API 방식 ---
    print("\n  [1/2] API 방식 크롤링 중...")
    raw_json, result_data, api_parsed = crawl_via_api(driver, record_id)

    api_raw_path = os.path.join(out, f"{record_id}_api_raw.json")
    with open(api_raw_path, 'w', encoding='utf-8') as f:
        json.dump(raw_json, f, ensure_ascii=False, indent=2)
    print(f"    원시 JSON 저장: {api_raw_path}")

    if api_parsed:
        api_parsed_path = os.path.join(out, f"{record_id}_api_parsed.txt")
        with open(api_parsed_path, 'w', encoding='utf-8') as f:
            for k, v in api_parsed.items():
                f.write(f"{k}: {v}\n")
        print(f"    파싱 결과 저장: {api_parsed_path}")
    else:
        print(f"    [경고] API 파싱 실패: {raw_json.get('error', '알 수 없음')}")

    # --- Selenium 방식 ---
    print("\n  [2/2] Selenium 방식 크롤링 중...")
    raw_html, legacy_parsed = crawl_via_selenium(driver, record_id)

    legacy_html_path = os.path.join(out, f"{record_id}_legacy_raw.html")
    with open(legacy_html_path, 'w', encoding='utf-8') as f:
        f.write(raw_html)
    print(f"    원시 HTML 저장: {legacy_html_path}")

    legacy_parsed_path = os.path.join(out, f"{record_id}_legacy_parsed.txt")
    with open(legacy_parsed_path, 'w', encoding='utf-8') as f:
        for k, v in legacy_parsed.items():
            f.write(f"{k}: {v}\n")
    print(f"    파싱 결과 저장: {legacy_parsed_path}")

    # --- 차이 비교 ---
    if api_parsed and legacy_parsed:
        all_keys = set(api_parsed) | set(legacy_parsed)
        diffs = []
        for k in sorted(all_keys):
            a = str(api_parsed.get(k, ""))
            b = str(legacy_parsed.get(k, ""))
            if a != b:
                diffs.append((k, a, b))

        if diffs:
            print("\n  --- [비교] API vs Selenium 차이 ---")
            for k, a, b in diffs:
                print(f"    {k}:")
                print(f"      API     : {a[:120]}")
                print(f"      Selenium: {b[:120]}")
        else:
            print("\n  차이 없음.")

        diff_path = os.path.join(out, f"{record_id}_diff.txt")
        with open(diff_path, 'w', encoding='utf-8') as f:
            if diffs:
                for k, a, b in diffs:
                    f.write(f"[{k}]\nAPI     : {a}\nSelenium: {b}\n\n")
            else:
                f.write("차이 없음.\n")
        print(f"    비교 결과 저장: {diff_path}")


if __name__ == "__main__":
    input_args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if not input_args:
        print("사용법: python extractor.py <신고번호|내부ID> [신고번호|내부ID ...]")
        print("  예: python extractor.py SPP-2604-1234567")
        print("  예: python extractor.py 59216726 40871819")
        sys.exit(1)

    logger.LoggerFactory.create_logger()
    print("--- 디버그 스크립트 시작 ---")

    engine = create_engine(f"sqlite:///{settings.db_path}", connect_args={"check_same_thread": False})

    record_ids = []
    for arg in input_args:
        rid = _resolve_id(engine, arg)
        if rid:
            record_ids.append(rid)

    if not record_ids:
        print("[오류] 처리할 ID가 없습니다.")
        sys.exit(1)

    out = settings.logpath
    driver = None
    last_id = record_ids[-1]

    try:
        print(f"\n드라이버 생성 및 로그인... (mode: {getattr(settings, 'chrome_mode', 'hub')})")
        driver = _create_debug_driver()
        login.login_mysafety(driver=driver)
        print("로그인 완료.")
        print(f"총 {len(record_ids)}건 처리 예정: {record_ids}")

        for rid in record_ids:
            try:
                _process_one(driver, rid, out)
            except Exception as e:
                import traceback
                print(f"\n[오류] ID {rid} 처리 중 오류: {e}")
                traceback.print_exc()
                if driver:
                    err_path = os.path.join(out, f"{rid}_error.html")
                    with open(err_path, 'w', encoding='utf-8') as f:
                        f.write(driver.page_source)
                    print(f"  에러 페이지 소스 저장: {err_path}")

    except Exception as e:
        import traceback
        print(f"\n예기치 않은 오류: {e}")
        traceback.print_exc()

    finally:
        if driver:
            mode = getattr(settings, 'chrome_mode', 'hub')
            if not _is_docker() and mode == 'remote':
                print("\n드라이버 분리 (remote 모드 - Chrome 유지).")
            else:
                driver.quit()
                print("\n드라이버 종료.")
        print("--- 디버그 스크립트 종료 ---")
