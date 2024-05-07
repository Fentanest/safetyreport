import settings.settings as settings
import pandas as pd
from sqlalchemy import text
import os
import gspread
from gspread_dataframe import set_with_dataframe
from gspread.exceptions import WorksheetNotFound
import logger
import alert_utils

gc = gspread.service_account(settings.google_api_auth_file)  # 구글 스프레드에 연결
spreadsheet = gc.open_by_key(settings.google_sheet_key)

## DB에서 링크번호 가져오기
def get_cNo(engine, conn):
    with engine.connect() as conn:
        row_count_query = text(f"SELECT COUNT(*) FROM {settings.table_detail}")
        row_count = conn.execute(row_count_query).fetchone()[0]
        if row_count == 0:
            logger.LoggerFactory.logbot.info("detail 테이블 비어 있어 전체 스캔 시작")
            Query_String = f"SELECT ID FROM {settings.table_title} WHERE 상태 != '취하'"
            df = pd.read_sql_query(Query_String, conn)
        else:
            logger.LoggerFactory.logbot.info("신규, 미종결 신고 건 스캔 시작")
            query_new = text(f'''
                            SELECT t.ID FROM {settings.table_title} t
                            WHERE NOT EXISTS
                            (SELECT 1 FROM {settings.table_detail} r
                            WHERE t.ID = r.ID)
                            ;''')
            query_notend = f"SELECT ID FROM {settings.table_detail} WHERE 종결여부 != 'Y'"
            df_a = pd.read_sql_query(query_new, conn) # 게시판 리스트에서 긁어온 신규 건 추출
            df_b = pd.read_sql_query(query_notend, conn) # 기존 개별 신고 리스트 중 미종결된 건 추출
            df = pd.merge(df_a, df_b, how="outer")
        df = df.values.tolist()
        logger.LoggerFactory.logbot.debug("스캔대상 ID 리스트화 완료")
        
        detaillist = []
        for ID in df:
            ID = str(ID).replace("[","").replace("]","")
            detaillist.append(ID)
        logger.LoggerFactory.logbot.debug("대상 ID 리스트 :")
        logger.LoggerFactory.logbot.debug(detaillist)
        logger.LoggerFactory.logbot.info(f"스캔대상 ID 총 {len(detaillist)}건")
        return(detaillist)

def opendata_from_gc(engine, conn):
    # opendata 시트를 불러오고 실패 시 기본양식으로 생성
    try:
        worksheet = spreadsheet.worksheet("opendata")
        logger.LoggerFactory.logbot.debug("opendata시트를 선택합니다.")
    except WorksheetNotFound:
        logger.LoggerFactory.logbot.warning("opendata시트가 확인되지 않습니다.")
        worksheet = spreadsheet.add_worksheet(title="opendata", rows="1000", cols="20")
        worksheet.update('A1', "'ID")
        worksheet.update('B1', "'신고번호")
        worksheet.update('C1', "'공개결과")
        logger.LoggerFactory.logbot.info("opendata시트를 생성합니다.")
    
    # 구글시트 opendata 전체 긁어오기
    values = worksheet.get_all_values()
    logger.LoggerFactory.logbot.debug("정보공개청구 결과 내용 복사 완료")
    
    # 헤더와 내용으로 구분
    header, rows = values[0], values[1:]
    header = (header[0], header[1], header[2]) # 1행 헤더는 튜플로 만들어 바로 쿼리 삽입
    rows = [str(tuple(row)) for row in rows] # 2행부터의 내용은 행별로 튜플로 만들어서 아래 쿼리로 삽입
    
    with engine.connect() as conn:
        Query_String = text(f'''
                            INSERT OR IGNORE INTO {settings.table_opendata} {header} VALUES
                            {', '.join(rows)}
                            ;''') # 튜플로 변환한 rows의 각 행을 원소로 ,join
        conn.execute(Query_String)
        logger.LoggerFactory.logbot.debug("정보공개청구 결과 입력 중")
        conn.commit()
        logger.LoggerFactory.logbot.info("정보공개청구 결과 테이블 입력 성공")

# 게시판 페이지마다 긁어온 리스트 받아서 db로 밀어넣기
def title_to_sql(dataframes, engine, conn):
    i = 0 # 카운트
    with engine.connect() as conn:
        for df in dataframes:
            df.to_sql(settings.table_title_temp, conn, if_exists='append', index=False)
            i += 1
            logger.LoggerFactory.logbot.debug(f"{i}건 임시 리스트 테이블 밀어넣기 완료")
    logger.LoggerFactory.logbot.info(f"총 {i}건 데이터 밀어넣기 완료")

# 개별 신고건 긁어온 내용 건 별로 받아서 db로 밀어넣기
def deatil_to_sql(dataframes, engine, conn):    
    i = 0 # 카운트
    with engine.connect() as conn:
        for df in dataframes:
            df.to_sql(settings.table_detail_temp, conn, if_exists='append', index=False)
            i += 1
            logger.LoggerFactory.logbot.debug(f"{i}건 개별 신고 결과 임시 테이블 밀어넣기 완료")
    logger.LoggerFactory.logbot.info(f"총 {i}건 데이터 밀어넣기 완료")

# title, detail 정리된 후 합치기
def merge_from_sql(engine, conn):
    with engine.connect() as conn:
        Query_load_title = f'SELECT * FROM {settings.table_title}'
        Query_load_detail = f'SELECT * FROM {settings.table_detail}'
        Query_load_opendata = f'SELECT ID, 공개결과 FROM {settings.table_opendata}'
        
        df_title = pd.read_sql_query(Query_load_title, conn)
        logger.LoggerFactory.logbot.debug("게시판 리스트 테이블 불러오기 성공")
        df_detail = pd.read_sql_query(Query_load_detail, conn)
        logger.LoggerFactory.logbot.debug("개별 신고 결과 테이블 불러오기 성공")
        df_opendata = pd.read_sql_query(Query_load_opendata, conn)
        logger.LoggerFactory.logbot.debug("정보공개청구 결과 테이블 불러오기 성공")
        
        df_middle = pd.DataFrame(pd.merge(df_title, df_detail, on="ID", how="left").sort_values(by="신고번호", ascending=False))
        df_final = pd.DataFrame(pd.merge(df_middle, df_opendata, on="ID", how="left").sort_values(by="신고번호", ascending=False))
        logger.LoggerFactory.logbot.debug("title_table, detail_table, opendata_table 병합 완료")
        logger.LoggerFactory.logbot.debug(df_final)        
        
        df_final.to_sql(settings.table_merge_temp, conn, if_exists='replace', index=False)
        conn.commit()
        logger.LoggerFactory.logbot.info("title, detail, opendata 병합 데이터 temp테이블 작성 성공")

def load_results(engine, conn):
    with engine.connect() as conn:
        Query_load_results = f'SELECT * FROM {settings.table_merge} ORDER BY ID DESC;'
        df = pd.DataFrame(pd.read_sql_query(Query_load_results, conn))
        return(df)

def save_results(df):
    df.to_excel(os.path.join(settings.path, settings.resultfile), index=False)
    logger.LoggerFactory.logbot.info(f"데이터 엑셀 저장 성공, 저장경로 : {os.path.join(settings.path, settings.resultfile)}")

    try:
        worksheet = spreadsheet.worksheet("data")
        logger.LoggerFactory.logbot.debug("data시트를 선택합니다.")
    except WorksheetNotFound:
        logger.LoggerFactory.logbot.warning("data시트가 확인되지 않습니다.")
        worksheet = spreadsheet.add_worksheet(title="data", rows="1000", cols="20")
        logger.LoggerFactory.logbot.info("data시트를 생성합니다.")
    
    worksheet.clear()
    logger.LoggerFactory.logbot.debug("기존 구글 스프레드시트 데이터를 삭제합니다.")
    
    set_with_dataframe(worksheet=worksheet, dataframe=df, 
                        include_index=False, include_column_header=True, 
                        resize=True, string_escaping='full')
    logger.LoggerFactory.logbot.info("구글 스프레드시트에 새로운 데이터를 성공적으로 입력하였습니다.")
    alert_utils()