import time
import requests
import settings.settings as settings
from core.utils import logger
from core.database import database
from sqlalchemy import create_engine

def run_batch_rating(ids, score=5, log_file=None):
    engine = create_engine(f'sqlite:///{settings.db_path}', connect_args={"check_same_thread": False})
    """
    ids: List of report IDs to rate (SPP-...)
    score: 1 to 5
    log_file: Optional path to write real-time logs for UI
    """
    def write_log(msg):
        if log_file:
            try:
                with open(log_file, 'a', encoding='utf-8') as f:
                    f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
            except Exception:
                pass

    logger.LoggerFactory.star_log.info(f"API 기반 일괄 별점 부여 시작 (총 {len(ids)}건, 점수: {score}점)")
    write_log(f"=== 별점 처리 작업 시작 (요청 횟수: {len(ids)}건, 목표 점수: {score}점) ===")
    
    existing_records = database.get_merged_records_by_ids(engine, ids)
    already_rated_ids = [rec['신고번호'] for rec in existing_records if rec.get('만족도조사여부') in ('참여 완료', '참여 불가')]
    ids_to_process = [rid for rid in ids if rid not in already_rated_ids]
    
    skip_count = len(already_rated_ids)
    if skip_count > 0:
        msg = f"DB 사전 확인: 이미 완료/불가 처리된 {skip_count}건을 로컬에서 스킵합니다. (실제 요청: {len(ids_to_process)}건)"
        logger.LoggerFactory.star_log.info(msg)
        write_log(msg)
    
    success_count = 0
    fail_count = 0
    total = len(ids_to_process)
    
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Origin": "https://www.safetyreport.go.kr",
        "X-Requested-With": "XMLHttpRequest"
    })

    # TCP RST 우회: 첫 연결 전 워밍업 요청 (RST 대비 2회 시도)
    for _ in range(2):
        try:
            session.get("https://www.safetyreport.go.kr/", timeout=5)
            break
        except Exception:
            pass

    for idx, report_id in enumerate(ids_to_process, 1):
        progress = f"[{idx}/{total}]"
        write_log(f"{progress} {report_id} 처리 중...")
        
        success = False
        for attempt in range(settings.max_retry_attemps + 1):
            if attempt > 0:
                msg = f"  - [재시도 {attempt}/{settings.max_retry_attemps}] {report_id} 다시 시도 중... ({settings.retry_interval}초 대기)"
                logger.LoggerFactory.star_log.warning(msg)
                write_log(msg)
                time.sleep(settings.retry_interval)
            
            try:
                # 1. 상태 확인 (이미 별점을 주었는지)
                check_url = f"https://www.safetyreport.go.kr/api/v1/portal/statistics/satisfactionstatistics/score/{report_id}/{settings.phone_number}"
                session.headers.update({"Referer": f"https://www.safetyreport.go.kr/html/common/popup/satisfaction.html?seq={report_id}&pn={settings.phone_number}"})
                check_res = session.get(check_url, timeout=10)
                if check_res.status_code == 200:
                    data = check_res.json()
                    if not data.get("result"):
                        msg = f"[{report_id}] 대상 신고건이 없거나 폰 번호가 맞지 않습니다."
                        logger.LoggerFactory.star_log.error(msg)
                        write_log(f"  - 실패: {msg}")
                        fail_count += 1
                        success = True # 중단용 (재시도 의미 없음)
                        break
                    if data["result"].get("STSFDG_SCORE", 0) != 0:
                        msg = f"[{report_id}] 이미 만족도 조사에 참여하셨습니다."
                        logger.LoggerFactory.star_log.warning(msg)
                        write_log(f"  - 스킵: {msg}")
                        
                        try:
                            database.sync_rating_status(engine, report_id)
                        except Exception as db_e:
                            logger.LoggerFactory.star_log.error(f"[{report_id}] DB 갱신 실패: {db_e}")
                            
                        skip_count += 1
                        success = True # 중단용 (재시도 의미 없음)
                        break
                else:
                    raise requests.exceptions.RequestException(f"체크 실패 (HTTP {check_res.status_code})")
                
                # 2. 별점 제출
                post_url = "https://www.safetyreport.go.kr/api/v1/portal/statistics/satisfactionstatistics"
                payload = {
                    "STTEMNT_NO": report_id,
                    "C_PHONE2": settings.phone_number,
                    "STSFDG_SCORE": str(score),
                    "STSFDG_CAUSE": ""
                }
                
                post_res = session.post(post_url, data=payload, timeout=10)
                if post_res.status_code == 200:
                    msg = f"[{report_id}] {score}점 별점 부여 성공"
                    logger.LoggerFactory.star_log.info(msg + " (API)")
                    write_log(f"  - {msg}")
                    
                    try:
                        database.sync_rating_status(engine, report_id)
                    except Exception as db_e:
                        logger.LoggerFactory.star_log.error(f"[{report_id}] DB 갱신 실패: {db_e}")
                        
                    success_count += 1
                    success = True
                    break
                else:
                    raise requests.exceptions.RequestException(f"제출 실패 (HTTP {post_res.status_code})")
                    
            except Exception as e:
                msg = f"[{report_id}] 오류 발생: {e}"
                logger.LoggerFactory.star_log.error(msg)
                if attempt < settings.max_retry_attemps:
                    write_log(f"  - 오류: {msg} (재시도 예정)")
                else:
                    write_log(f"  - 최종 실패: {msg}")
                    fail_count += 1
        
        time.sleep(1) # 부하 방지
            
    final_msg = f"별점 처리가 종료되었습니다. (성공: {success_count}, 스킵: {skip_count}, 실패: {fail_count})"
    logger.LoggerFactory.star_log.info(final_msg)
    write_log(f"\n=== {final_msg} ===")
    return success_count, fail_count

# ================================================================================
# 추후 API 사용 불가될 시 (캡챠 도입 등) 사용하기 위한 브라우저(Selenium) 기반 코드 백업
# ================================================================================
# from selenium.webdriver.common.by import By
# from selenium.webdriver.support.ui import WebDriverWait
# from selenium.webdriver.support import expected_conditions as EC
#
# def run_batch_rating_selenium(driver, ids, score=5):
#     logger.LoggerFactory.star_log.info(f"일괄 별점 부여 시작 (총 {len(ids)}건, 점수: {score}점)")
#     success_count, fail_count = 0, 0
#     for report_id in ids:
#         try:
#             path = f"https://www.safetyreport.go.kr/html/common/popup/satisfaction.html?seq={report_id}&pn={settings.phone_number}"
#             logger.LoggerFactory.star_log.info(f"[{report_id}] 만족도 조사 직행 (JS 기반 우회 접속): {path}")
#             driver.execute_script(f"window.location.href='{path}';")
#             WebDriverWait(driver, 15).until(lambda d: d.execute_script('return document.readyState') == 'complete')
#             time.sleep(2)
#
#             try:
#                 WebDriverWait(driver, 2).until(EC.alert_is_present())
#                 alert = driver.switch_to.alert
#                 log_msg = alert.text
#                 alert.accept()
#                 logger.LoggerFactory.star_log.error(f"[{report_id}] 팝업 접근 거부됨: {log_msg}")
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
#             logger.LoggerFactory.star_log.info(f"[{report_id}] {score}점 별점 부여 성공")
#             success_count += 1
#             time.sleep(1)
#         except Exception as e:
#             logger.LoggerFactory.star_log.error(f"[{report_id}] 별점 부여 중 오류 발생: {e}")
#             fail_count += 1
#     return success_count, fail_count
