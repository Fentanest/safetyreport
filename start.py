from sqlalchemy import create_engine, inspect, text
import os
import sys
import subprocess
import settings.settings as settings
import driv
import login
import crawltitle
import crawldetail
import database, export, message_formatter
import logger

logger.LoggerFactory.create_logger()

def _parse_args():
    args = {
        "force": '--force' in sys.argv,
        "reset": '--reset' in sys.argv,
        "min": '--min' in sys.argv,
        "page_range": None
    }
    for arg in sys.argv:
        if arg.startswith('--p:'):
            try:
                range_str = arg.split(':')[1]
                if ',' in range_str:
                    args["page_range"] = list(map(int, range_str.split(',')))
                elif '-' in range_str:
                    start, end = map(int, range_str.split('-'))
                    args["page_range"] = list(range(start, end + 1))
                else:
                    args["page_range"] = [int(range_str)]
            except (ValueError, IndexError):
                logger.LoggerFactory.logbot.error(f"페이지 범위 인수 형식이 잘못되었습니다: {arg}. '--p:5' 또는 '--p:5-7' 형식으로 사용하세요.")
                sys.exit(1)
    return args

def _validate_settings():
    """Validates necessary settings."""
    variables_to_check = [
        ("username", "ID가 올바르게 입력되지 않았습니다."),
        ("password", "PW가 올바르게 입력되지 않았습니다."),
        ("remotepath", "Selenium Grid 주소 확인이 필요합니다.")
    ]
    for var_name, error_message in variables_to_check:
        if getattr(settings, var_name) in ["nousername", "nopassword", "nonpath", None, ""]:
            logger.LoggerFactory.logbot.critical(error_message)
            sys.exit(1)
    
    if settings.google_sheet_enabled and settings.google_sheet_key in ["nosheetkey", None, ""]:
        logger.LoggerFactory.logbot.critical("Google Spreadsheet Key 확인이 필요합니다.")
        sys.exit(1)

    if not os.path.exists(settings.resultpath):
        os.makedirs(settings.resultpath, exist_ok=True)

def _prepare_database(engine, reset=False):
    """Initializes and migrates the database schema."""
    if reset:
        logger.LoggerFactory.logbot.warning("--reset 옵션이 사용되어 DB를 초기화합니다.")
        database.metadata.drop_all(engine)

    inspector = inspect(engine)
    with engine.connect() as connection:
        existing_tables = inspector.get_table_names()
        for table in database.metadata.sorted_tables:
            if table.name not in existing_tables:
                logger.LoggerFactory.logbot.info(f"테이블 '{table.name}'이(가) 존재하지 않아 새로 생성합니다.")
                table.create(connection)
            else:
                logger.LoggerFactory.logbot.info(f"테이블 '{table.name}'의 구조를 확인 및 업데이트합니다.")
                existing_columns = [col['name'] for col in inspector.get_columns(table.name)]
                for column in table.columns:
                    if column.name not in existing_columns:
                        logger.LoggerFactory.logbot.warning(f"'{table.name}' 테이블에 '{column.name}' 열이 없어 추가합니다.")
                        column_type = column.type.compile(engine.dialect)
                        alter_query = text(f'ALTER TABLE {table.name} ADD COLUMN {column.name} {column_type}')
                        connection.execute(alter_query)
        connection.commit()
    logger.LoggerFactory.logbot.info("DB 테이블 구조 확인 및 업데이트 완료.")

def _run_crawling_process(driver, engine, args):
    """Runs the main crawling and data insertion process."""
    
    last_page = 0
    if args["page_range"]:
        logger.LoggerFactory.logbot.info(f"페이지 {args['page_range']}에 대한 크롤링을 시작합니다.")
        titlelist, last_page = crawltitle.crawl_titles(driver=driver, use_minimal_crawl=args["min"], page_range=args["page_range"])
    else:
        logger.LoggerFactory.logbot.info("전체 신고 목록 업데이트를 시작합니다.")
        titlelist, last_page = crawltitle.crawl_titles(driver=driver, use_minimal_crawl=args["min"])

    new_report_numbers = database.title_to_sql(dataframes=titlelist, engine=engine)
    if settings.telegram_enabled:
        message = f"1/5. 신고 목록(title) 크롤링(총 {last_page} 페이지) 및 DB 저장을 완료했습니다."
        if new_report_numbers:
            message += f"\n\n[신규 추가된 신고번호]\n" + "\n".join(new_report_numbers)
        subprocess.run([sys.executable, "notifier.py", message])

    if args["page_range"]:
        detaillist = []
        for df in titlelist:
            detaillist.extend(df['ID'].tolist())
        logger.LoggerFactory.logbot.info(f"페이지 지정 크롤링 대상 ID {len(detaillist)}건을 수집했습니다.")
    else:
        logger.LoggerFactory.logbot.info("크롤링 대상 ID를 DB에서 가져옵니다.")
        detaillist = database.get_cNo(engine=engine, force=args["force"])

    if not detaillist:
        logger.LoggerFactory.logbot.info("새로 크롤링할 상세 신고 내역이 없습니다.")
        return [] # Return empty list if no details were processed
    else:
        detail_datas = list(crawldetail.crawl_details(driver=driver, list=detaillist))
        changed_item_ids = database.deatil_to_sql(dataframes=detail_datas, engine=engine)
        
        if settings.telegram_enabled:
            simple_message = f"2/5. 신고 상세(detail) 크롤링 {len(detaillist)}건 및 DB 저장을 완료했습니다."
            if changed_item_ids:
                simple_message += f" (내용 변경/신규 처리: {len(changed_item_ids)}건)"
            subprocess.run([sys.executable, "notifier.py", simple_message])
        
        return changed_item_ids

def _process_and_save_results(engine, changed_item_ids):
    """Merges, cleans, and saves the final results."""
    logger.LoggerFactory.logbot.info("최종 데이터 병합 및 저장을 시작합니다.")
    database.merge_final(engine=engine)
    if settings.telegram_enabled:
        subprocess.run([sys.executable, "notifier.py", "3/5. 최종 데이터 병합 및 DB 저장을 완료했습니다."])
    
    # Send notification for changed items after merge
    if changed_item_ids and settings.telegram_enabled:
        with engine.connect() as conn:
            # Query merge_table for the full records of changed items
            query = select(database.merge_table).where(database.merge_table.c.ID.in_(changed_item_ids))
            changed_records = pd.read_sql_query(query, conn).to_dict('records')
        
        if changed_records:
            title = f"[내용 변경/신규 처리된 신고 목록 (병합 후)]"
            full_message = message_formatter.format_report_list(changed_records, title)
            if full_message:
                subprocess.run([sys.executable, "notifier.py", full_message])
    database.clear_old_attachments(engine=engine)
    df = database.load_results(engine=engine)
    export.save_results(df=df)

def main():
    """Main function to run the crawling process."""
    # --- Initialization ---
    args = _parse_args()

    # --- Settings Validation ---
    _validate_settings()

    # --- DB Preparation ---
    engine = create_engine(f'sqlite:///{settings.db_path}')
    _prepare_database(engine, reset=args["reset"])

    # --- Crawling ---
    driver = None
    try:
        driver = driv.create_driver()
        login.login_mysafety(driver=driver)
        changed_item_ids = _run_crawling_process(driver, engine, args)
    finally:
        if driver:
            driver.quit()

    # --- Post-processing and Saving ---
    _process_and_save_results(engine, changed_item_ids)



if __name__ == "__main__":
    main()
