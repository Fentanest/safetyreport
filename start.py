from sqlalchemy import create_engine, inspect, text, select
import pandas as pd
import os
import sys
import subprocess
import time
import settings.settings as settings
from core.crawler import driv, login, crawltitle, crawldetail
from core.utils import logger
logger.LoggerFactory.create_logger(mode='crawl')
from core.database import database
from core.utils import export, message_formatter
from core.utils.path_utils import resource_path, is_frozen, enforce_utf8
enforce_utf8()

def _parse_args():
    args = {
        "force": '--force' in sys.argv,
        "reset": '--reset' in sys.argv,
        "min": '--min' in sys.argv,
        "nonmember": '--nonmember' in sys.argv,
        "queue_file": None,
        "page_range": None
    }
    
    if '--queue' in sys.argv:
        try:
            q_index = sys.argv.index('--queue')
            args["queue_file"] = sys.argv[q_index + 1]
        except (ValueError, IndexError):
            pass

    if '--p' in sys.argv:
        try:
            p_index = sys.argv.index('--p')
            range_str = sys.argv[p_index + 1]
            if ',' in range_str:
                args["page_range"] = list(map(int, range_str.split(',')))
            elif '-' in range_str:
                start, end = map(int, range_str.split('-'))
                args["page_range"] = list(range(start, end + 1))
            else:
                args["page_range"] = [int(range_str)]
        except (ValueError, IndexError):
            pass
    return args

def _validate_settings():
    if not os.path.exists(settings.resultpath):
        os.makedirs(settings.resultpath, exist_ok=True)

def _prepare_database(engine, reset=False):
    if reset:
        logger.LoggerFactory.logbot.warning("--reset 옵션이 사용되어 DB를 초기화합니다.")
        database.metadata.drop_all(engine)
    database.upgrade_schema(engine)

def extract_ids_from_queue(engine, queuelist):
    resolved_ids = []
    with engine.connect() as conn:
        for item in queuelist:
            item = item.strip()
            if not item: continue
            if item.startswith('SPP-') or '-' in item:
                query = select(database.title_table.c.ID).where(database.title_table.c.신고번호.like(f"%{item}%"))
                res = conn.execute(query).scalar()
                if res:
                    resolved_ids.append(res)
                else:
                    logger.LoggerFactory.logbot.warning(f"큐 신고번호 {item}의 ID를 찾을 수 없습니다.")
            else:
                resolved_ids.append(item)
    return resolved_ids

def _run_crawling_process(driver, engine, args):
    last_page = 0
    titlelist = []
    
    if args.get("queue_file"):
        logger.LoggerFactory.logbot.info("큐 지정 크롤링 모드입니다. 전체 목록 갱신을 건너뜁니다.")
    else:
        if args["page_range"]:
            logger.LoggerFactory.logbot.info(f"페이지 {args['page_range']} 크롤링 시작.")
            titlelist, last_page = crawltitle.crawl_titles(driver=driver, use_minimal_crawl=args["min"], page_range=args["page_range"])
        else:
            logger.LoggerFactory.logbot.info("전체 신고 목록 크롤링 시작.")
            titlelist, last_page = crawltitle.crawl_titles(driver=driver, use_minimal_crawl=args["min"])

        new_report_numbers = database.title_to_sql(dataframes=titlelist, engine=engine)
        if settings.telegram_enabled:
            msg = f"1/5. 신고 목록(Title) 수집 및 DB 저장을 완료했습니다. (총 {last_page} 페이지)\n"
            if new_report_numbers:
                msg += "\n[신규 추가된 신고번호]\n" + "\n".join(new_report_numbers[:30])
                if len(new_report_numbers) > 30:
                    msg += f"\n... 외 {len(new_report_numbers)-30}건"
            
            if is_frozen:
                subprocess.run([sys.executable, "--mode", "notify", msg])
            else:
                notifier_path = resource_path("core/utils/notifier.py")
                subprocess.run([sys.executable, notifier_path, msg])

    # Prepare detail list
    if args.get("queue_file"):
        with open(args["queue_file"], 'r', encoding='utf-8') as f:
            q_items = f.readlines()
        detaillist = extract_ids_from_queue(engine, q_items)
        logger.LoggerFactory.logbot.info(f"큐 파일에서 {len(detaillist)}개의 아이템 크롤링 시작.")
    elif args["page_range"]:
        detaillist = []
        for df in titlelist:
            detaillist.extend(df['ID'].tolist())
    else:
        detaillist = database.get_cNo(engine=engine, force=args["force"])

    if not detaillist:
        logger.LoggerFactory.logbot.info("크롤링할 상세 내역 없음.")
        return []

    logger.LoggerFactory.logbot.info(f"상세 크롤링 대상 ID: {len(detaillist)} 건 (순차 처리)")
    
    # 공격적인 멀티쓰레딩 대신 안정적인 단일 브라우저 순차 크롤링으로 복구
    detail_datas = list(crawldetail.crawl_details(driver=driver, list=detaillist))
        
    changed_item_ids = database.deatil_to_sql(dataframes_with_category=detail_datas, engine=engine)
    if settings.telegram_enabled:
        msg = f"2/5. 상세 정보(Detail) 크롤링 {len(detaillist)}건 및 DB 저장을 완료했습니다. (내용 변경/신규 처리: {len(changed_item_ids)}건)"
        if is_frozen:
            subprocess.run([sys.executable, "--mode", "notify", msg])
        else:
            notifier_path = resource_path("core/utils/notifier.py")
            subprocess.run([sys.executable, notifier_path, msg])
    
    return changed_item_ids

def _process_and_save_results(engine, changed_item_ids):
    logger.LoggerFactory.logbot.info("최종 데이터 병합 및 저장 시작")
    database.merge_final(engine=engine)
    database.clear_old_attachments(engine=engine)
    
    if settings.telegram_enabled:
        msg = "3/5. 최종 데이터 병합 및 DB 저장을 완료했습니다."
        if changed_item_ids:
            changed_records = database.get_merged_records_by_ids(engine, changed_item_ids)
            detail_msg = message_formatter.format_report_list(changed_records, "[내용 변경/신규 처리된 신고 목록 (병합 후)]")
            if detail_msg:
                msg += "\n\n" + detail_msg
        
        if is_frozen:
            subprocess.run([sys.executable, "--mode", "notify", msg])
        else:
            notifier_path = resource_path("core/utils/notifier.py")
            subprocess.run([sys.executable, notifier_path, msg])

    df = database.load_results(engine=engine)
    export.save_results(df=df)

def wait_for_resume_signal():
    logger.LoggerFactory.logbot.info("비회원 모드 대기 중... 브라우저에서 로그인 후 웹 UI의 '크롤링 재개'를 클릭하세요.")
    sig_file = os.path.join(settings.datapath, 'resume.sig')
    if os.path.exists(sig_file):
        os.remove(sig_file)
    while not os.path.exists(sig_file):
        time.sleep(2)
    os.remove(sig_file)
    logger.LoggerFactory.logbot.info("'크롤링 재개' 신호 수신됨. 작업을 계속합니다.")

def main():
    args = _parse_args()
    _validate_settings()
    engine = create_engine(f'sqlite:///{settings.db_path}')
    _prepare_database(engine, reset=args["reset"])

    driver = None
    try:
        driver = driv.create_driver()
        driver.get(settings.loginurl)
        
        if args["nonmember"]:
            wait_for_resume_signal()
        else:
            login.login_mysafety(driver=driver)
            if settings.telegram_enabled:
                if is_frozen:
                    subprocess.run([sys.executable, "--mode", "notify", "안전신문고 로그인에 성공했습니다."])
                else:
                    notifier_path = resource_path("core/utils/notifier.py")
                    subprocess.run([sys.executable, notifier_path, "안전신문고 로그인에 성공했습니다."])

        changed_item_ids = _run_crawling_process(driver, engine, args)
    except Exception as e:
        logger.LoggerFactory.logbot.error(f"실행 중 치명적 오류 발생: {e}")
        changed_item_ids = []
    finally:
        if driver:
            driver.quit()

    try:
        _process_and_save_results(engine, changed_item_ids)
        logger.LoggerFactory.logbot.info("====== 크롤링 작업 완료 ======")
    except Exception as e:
        logger.LoggerFactory.logbot.error(f"저장 중 오류 발생: {e}")

if __name__ == "__main__":
    main()
