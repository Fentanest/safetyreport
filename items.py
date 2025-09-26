import settings.settings as settings
import pandas as pd
from sqlalchemy import text, Table, MetaData, Column, String, select, func, exists
from sqlalchemy.dialects.sqlite import insert
import os
import gspread
from gspread_dataframe import set_with_dataframe
from gspread.exceptions import WorksheetNotFound
import logger

if settings.google_sheet_enabled:
    gc = gspread.service_account(settings.google_api_auth_file)  # 구글 스프레드에 연결
    spreadsheet = gc.open_by_key(settings.google_sheet_key)
else:
    gc = None
    spreadsheet = None

metadata = MetaData()

# Define table structures
title_table = Table(settings.table_title, metadata,
                    Column('ID', String, primary_key=True),
                    Column('상태', String),
                    Column('신고번호', String),
                    Column('신고명', String),
                    Column('신고일', String))

detail_table = Table(settings.table_detail, metadata,
                     Column('ID', String, primary_key=True),
                     Column('처리상태', String),
                     Column('차량번호', String),
                     Column('위반법규', String),
                     Column('범칙금_과태료', String),
                     Column('벌점', String),
                     Column('처리기관', String),
                     Column('담당자', String),
                     Column('답변일', String),
                     Column('발생일자', String),
                     Column('발생시각', String),
                     Column('위반장소', String),
                     Column('종결여부', String))

opendata_table = Table(settings.table_opendata, metadata,
                      Column('ID', String, primary_key=True),
                      Column('신고번호', String),
                      Column('공개결과', String))

merge_table = Table(settings.table_merge, metadata,
                    Column('ID', String, primary_key=True),
                    Column('상태', String),
                    Column('신고번호', String),
                    Column('신고명', String),
                    Column('신고일', String),
                    Column('처리상태', String),
                    Column('차량번호', String),
                    Column('위반법규', String),
                    Column('범칙금_과태료', String),
                    Column('벌점', String),
                    Column('처리기관', String),
                    Column('담당자', String),
                    Column('답변일', String),
                    Column('발생일자', String),
                    Column('발생시각', String),
                    Column('위반장소', String),
                    Column('종결여부', String),
                    Column('공개결과', String))


## DB에서 링크번호 가져오기
def get_cNo(engine, conn=None):
    with engine.connect() as conn:
        row_count_query = select(func.count()).select_from(detail_table)
        row_count = conn.execute(row_count_query).scalar()
        if row_count == 0:
            logger.LoggerFactory.logbot.info("detail 테이블 비어 있어 전체 스캔 시작")
            query = select(title_table.c.ID).where(title_table.c.상태 != '취하')
            df = pd.read_sql_query(query, conn)
        else:
            logger.LoggerFactory.logbot.info("신규, 미종결 신고 건 스캔 시작")
            query_new = select(title_table.c.ID).where(
                ~exists().where(title_table.c.ID == detail_table.c.ID)
            )
            query_notend = select(detail_table.c.ID).where(detail_table.c.종결여부 != 'Y')
            df_a = pd.read_sql_query(query_new, conn)
            df_b = pd.read_sql_query(query_notend, conn)
            df = pd.merge(df_a, df_b, how="outer")
        df_sorted = df.sort_values(by='ID', ascending=True)
        detaillist = df_sorted['ID'].tolist()
        logger.LoggerFactory.logbot.debug("스캔대상 ID 리스트화 완료")

        logger.LoggerFactory.logbot.debug("대상 ID 리스트 :")
        logger.LoggerFactory.logbot.debug(detaillist)
        logger.LoggerFactory.logbot.info(f"스캔대상 ID 총 {len(detaillist)}건")
        return (detaillist)

def opendata_from_gc(engine, conn=None):
    if not settings.google_sheet_enabled:
        logger.LoggerFactory.logbot.info("Google Sheet 기능이 비활성화되어 opendata_from_gc를 건너뜁니다.")
        return
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

    values = worksheet.get_all_values()
    logger.LoggerFactory.logbot.debug("정보공개청구 결과 내용 복사 완료")

    header, rows = values[0], values[1:]
    rows_to_insert = [dict(zip(header, row)) for row in rows]

    with engine.connect() as conn:
        if rows_to_insert:
            insert_stmt = insert(opendata_table).values(rows_to_insert)
            upsert_query = insert_stmt.on_conflict_do_nothing(index_elements=['ID'])
            conn.execute(upsert_query)
            logger.LoggerFactory.logbot.debug("정보공개청구 결과 입력 중")
            conn.commit()
            logger.LoggerFactory.logbot.info("정보공개청구 결과 테이블 입력 성공")

# 게시판 페이지마다 긁어온 리스트 받아서 db로 밀어넣기
def title_to_sql(dataframes, engine, conn=None):
    i = 0 # 카운트
    with engine.connect() as conn:
        for df in dataframes:
            records = df.to_dict('records')
            if not records:
                continue
            
            insert_stmt = insert(title_table).values(records)
            
            update_dict = {
                '상태': insert_stmt.excluded.상태,
                '신고번호': insert_stmt.excluded.신고번호,
                '신고명': insert_stmt.excluded.신고명,
                '신고일': insert_stmt.excluded.신고일
            }
            
            upsert_query = insert_stmt.on_conflict_do_update(
                index_elements=['ID'],
                set_=update_dict
            )
            
            conn.execute(upsert_query)
            i += len(records)
        conn.commit()
    logger.LoggerFactory.logbot.info(f"총 {i}건 title 테이블 upsert 완료")

def deatil_to_sql(dataframes, engine, conn=None):
    i = 0 # 카운트
    with engine.connect() as conn:
        for df in dataframes:
            records = df.to_dict('records')
            if not records:
                continue
            
            insert_stmt = insert(detail_table).values(records)
            
            update_dict = {
                '처리상태': insert_stmt.excluded.처리상태,
                '차량번호': insert_stmt.excluded.차량번호,
                '위반법규': insert_stmt.excluded.위반법규,
                '범칙금_과태료': insert_stmt.excluded.범칙금_과태료,
                '벌점': insert_stmt.excluded.벌점,
                '처리기관': insert_stmt.excluded.처리기관,
                '담당자': insert_stmt.excluded.담당자,
                '답변일': insert_stmt.excluded.답변일,
                '발생일자': insert_stmt.excluded.발생일자,
                '발생시각': insert_stmt.excluded.발생시각,
                '위반장소': insert_stmt.excluded.위반장소,
                '종결여부': insert_stmt.excluded.종결여부
            }
            
            upsert_query = insert_stmt.on_conflict_do_update(
                index_elements=['ID'],
                set_=update_dict
            )
            
            conn.execute(upsert_query)
            i += len(records)
        conn.commit()
    logger.LoggerFactory.logbot.info(f"총 {i}건 detail 테이블 upsert 완료")

def load_results(engine, conn=None):
    with engine.connect() as conn:
        query = select(merge_table).order_by(merge_table.c.ID.desc())
        df = pd.DataFrame(pd.read_sql_query(query, conn))
        return (df)

def save_results(df):
    df.to_excel(os.path.join(settings.path, settings.resultfile), index=False)
    logger.LoggerFactory.logbot.info(f"데이터 엑셀 저장 성공, 저장경로 : {os.path.join(settings.path, settings.resultfile)}")

    if not settings.google_sheet_enabled:
        logger.LoggerFactory.logbot.info("Google Sheet 기능이 비활성화되어 구글 시트 저장을 건너뜁니다.")
        return

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

def search_by_car_number(engine, car_number: str):
    """Searches for reports by car number in the merge_table."""
    with engine.connect() as conn:
        query = select(merge_table).where(merge_table.c.차량번호 == car_number)
        result = conn.execute(query)
        rows = result.fetchall()
        if not rows:
            return []
        
        # Convert rows to list of dictionaries
        column_names = result.keys()
        results_as_dict = [dict(zip(column_names, row)) for row in rows]
        return results_as_dict

def merge_final(engine, conn=None):
    with engine.connect() as conn:
        # --- 디버깅 코드 추가 ---
        row_count_title = conn.execute(select(func.count()).select_from(title_table)).scalar()
        row_count_detail = conn.execute(select(func.count()).select_from(detail_table)).scalar()
        logger.LoggerFactory.logbot.info(f"merge_final 시작: title 테이블에 {row_count_title}개 행, detail 테이블에 {row_count_detail}개 행이 보입니다.")
        # -------------------------

        # First, clear the merge_table
        conn.execute(merge_table.delete())

        # Join title, detail, and opendata tables
        j = title_table.join(detail_table, title_table.c.ID == detail_table.c.ID, isouter=True)\
                         .join(opendata_table, title_table.c.ID == opendata_table.c.ID, isouter=True)

        # Select all columns for the final merge
        select_stmt = select(
            title_table.c.ID,
            title_table.c.상태,
            title_table.c.신고번호,
            title_table.c.신고명,
            title_table.c.신고일,
            func.coalesce(detail_table.c.처리상태, '').label('처리상태'),
            func.coalesce(detail_table.c.차량번호, '').label('차량번호'),
            func.coalesce(detail_table.c.위반법규, '').label('위반법규'),
            func.coalesce(detail_table.c.범칙금_과태료, '').label('범칙금_과태료'),
            func.coalesce(detail_table.c.벌점, '').label('벌점'),
            func.coalesce(detail_table.c.처리기관, '').label('처리기관'),
            func.coalesce(detail_table.c.담당자, '').label('담당자'),
            func.coalesce(detail_table.c.답변일, '').label('답변일'),
            func.coalesce(detail_table.c.발생일자, '').label('발생일자'),
            func.coalesce(detail_table.c.발생시각, '').label('발생시각'),
            func.coalesce(detail_table.c.위반장소, '').label('위반장소'),
            func.coalesce(detail_table.c.종결여부, '').label('종결여부'),
            func.coalesce(opendata_table.c.공개결과, '').label('공개결과')
        ).select_from(j)

        # Insert the result of the join into the merge_table
        insert_stmt = merge_table.insert().from_select(
            [c.name for c in merge_table.c],
            select_stmt
        )
        conn.execute(insert_stmt)
        logger.LoggerFactory.logbot.debug("신규병합 데이터 추가")

        # Update regret
        update_regret_query = merge_table.update().\
            where(merge_table.c.처리상태 == '불수용').\
            values(공개결과='불수용')
        conn.execute(update_regret_query)

        conn.commit()
        logger.LoggerFactory.logbot.info("최종 데이터 병합 완료")