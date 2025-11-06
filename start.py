from sqlalchemy import create_engine, inspect, text
import os
import sys
import settings.settings as settings
import driv
import login
import crawltitle
import crawldetail
import items
import logger

def parse_args():
    args = {
        "force": '--force' in sys.argv,
        "forceall": '--forceall' in sys.argv,
        "min": '--min' in sys.argv,
        "page_range": None
    }
    for arg in sys.argv:
        if arg.startswith('--p:'):
            try:
                range_str = arg.split(':')[1]
                if '-' in range_str:
                    start, end = map(int, range_str.split('-'))
                    args["page_range"] = list(range(start, end + 1))
                else:
                    args["page_range"] = [int(range_str)]
            except (ValueError, IndexError):
                logger.LoggerFactory.logbot.error(f"페이지 범위 인수 형식이 잘못되었습니다: {arg}. '--p:5' 또는 '--p:5-7' 형식으로 사용하세요.")
                sys.exit(1)
    return args

def main():
    # --- 초기화 ---
    logger.LoggerFactory.create_logger()
    args = parse_args()

    # --- 설정 확인 ---
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

    # --- DB 준비 및 마이그레이션 ---
    engine = create_engine(f'sqlite:///{settings.db_path}')
    
    if args["forceall"]:
        logger.LoggerFactory.logbot.warning("--forceall 옵션이 사용되어 DB를 초기화합니다.")
        items.metadata.drop_all(engine)

    inspector = inspect(engine)
    with engine.connect() as connection:
        existing_tables = inspector.get_table_names()
        for table in items.metadata.sorted_tables:
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

    # --- 크롤링 시작 ---
    driver = driv.create_driver()
    login.login_mysafety(driver=driver)

    detaillist = []
    if args["page_range"]:
        logger.LoggerFactory.logbot.info(f"페이지 {args['page_range']}에 대한 크롤링을 시작합니다.")
        # 페이지 지정 크롤링 시에는 title 크롤링으로 바로 대상 ID 리스트를 가져옴
        titlelist = list(crawltitle.Crawling_title(driver=driver, use_minimal_crawl=args["min"], page_range=args["page_range"]))
        items.title_to_sql(dataframes=titlelist, engine=engine)
        # titlelist는 DataFrame의 list이므로, ID만 추출해야 함
        for df in titlelist:
            detaillist.extend(df['ID'].tolist())
        logger.LoggerFactory.logbot.info(f"페이지 지정 크롤링 대상 ID {len(detaillist)}건을 수집했습니다.")
    else:
        logger.LoggerFactory.logbot.info("전체 신고 목록 업데이트를 시작합니다.")
        titlelist = list(crawltitle.Crawling_title(driver=driver, use_minimal_crawl=args["min"]))
        items.title_to_sql(dataframes=titlelist, engine=engine)
        logger.LoggerFactory.logbot.info("크롤링 대상 ID를 DB에서 가져옵니다.")
        detaillist = items.get_cNo(engine=engine, force=args["force"])

    if not detaillist:
        logger.LoggerFactory.logbot.info("새로 크롤링할 상세 신고 내역이 없습니다.")
    else:
        detail_datas = list(crawldetail.Crawling_detail(driver=driver, list=detaillist))
        items.deatil_to_sql(dataframes=detail_datas, engine=engine)

    # --- 후처리 및 저장 ---
    logger.LoggerFactory.logbot.info("최종 데이터 병합 및 저장을 시작합니다.")
    items.merge_final(engine=engine)
    items.clear_old_attachments(engine=engine)
    df = items.load_results(engine=engine)
    items.save_results(df=df)

    # --- 종료 ---
    logger.LoggerFactory.logbot.info("모든 작업 완료, 텔레그램으로 알림 발송")
    if settings.telegram_enabled:
        import subprocess
        subprocess.run([sys.executable, "notifier.py", "백그라운드 크롤링 및 저장이 완료되었습니다."])
    
    driver.quit()

if __name__ == "__main__":
    main()