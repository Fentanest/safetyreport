import settings.settings as settings
import pandas as pd
from sqlalchemy import Table, MetaData, Column, String, select, func, exists, update
from sqlalchemy.dialects.sqlite import insert
import logger
from datetime import datetime
from dateutil.relativedelta import relativedelta

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
                     Column('종결여부', String),
                     Column('신고내용', String),
                     Column('처리내용', String),
                     Column('지도', String),
                     Column('첨부사진', String),
                     Column('첨부파일', String))



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
                    Column('신고내용', String),
                    Column('처리내용', String),
                    Column('지도', String),
                    Column('첨부사진', String),
                    Column('첨부파일', String))


def _get_all_title_ids(conn):
    """Returns all IDs from the title_table."""
    logger.LoggerFactory.logbot.info("전체 신고 건을 다시 스캔합니다.")
    query = select(title_table.c.ID)
    return pd.read_sql_query(query, conn)

def _get_initial_scan_ids(conn):
    """Returns IDs for the initial scan (when detail_table is empty)."""
    logger.LoggerFactory.logbot.info("detail 테이블 비어 있어 전체 스캔 시작")
    query = select(title_table.c.ID).where(title_table.c.상태 != '취하')
    return pd.read_sql_query(query, conn)

def _get_new_and_incomplete_ids(conn):
    """Returns new IDs and IDs of incomplete reports."""
    logger.LoggerFactory.logbot.info("신규, 미종결 신고 건 스캔 시작")
    query_new = select(title_table.c.ID).where(
        ~exists().where(title_table.c.ID == detail_table.c.ID)
    )
    query_notend = select(detail_table.c.ID).where(detail_table.c.종결여부 != 'Y')
    df_a = pd.read_sql_query(query_new, conn)
    df_b = pd.read_sql_query(query_notend, conn)
    return pd.merge(df_a, df_b, how="outer")

def get_cNo(engine, force=False):
    """
    Determines and returns a list of report IDs to be crawled.
    - force=True: All IDs from the title table.
    - detail table is empty: All IDs except those marked '취하'.
    - Otherwise: New and incomplete report IDs.
    """
    with engine.connect() as conn:
        if force:
            df = _get_all_title_ids(conn)
        else:
            row_count = conn.execute(select(func.count()).select_from(detail_table)).scalar()
            if row_count == 0:
                df = _get_initial_scan_ids(conn)
            else:
                df = _get_new_and_incomplete_ids(conn)
        
        if df.empty:
            return []

        df_sorted = df.sort_values(by='ID', ascending=True)
        detaillist = df_sorted['ID'].tolist()
        logger.LoggerFactory.logbot.debug("스캔대상 ID 리스트화 완료")
        logger.LoggerFactory.logbot.debug("대상 ID 리스트 : %s", detaillist)
        logger.LoggerFactory.logbot.info(f"스캔대상 ID 총 {len(detaillist)}건")
        return detaillist


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
                '종결여부': insert_stmt.excluded.종결여부,
                '신고내용': insert_stmt.excluded.신고내용,
                '처리내용': insert_stmt.excluded.처리내용,
                '지도': insert_stmt.excluded.지도,
                '첨부사진': insert_stmt.excluded.첨부사진,
                '첨부파일': insert_stmt.excluded.첨부파일
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

def search_by_car_number(engine, car_number: str):
    """Searches for reports by car number in the merge_table."""
    with engine.connect() as conn:
        # Use the LIKE operator for a "contains" search
        query = select(merge_table).where(merge_table.c.차량번호.like(f"%{car_number}%"))
        result = conn.execute(query)
        rows = result.fetchall()
        if not rows:
            return []
        
        # Convert rows to list of dictionaries
        column_names = result.keys()
        results_as_dict = [dict(zip(column_names, row)) for row in rows]
        return results_as_dict

def search_by_report_number(engine, report_number: str):
    """Searches for reports by report number in the merge_table."""
    with engine.connect() as conn:
        # Use the LIKE operator for a "contains" search
        query = select(merge_table).where(merge_table.c.신고번호.like(f"%{report_number}%"))
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

        # Join title and detail tables
        j = title_table.join(detail_table, title_table.c.ID == detail_table.c.ID, isouter=True)

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
            func.coalesce(detail_table.c.신고내용, '').label('신고내용'),
            func.coalesce(detail_table.c.처리내용, '').label('처리내용'),
            func.coalesce(detail_table.c.지도, '').label('지도'),
            func.coalesce(detail_table.c.첨부사진, '').label('첨부사진'),
            func.coalesce(detail_table.c.첨부파일, '').label('첨부파일')
        ).select_from(j)

        # Insert the result of the join into the merge_table
        insert_stmt = merge_table.insert().from_select(
            [c.name for c in merge_table.c],
            select_stmt
        )
        conn.execute(insert_stmt)
        logger.LoggerFactory.logbot.debug("신규병합 데이터 추가")

        conn.commit()
        logger.LoggerFactory.logbot.info("최종 데이터 병합 완료")

def clear_old_attachments(engine):
    """Clears attachment links for reports older than 6 months."""
    six_months_ago = datetime.now() - relativedelta(months=6)
    six_months_ago_str = six_months_ago.strftime('%Y-%m-%d')

    stmt = (
        update(merge_table)
        .where(merge_table.c.신고일 < six_months_ago_str)
        .values(
            지도="6개월 초과",
            첨부사진="6개월 초과",
            첨부파일="6개월 초과"
        )
    )

    with engine.connect() as conn:
        result = conn.execute(stmt)
        conn.commit()
        logger.LoggerFactory.logbot.info(f"6개월 초과된 신고 {result.rowcount}건의 첨부파일 링크를 정리했습니다.")