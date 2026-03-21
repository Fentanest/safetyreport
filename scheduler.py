from apscheduler.schedulers.background import BackgroundScheduler
import settings.settings as app_settings
import subprocess
import sys
import os
import logger
from services.crawl_manager import crawl_manager

# 시스템 로컬 타임존 사용
scheduler = BackgroundScheduler()

def run_crawler():
    if crawl_manager.is_crawling():
        logger.LoggerFactory.logbot.warning("스케줄러: 이미 크롤링 실행 중. 건너뜁니다.")
        return

    logger.LoggerFactory.logbot.info("스케줄러에 의해 크롤러가 시작됩니다.")
    log_file = os.path.join(app_settings._instance.datapath, 'logs', 'current_crawl.log')
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    with open(log_file, 'w', encoding='utf-8') as f:
        f.write("=== 자동 스케줄러 크롤링 작업 시작 ===\n")

    cmd = [sys.executable, "-u", "start.py"]
    base_dir = os.path.abspath(os.path.dirname(__file__))
    
    if not crawl_manager.start_crawl(cmd, cwd=base_dir, log_file=log_file):
        logger.LoggerFactory.logbot.warning("스케줄러: 시작 직전 다른 프로세스 진입 발견됨. 건너뜁니다.")
        return

    p = crawl_manager.get_process()

    # Lock 해제 후 대청소 대기
    def wait_and_rotate_log(proc, lpath):
        if proc:
            proc.wait()
        crawl_manager.clear_process()
        import time, shutil, datetime
        time.sleep(1)
        if os.path.exists(lpath):
            now_str = datetime.datetime.now().strftime("%Y-%m-%d %H_%M_%S")
            dst = os.path.join(os.path.dirname(lpath), f"crawl_{now_str}.log")
            try:
                shutil.copy2(lpath, dst)
                with open(lpath, 'a', encoding='utf-8') as f:
                    f.write(f"\n[시스템] 자동 크롤링 작업이 성공적으로 종료되었습니다.\n전체 상세 로그는 {os.path.basename(dst)} 파일로 백업 보관되었습니다.\n")
            except Exception:
                pass

    import threading
    if p:
        threading.Thread(target=wait_and_rotate_log, args=(p, log_file), daemon=True).start()


def update_jobs():
    from apscheduler.triggers.cron import CronTrigger
    
    # 기존 모든 작업 제거
    all_jobs = scheduler.get_jobs()
    for job in all_jobs:
        scheduler.remove_job(job.id)
    
    # 설정 파일 직접 다시 읽기
    import configparser
    config = configparser.ConfigParser()
    config.read(app_settings.config_path)
    
    enabled = config.getboolean('SCHEDULER', 'enabled', fallback=False)
    if not enabled:
        logger.LoggerFactory.logbot.info("스케줄러가 비활성화되어 모든 작업을 제거했습니다.")
        return
        
    mode = config.get('SCHEDULER', 'mode', fallback='interval')
    logger.LoggerFactory.logbot.info(f"스케줄러 업데이트 시작 (모드: {mode})")
    
    if mode == 'interval':
        hours = config.getint('SCHEDULER', 'interval_hours', fallback=24)
        if hours > 0:
            scheduler.add_job(run_crawler, 'interval', hours=hours, id='crawl_job_interval')
            logger.LoggerFactory.logbot.info(f"스케줄러: {hours}시간 간격으로 실행 예약됨.")
    elif mode == 'cron':
        import re
        times_str = config.get('SCHEDULER', 'cron_times', fallback='')
        parts = re.split(r'[,\s;]+', times_str)
        
        valid_count = 0
        for t in parts:
            t = t.strip()
            if not t or valid_count >= 10:
                continue
                
            try:
                t_normalized = re.sub(r'[:;.!]', ':', t)
                if ':' in t_normalized:
                    h_str, m_str = t_normalized.split(':')
                    h, m = int(h_str), int(m_str)
                    
                    if 0 <= h < 24 and 0 <= m < 60:
                        # 별도 타임존 지정 없이 시스템 로컬 시각을 따름
                        trigger = CronTrigger(hour=h, minute=m)
                        scheduler.add_job(
                            run_crawler, 
                            trigger=trigger,
                            id=f'crawl_job_cron_{valid_count}',
                            misfire_grace_time=3600
                        )
                        logger.LoggerFactory.logbot.info(f"스케줄러 등록: 매일 {h:02d}:{m:02d} (시스템 시각 기준)")
                        valid_count += 1
            except Exception as e:
                logger.LoggerFactory.logbot.error(f"시간 파싱 실패 ({t}): {e}")

    # 최종 등록된 작업 목록 확인 로그
    final_jobs = scheduler.get_jobs()
    logger.LoggerFactory.logbot.info(f"현재 활성화된 스케줄러 작업 수: {len(final_jobs)}개")
    for j in final_jobs:
        logger.LoggerFactory.logbot.info(f" - 작업ID: {j.id}, 다음 실행예정: {j.next_run_time} (시스템 시각 기준)")

def init_scheduler():
    if not scheduler.running:
        scheduler.start()
    update_jobs()
