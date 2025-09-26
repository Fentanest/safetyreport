from sqlalchemy import create_engine, inspect
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
if not os.path.exists(settings.path):
    logger.LoggerFactory.logbot.warning("결과 저장 경로 없음")
    logger.LoggerFactory.logbot.info("결과 저장 경로 생성")
    os.mkdir(settings.path)
else:
    logger.LoggerFactory.logbot.info("결과 저장 경로 있음")

# DB준비
engine = create_engine(f'sqlite:///{os.path.join(settings.path, settings.db)}')
inspector = inspect(engine)

required_tables = [settings.table_title, settings.table_detail, settings.table_merge, settings.table_opendata]
existing_tables = inspector.get_table_names()

if not all(table in existing_tables for table in required_tables):
    logger.LoggerFactory.logbot.info("DB 테이블 설정 시작")
    items.metadata.drop_all(engine)
    items.metadata.create_all(engine)
    logger.LoggerFactory.logbot.info("DB 테이블 설정 완료")
else:
    logger.LoggerFactory.logbot.info("DB 정상 확인")

# 크롬 열기
driver = driv.create_driver()

# 로그인
login.login_mysafety(driver=driver)

# 게시판 리스트 크롤링, 개별 ID 확보 후 temp테이블과 병합
titlelist = list(crawltitle.Crawling_title(driver=driver))
items.title_to_sql(dataframes=titlelist, engine=engine, conn=None) # conn is not used

# 개별 신고 건 크롤링, 상태 저장 후 temp테이블과 병합
detaillist = items.get_cNo(engine=engine, conn=None) # conn is not used
detail_datas = list(crawldetail.Crawling_detail(driver=driver, list=detaillist))

items.deatil_to_sql(dataframes=detail_datas, engine=engine, conn=None) # conn is not used

# 스프레드시트의 정보공개청구 결과 긁어와서 최종 병합
items.opendata_from_gc(engine=engine, conn=None) # conn is not used

items.merge_final(engine=engine, conn=None) # conn is not used
df = items.load_results(engine=engine, conn=None) # conn is not used
items.save_results(df=df)

import subprocess

# 셀레니움 종료
logger.LoggerFactory.logbot.info("모든 작업 완료, 텔레그램으로 알림 발송")
subprocess.run([sys.executable, "notifier.py", "백그라운드 크롤링 및 저장이 완료되었습니다."])
driver.quit()
