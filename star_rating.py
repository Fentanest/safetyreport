import time
import requests
import settings.settings as settings
import logger

def run_batch_rating(ids, score=5):
    """
    ids: List of report IDs to rate (SPP-...)
    score: 1 to 5
    """
    logger.LoggerFactory.logbot.info(f"API 기반 일괄 별점 부여 시작 (총 {len(ids)}건, 점수: {score}점)")
    
    success_count = 0
    fail_count = 0
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Origin": "https://www.safetyreport.go.kr",
        "X-Requested-With": "XMLHttpRequest"
    }
    
    for report_id in ids:
        try:
            # 1. 상태 확인 (이미 별점을 주었는지)
            check_url = f"https://www.safetyreport.go.kr/api/v1/portal/statistics/satisfactionstatistics/score/{report_id}/{settings.phone_number}"
            headers['Referer'] = f"https://www.safetyreport.go.kr/html/common/popup/satisfaction.html?seq={report_id}&pn={settings.phone_number}"
            
            check_res = requests.get(check_url, headers=headers, timeout=10)
            if check_res.status_code == 200:
                data = check_res.json()
                if not data.get("result"):
                    logger.LoggerFactory.logbot.error(f"[{report_id}] 대상 신고건이 없거나 폰 번호가 맞지 않습니다.")
                    fail_count += 1
                    continue
                if data["result"].get("STSFDG_SCORE", 0) != 0:
                    logger.LoggerFactory.logbot.warning(f"[{report_id}] 이미 만족도 조사에 참여하셨습니다.")
                    fail_count += 1
                    continue
            
            # 2. 별점 제출
            post_url = "https://www.safetyreport.go.kr/api/v1/portal/statistics/satisfactionstatistics"
            payload = {
                "STTEMNT_NO": report_id,
                "C_PHONE2": settings.phone_number,
                "STSFDG_SCORE": str(score),
                "STSFDG_CAUSE": ""
            }
            
            post_res = requests.post(post_url, data=payload, headers=headers, timeout=10)
            if post_res.status_code == 200:
                logger.LoggerFactory.logbot.info(f"[{report_id}] {score}점 별점 부여 성공 (API)")
                success_count += 1
            else:
                logger.LoggerFactory.logbot.error(f"[{report_id}] 제출 실패 (HTTP {post_res.status_code}): {post_res.text}")
                fail_count += 1
                
            time.sleep(1) # 부하 방지
            
        except Exception as e:
            logger.LoggerFactory.logbot.error(f"[{report_id}] API 통신 중 오류 발생: {e}")
            fail_count += 1
            
    logger.LoggerFactory.logbot.info(f"API 일괄 별점 처리가 종료되었습니다. (성공: {success_count}, 스킵/실패: {fail_count})")
    return success_count, fail_count

# ================================================================================
# 추후 API 사용 불가될 시 (캡챠 도입 등) 사용하기 위한 브라우저(Selenium) 기반 코드 백업
# ================================================================================
# from selenium.webdriver.common.by import By
# from selenium.webdriver.support.ui import WebDriverWait
# from selenium.webdriver.support import expected_conditions as EC
#
# def run_batch_rating_selenium(driver, ids, score=5):
#     logger.LoggerFactory.logbot.info(f"일괄 별점 부여 시작 (총 {len(ids)}건, 점수: {score}점)")
#     success_count, fail_count = 0, 0
#     for report_id in ids:
#         try:
#             path = f"https://www.safetyreport.go.kr/html/common/popup/satisfaction.html?seq={report_id}&pn={settings.phone_number}"
#             logger.LoggerFactory.logbot.info(f"[{report_id}] 만족도 조사 직행 (JS 기반 우회 접속): {path}")
#             driver.execute_script(f"window.location.href='{path}';")
#             WebDriverWait(driver, 15).until(lambda d: d.execute_script('return document.readyState') == 'complete')
#             time.sleep(2)
#
#             try:
#                 WebDriverWait(driver, 2).until(EC.alert_is_present())
#                 alert = driver.switch_to.alert
#                 log_msg = alert.text
#                 alert.accept()
#                 logger.LoggerFactory.logbot.error(f"[{report_id}] 팝업 접근 거부됨: {log_msg}")
#                 fail_count += 1
#                 continue
#             except:
#                 pass
#
#             radio_xpath = f"//input[@name='STSFDG_SCORE' and @value='{score}']"
#             radio_btn = WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.XPATH, radio_xpath)))
#             driver.execute_script("arguments[0].click();", radio_btn)
#
#             try:
#                 phone_input = driver.find_element(By.XPATH, "//input[@name='TEL_NO' or @id='telNo' or @type='tel']")
#                 if phone_input and settings.phone_number:
#                     phone_input.clear()
#                     phone_input.send_keys(settings.phone_number)
#             except Exception:
#                 pass
#
#             submit_btn = driver.find_element(By.ID, "btn_submit")
#             driver.execute_script("arguments[0].click();", submit_btn)
#
#             try:
#                 WebDriverWait(driver, 3).until(EC.alert_is_present())
#                 alert = driver.switch_to.alert
#                 alert.accept()
#             except Exception:
#                 pass
#
#             logger.LoggerFactory.logbot.info(f"[{report_id}] {score}점 별점 부여 성공")
#             success_count += 1
#             time.sleep(1)
#         except Exception as e:
#             logger.LoggerFactory.logbot.error(f"[{report_id}] 별점 부여 중 오류 발생: {e}")
#             fail_count += 1
#     return success_count, fail_count
