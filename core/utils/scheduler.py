from apscheduler.schedulers.background import BackgroundScheduler
import settings.settings as app_settings
from core.utils import logger
from services import crawl_control
from services.crawl_manager import crawl_manager

# 시스템 로컬 타임존 사용
scheduler = BackgroundScheduler()

def run_crawler():
    if crawl_manager.is_crawling():
        logger.LoggerFactory.logbot.warning("스케줄러: 이미 크롤링 실행 중. 건너뜁니다.")
        return

    # 커뮤니티 게이트·초기화 확인(T3b): 미충족이면 시작하지 않는다(T3a 모듈이 없으면 검사 생략).
    try:
        from services import community_gate as _gate

        fresh = _gate.require_fresh(max_age=60.0) if hasattr(_gate, "require_fresh") else None
        if fresh is not None and not fresh.get("can_enter"):
            logger.LoggerFactory.logbot.warning("스케줄러: 커뮤니티 게이트 미충족. 건너뜁니다.")
            return
    except Exception:
        pass
    try:
        from services import community_rebuild as _rebuild

        if _rebuild.required() or _rebuild.blocking_state() is not None:
            logger.LoggerFactory.logbot.warning("스케줄러: 초기화 크롤 필요·진행 중. 건너뜁니다.")
            return
    except Exception:
        pass

    logger.LoggerFactory.logbot.info("스케줄러에 의해 크롤러가 시작됩니다.")
    try:
        crawl_control.start_crawl(
            crawl_mode="full",
            header="=== 자동 스케줄러 크롤링 작업 시작 ===",
            broadcast_source="scheduler",
        )
    except RuntimeError:
        logger.LoggerFactory.logbot.warning("스케줄러: 시작 직전 다른 프로세스 진입 발견됨. 건너뜁니다.")
    except Exception as exc:
        logger.LoggerFactory.logbot.error(f"스케줄러 크롤링 시작 실패: {exc}")


import re as _re

_CRAWL_JOB_INTERVAL_ID = "crawl_job_interval"
_CRON_JOB_PATTERN = _re.compile(r"^cron_\d{2}_\d{2}$")


def is_crawl_job_id(job_id: str) -> bool:
    """크롤 스케줄러가 소유한 job id. 커뮤니티 job 은 여기서 지우지 않는다(S-08)."""
    return job_id == _CRAWL_JOB_INTERVAL_ID or bool(_CRON_JOB_PATTERN.match(str(job_id or "")))


def _register_community_jobs():
    try:
        from services import community_schedule as _schedule
    except ImportError:
        logger.LoggerFactory.logbot.debug("스케줄러: community_schedule 없음 — 커뮤니티 job 등록 생략.")
        return
    try:
        _schedule.register_community_jobs(scheduler)
    except Exception as exc:
        logger.LoggerFactory.logbot.warning(f"스케줄러: 커뮤니티 job 등록 실패: {exc}")


def update_jobs():
    from apscheduler.triggers.cron import CronTrigger

    # 크롤 job 만 제거·재생성한다. 커뮤니티 job(community-midnight-upload 등)은 유지(S-08).
    for job in list(scheduler.get_jobs()):
        if is_crawl_job_id(job.id):
            scheduler.remove_job(job.id)
    logger.LoggerFactory.logbot.info("스케줄러: 크롤 작업을 제거하고 설정을 초기화했습니다(커뮤니티 작업 유지).")

    # 설정 파일 직접 다시 읽기
    import configparser
    config = configparser.ConfigParser()
    config.read(app_settings.config_path)

    enabled = config.getboolean('SCHEDULER', 'enabled', fallback=False)
    if not enabled:
        logger.LoggerFactory.logbot.info("스케줄러가 비활성화되어 크롤 작업을 제거했습니다(커뮤니티 작업 유지).")
        _register_community_jobs()
        return
        
    mode = config.get('SCHEDULER', 'mode', fallback='interval')
    logger.LoggerFactory.logbot.info(f"스케줄러 업데이트 시작 (모드: {mode})")
    
    if mode == 'interval':
        hours = config.getint('SCHEDULER', 'interval_hours', fallback=24)
        start_time_str = config.get('SCHEDULER', 'interval_start', fallback='00:00')
        
        if hours > 0:
            import datetime
            try:
                h_str, m_str = start_time_str.split(':')
                h, m = int(h_str), int(m_str)
                
                # 오늘 혹은 내일의 지정된 시각으로 시작 시각 설정
                now = datetime.datetime.now()
                start_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
                
                # 이미 지난 시각이면 APScheduler가 자동으로 처리하거나, 명시적으로 다음 실행 시각을 조정할 수 있음
                # 여기서는 start_date를 그대로 전달 (이미 지났으면 즉시 혹은 다음 주기에 실행됨)
                
                scheduler.add_job(
                    run_crawler, 
                    'interval', 
                    hours=hours, 
                    id='crawl_job_interval', 
                    start_date=start_dt
                )
                logger.LoggerFactory.logbot.info(f"스케줄러: {hours}시간 간격으로 실행 예약됨. (시작 기준 시각: {start_time_str})")
            except Exception as e:
                logger.LoggerFactory.logbot.error(f"간격 시작 시각 파싱 실패 ({start_time_str}): {e}")
                # 파싱 실패 시 기본 동작 (즉시 시작)
                scheduler.add_job(run_crawler, 'interval', hours=hours, id='crawl_job_interval')
                logger.LoggerFactory.logbot.info(f"스케줄러: {hours}시간 간격으로 즉시 실행 예약됨 (시작 시각 파싱 실패).")
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
                        job_id = f'cron_{h:02d}_{m:02d}'
                        trigger = CronTrigger(hour=h, minute=m)
                        scheduler.add_job(
                            run_crawler, 
                            trigger=trigger,
                            id=job_id,
                            misfire_grace_time=3600
                        )
                        logger.LoggerFactory.logbot.info(f"스케줄러 등록: 매일 {h:02d}:{m:02d} (ID: {job_id}, 시스템 시각 기준)")
                        valid_count += 1
            except Exception as e:
                logger.LoggerFactory.logbot.error(f"시간 파싱 실패 ({t}): {e}")

    # 커뮤니티 job 존재를 끝에서 재확인(멱등 등록).
    _register_community_jobs()

    # 최종 등록된 작업 목록 확인 로그
    final_jobs = scheduler.get_jobs()
    logger.LoggerFactory.logbot.info(f"현재 활성화된 스케줄러 작업 수: {len(final_jobs)}개")
    for j in final_jobs:
        # 미기동 스케줄러의 job 에는 next_run_time 속성이 없다(실행 예약 전).
        logger.LoggerFactory.logbot.info(f" - 작업ID: {j.id}, 다음 실행예정: {getattr(j, 'next_run_time', None)} (시스템 시각 기준)")

def init_scheduler():
    if not scheduler.running:
        scheduler.start()
    update_jobs()
