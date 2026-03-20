from apscheduler.schedulers.background import BackgroundScheduler
import settings.settings as app_settings
import subprocess
import sys
import logger

scheduler = BackgroundScheduler()

def run_crawler():
    logger.LoggerFactory.logbot.info("스케줄러에 의해 크롤러가 시작됩니다.")
    log_file = os.path.join(app_settings.datapath, 'logs', 'current_crawl.log')
    with open(log_file, 'w', encoding='utf-8') as f:
        f.write("=== 자동 스케줄러 크롤링 작업 시작 ===\n")
        
    p = subprocess.Popen(
        [sys.executable, "-u", "start.py"],
        stdout=open(log_file, 'a', encoding='utf-8'),
        stderr=subprocess.STDOUT,
        text=True
    )
    
    def wait_and_rotate_log(proc, lpath):
        proc.wait()
        import time, shutil, datetime
        time.sleep(1)
        if os.path.exists(lpath):
            now_str = datetime.datetime.now().strftime("%Y-%m-%d %H_%M_%S")
            dst = os.path.join(os.path.dirname(lpath), f"crawl_{now_str}.log")
            try:
                shutil.copy2(lpath, dst)
                with open(lpath, 'w', encoding='utf-8') as f:
                    f.write(f"\n[시스템] 자동 크롤링 작업이 성공적으로 종료되었습니다.\n전체 상세 로그는 {os.path.basename(dst)} 파일로 백업 보관되었습니다.\n")
            except Exception:
                pass

    import threading
    threading.Thread(target=wait_and_rotate_log, args=(p, log_file), daemon=True).start()

def update_jobs():
    scheduler.remove_all_jobs()
    
    # Reload settings logically in case it changed via web UI
    import configparser
    config = configparser.ConfigParser()
    config.read(app_settings.config_path)
    
    enabled = config.getboolean('SETTINGS', 'scheduler_enabled', fallback=False)
    if not enabled:
        logger.LoggerFactory.logbot.info("스케줄러가 비활성화되었습니다.")
        return
        
    mode = config.get('SETTINGS', 'scheduler_mode', fallback='interval')
    
    if mode == 'interval':
        hours = config.getint('SETTINGS', 'scheduler_interval_hours', fallback=24)
        if hours > 0:
            scheduler.add_job(run_crawler, 'interval', hours=hours, id='crawl_job_interval')
            logger.LoggerFactory.logbot.info(f"스케줄러: {hours}시간마다 크롤링하도록 설정되었습니다.")
    elif mode == 'cron':
        times_str = config.get('SETTINGS', 'scheduler_cron_times', fallback='')
        times = [t.strip() for t in times_str.split(',') if t.strip()]
        for i, t in enumerate(times[:10]): # max 10
            try:
                hour, minute = t.split(':')
                scheduler.add_job(run_crawler, 'cron', hour=int(hour), minute=int(minute), id=f'crawl_job_cron_{i}')
                logger.LoggerFactory.logbot.info(f"스케줄러: 매일 {hour}시 {minute}분에 크롤링 예약됨.")
            except ValueError:
                logger.LoggerFactory.logbot.error(f"잘못된 시간 형식: {t}. HH:MM 형식이어야 합니다.")

def init_scheduler():
    if not scheduler.running:
        scheduler.start()
    update_jobs()
