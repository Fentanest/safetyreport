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

logger.LoggerFactory.create_logger()

# 설정 변수 리스트
variables_to_check = [
    ("username", "ID가 올바르게 입력되지 않았습니다."),
    ("password", "PW가 올바르게 입력되지 않았습니다."),
    ("remotepath", "Selenium Grid 주소 확인이 필요합니다.")
]

# 설정 변수 확인
for var_name, error_message in variables_to_check:
    if getattr(settings, var_name) in ["nousername", "nopassword", "nonpath"]:
        logger.LoggerFactory.logbot.critical(error_message)
        sys.exit(1)
    else:
        logger.LoggerFactory.logbot.debug(f"{var_name} 값 확인")

if settings.google_sheet_enabled:
    if settings.google_sheet_key in ["nosheetkey", None, ""]:
        logger.LoggerFactory.logbot.critical("Google Spreadsheet Key 확인이 필요합니다.")
        sys.exit(1)
    else:
        logger.LoggerFactory.logbot.debug("Sheet_KEY 값 확인")
else:
    logger.LoggerFactory.logbot.info("Google Sheet 기능이 비활성화되어 있습니다.")

# 결과저장 폴더 있는지 확인
if not os.path.exists(settings.resultpath):
    logger.LoggerFactory.logbot.warning("결과 저장 경로 없음")
    logger.LoggerFactory.logbot.info("결과 저장 경로 생성")
    os.makedirs(settings.resultpath, exist_ok=True)
else:
    logger.LoggerFactory.logbot.info("결과 저장 경로 있음")

# DB준비 및 마이그레이션
engine = create_engine(f'sqlite:///{settings.db_path}')
inspector = inspect(engine)

with engine.connect() as connection:
    existing_tables = inspector.get_table_names()
    for table in items.metadata.sorted_tables:
        table_name = table.name
        if table_name not in existing_tables:
            logger.LoggerFactory.logbot.info(f"테이블 '{table_name}'이(가) 존재하지 않아 새로 생성합니다.")
            table.create(connection)
        else:
            logger.LoggerFactory.logbot.info(f"테이블 '{table_name}'의 구조를 확인 및 업데이트합니다.")
            existing_columns = [col['name'] for col in inspector.get_columns(table_name)]
            for column in table.columns:
                if column.name not in existing_columns:
                    logger.LoggerFactory.logbot.warning(f"'{table_name}' 테이블에 '{column.name}' 열이 없어 추가합니다.")
                    column_type = column.type.compile(engine.dialect)
                    alter_query = text(f'ALTER TABLE {table_name} ADD COLUMN {column.name} {column_type}')
                    connection.execute(alter_query)
    connection.commit()
logger.LoggerFactory.logbot.info("DB 테이블 구조 확인 및 업데이트 완료.")

# 크롬 열기
driver = driv.create_driver()

# 로그인
login.login_mysafety(driver=driver)

# 게시판 리스트 크롤링, 개별 ID 확보 후 기록
use_minimal_crawl = '--min' in sys.argv
titlelist = list(crawltitle.Crawling_title(driver=driver, use_minimal_crawl=use_minimal_crawl))
items.title_to_sql(dataframes=titlelist, engine=engine, conn=None) # conn is not used

# 개별 신고 건 크롤링, 상태 저장 후 기록
detaillist = items.get_cNo(engine=engine, conn=None) # conn is not used
detail_datas = list(crawldetail.Crawling_detail(driver=driver, list=detaillist))

items.deatil_to_sql(dataframes=detail_datas, engine=engine, conn=None) # conn is not used



items.merge_final(engine=engine, conn=None) # conn is not used
df = items.load_results(engine=engine, conn=None) # conn is not used
items.save_results(df=df)

import subprocess

# 셀레니움 종료
logger.LoggerFactory.logbot.info("모든 작업 완료, 텔레그램으로 알림 발송")
subprocess.run([sys.executable, "notifier.py", "백그라운드 크롤링 및 저장이 완료되었습니다."])
driver.quit()
