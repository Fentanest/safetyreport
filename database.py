import settings.settings as settings
import pandas as pd
from sqlalchemy import select, func, exists, update, text, inspect
from sqlalchemy.dialects.sqlite import insert
import logger
from datetime import datetime
from dateutil.relativedelta import relativedelta

from database_models import *

def upgrade_schema(engine):
    inspector = inspect(engine)
    with engine.connect() as connection:
        try:
            connection.execute(text("PRAGMA journal_mode=WAL;"))
            logger.LoggerFactory.logbot.debug("SQLite WAL 모드 활성화됨 (동시성 최적화)")
        except Exception:
            pass
            
        existing_tables = inspector.get_table_names()
        for table in metadata.sorted_tables:
            if table.name not in existing_tables:
                logger.LoggerFactory.logbot.info(f"테이블 '{table.name}' 생성 중...")
                table.create(connection)
                if table.name == 'mysafety_watchlist':
                    if settings.table_merge_traffic in existing_tables and settings.table_merge_other in existing_tables:
                        try:
                            migrate_query = text(f"""
                                INSERT OR IGNORE INTO mysafety_watchlist (신고번호)
                                SELECT 신고번호 FROM {settings.table_merge_traffic} WHERE 감시목록 = 'Y'
                                UNION
                                SELECT 신고번호 FROM {settings.table_merge_other} WHERE 감시목록 = 'Y'
                            """)
                            connection.execute(migrate_query)
                            logger.LoggerFactory.logbot.info("기존 감시목록 데이터를 완벽하게 이관했습니다.")
                        except Exception as e:
                            logger.LoggerFactory.logbot.error(f"감시목록 데이터 이관 중 오류 발생: {e}")
            else:
                existing_columns = [col['name'] for col in inspector.get_columns(table.name)]
                for column in table.columns:
                    if column.name not in existing_columns:
                        logger.LoggerFactory.logbot.warning(f"'{table.name}' 테이블에 '{column.name}' 컬럼을 추가합니다.")
                        column_type = column.type.compile(engine.dialect)
                        alter_query = text(f'ALTER TABLE {table.name} ADD COLUMN {column.name} {column_type}')
                        try:
                            connection.execute(alter_query)
                        except Exception as e:
                            logger.LoggerFactory.logbot.error(f"스키마 업그레이드 오류: {e}")
        connection.commit()

def _get_all_title_ids(conn):
    logger.LoggerFactory.logbot.info("전체 신고 건을 다시 스캔합니다.")
    query = select(title_table.c.ID)
    return pd.read_sql_query(query, conn)

def _get_initial_scan_ids(conn):
    logger.LoggerFactory.logbot.info("detail 테이블 비어 있어 전체 스캔 시작")
    query = select(title_table.c.ID)
    return pd.read_sql_query(query, conn)

def _get_new_and_incomplete_ids(conn):
    logger.LoggerFactory.logbot.info("신규, 미종결 신고 건 스캔 시작")
    query_new = select(title_table.c.ID).where(
        ~exists().where(title_table.c.ID == detail_traffic_table.c.ID)
    ).where(
        ~exists().where(title_table.c.ID == detail_other_table.c.ID)
    )
    
    # items where state has changed between title and detail
    query_changed_traffic = select(title_table.c.ID).select_from(
        title_table.join(detail_traffic_table, title_table.c.ID == detail_traffic_table.c.ID)
    ).where(
        (title_table.c.상태 != detail_traffic_table.c.처리상태) &
        (detail_traffic_table.c.종결여부 != 'Y')
    )

    query_changed_other = select(title_table.c.ID).select_from(
        title_table.join(detail_other_table, title_table.c.ID == detail_other_table.c.ID)
    ).where(
        (title_table.c.상태 != detail_other_table.c.처리상태) &
        (detail_other_table.c.종결여부 != 'Y')
    )
    
    df_new = pd.read_sql_query(query_new, conn)
    df_changed_t = pd.read_sql_query(query_changed_traffic, conn)
    df_changed_o = pd.read_sql_query(query_changed_other, conn)
    
    merged = pd.concat([df_new, df_changed_t, df_changed_o]).drop_duplicates()
    return merged

def get_cNo(engine, force=False):
    with engine.connect() as conn:
        if force:
            df = _get_all_title_ids(conn)
        else:
            row_count_t = conn.execute(select(func.count()).select_from(detail_traffic_table)).scalar()
            row_count_o = conn.execute(select(func.count()).select_from(detail_other_table)).scalar()
            
            if row_count_t + row_count_o == 0:
                df = _get_initial_scan_ids(conn)
            else:
                df = _get_new_and_incomplete_ids(conn)
        
        if df.empty:
            return []

        df_sorted = df.sort_values(by='ID', ascending=True)
        detaillist = df_sorted['ID'].tolist()
        logger.LoggerFactory.logbot.debug("스캔대상 ID 리스트화 완료")
        logger.LoggerFactory.logbot.info(f"스캔대상 ID 총 {len(detaillist)}건")
        return detaillist

def title_to_sql(dataframes, engine, conn=None):
    if not dataframes:
        return []

    combined_df = pd.concat(dataframes, ignore_index=True)
    if combined_df.empty:
        return []

    if '만족도조사여부' not in combined_df.columns:
        combined_df['만족도조사여부'] = ""
    if '감시목록' not in combined_df.columns:
        combined_df['감시목록'] = "N"

    incoming_ids = combined_df['ID'].tolist()
    new_report_numbers = []

    with engine.connect() as conn:
        existing_ids_query = select(title_table.c.ID).where(title_table.c.ID.in_(incoming_ids))
        existing_ids = set(pd.read_sql(existing_ids_query, conn)['ID'])

        new_df = combined_df[~combined_df['ID'].isin(existing_ids)]
        if not new_df.empty:
            new_report_numbers = new_df['신고번호'].tolist()

        records = combined_df.to_dict('records')
        insert_stmt = insert(title_table).values(records)

        from sqlalchemy import case as sa_case
        # 만족도조사여부: 새 값이 비어있으면 기존 값을 유지 (재크롤링 시 덮어쓰기 방지)
        poll_update = sa_case(
            (insert_stmt.excluded.만족도조사여부 != '', insert_stmt.excluded.만족도조사여부),
            else_=title_table.c.만족도조사여부
        )
        update_dict = {
            '상태': insert_stmt.excluded.상태,
            '신고번호': insert_stmt.excluded.신고번호,
            '신고명': insert_stmt.excluded.신고명,
            '신고일': insert_stmt.excluded.신고일,
            '만족도조사여부': poll_update,
        }
        upsert_query = insert_stmt.on_conflict_do_update(
            index_elements=['ID'],
            set_=update_dict
        )
        conn.execute(upsert_query)
        conn.commit()

    logger.LoggerFactory.logbot.info(f"총 {len(combined_df)}건 title 테이블 upsert 완료. (신규: {len(new_report_numbers)}건)")
    return new_report_numbers

def deatil_to_sql(dataframes_with_category, engine, conn=None):
    if not dataframes_with_category:
        return []

    changed_item_ids = []
    total_records = 0

    with engine.connect() as conn:
        for df, category in dataframes_with_category:
            target_table = detail_traffic_table if category == "traffic" else detail_other_table
            
            records = df.to_dict('records')
            if not records:
                continue
            
            new_record = records[0]
            record_id = new_record['ID']
            total_records += 1

            select_stmt = select(target_table).where(target_table.c.ID == record_id)
            existing_record_proxy = conn.execute(select_stmt).first()

            is_new = existing_record_proxy is None
            is_changed = False

            if not is_new:
                existing_record = dict(existing_record_proxy._mapping)
                for key, new_value in new_record.items():
                    if key in existing_record and str(existing_record[key]) != str(new_value):
                        is_changed = True
                        break
                
                if is_changed:
                    changed_item_ids.append(record_id)
            
            insert_stmt = insert(target_table).values(new_record)
            update_dict = {col.name: getattr(insert_stmt.excluded, col.name) for col in target_table.c if col.name != 'ID'}
            
            upsert_query = insert_stmt.on_conflict_do_update(
                index_elements=['ID'],
                set_=update_dict
            )
            conn.execute(upsert_query)
        
        conn.commit()

    logger.LoggerFactory.logbot.info(f"총 {total_records}건 detail 테이블 upsert 완료. (변경/신규: {len(changed_item_ids)}건)")
    return changed_item_ids

def _merge_for_table(conn, merge_target, detail_source):
    conn.execute(merge_target.delete())
    j_inner = title_table.join(detail_source, title_table.c.ID == detail_source.c.ID)

    select_stmt = select(
        title_table.c.ID,
        title_table.c.상태,
        title_table.c.신고번호,
        title_table.c.신고명,
        title_table.c.신고일,
        title_table.c.만족도조사여부,
        title_table.c.감시목록,
        detail_source.c.처리상태,
        detail_source.c.차량번호,
        detail_source.c.위반법규,
        detail_source.c.범칙금_과태료,
        detail_source.c.벌점,
        detail_source.c.처리기관,
        detail_source.c.담당자,
        detail_source.c.답변일,
        detail_source.c.발생일자,
        detail_source.c.발생시각,
        detail_source.c.위반장소,
        detail_source.c.종결여부,
        detail_source.c.신고내용,
        detail_source.c.처리내용,
        detail_source.c.지도,
        detail_source.c.첨부사진,
        detail_source.c.첨부파일
    ).select_from(j_inner)

    insert_stmt = merge_target.insert().from_select([c.name for c in merge_target.c], select_stmt)
    conn.execute(insert_stmt)

def merge_final(engine, conn=None):
    with engine.connect() as conn:
        _merge_for_table(conn, merge_traffic_table, detail_traffic_table)
        _merge_for_table(conn, merge_other_table, detail_other_table)
        conn.commit()
        logger.LoggerFactory.logbot.info("최종 데이터 병합 완료 (Traffic/Other 분리)")

def clear_old_attachments(engine):
    six_months_ago = datetime.now() - relativedelta(months=6)
    six_months_ago_str = six_months_ago.strftime('%Y-%m-%d')

    with engine.connect() as conn:
        for t in [merge_traffic_table, merge_other_table]:
            stmt = (
                update(t)
                .where(t.c.신고일 < six_months_ago_str)
                .values(
                    지도="6개월 초과",
                    첨부사진="6개월 초과",
                    첨부파일="6개월 초과"
                )
            )
            conn.execute(stmt)
        conn.commit()

def load_results(engine, conn=None):
    with engine.connect() as conn:
        query_t = select(merge_traffic_table)
        query_o = select(merge_other_table)
        df_t = pd.DataFrame(pd.read_sql_query(query_t, conn))
        df_o = pd.DataFrame(pd.read_sql_query(query_o, conn))
        df = pd.concat([df_t, df_o]) if not df_t.empty or not df_o.empty else pd.DataFrame()
        
        if not df.empty:
            # 엑셀/구글 시트 내보내기 시에도 감시목록 '★' 여부를 정확히 렌더링하기 위한 조인 복구
            df_watch = pd.read_sql_query(select(watchlist_table.c.신고번호), conn)
            watch_ids = set(df_watch['신고번호'].tolist())
            df['감시목록'] = df['신고번호'].apply(lambda x: 'Y' if x in watch_ids else 'N')
            
            if settings.exclude_withdraw:
                df = df[df['처리상태'] != '취하']
            
            if settings.normalize_police and '처리기관' in df.columns:
                def norm_police(x):
                    x = str(x)
                    if '경찰서' in x:
                        return x.split('경찰서')[0].split()[-1] + '경찰서'
                    return x
                df['처리기관'] = df['처리기관'].apply(norm_police)
            
        return df

def get_merged_records_by_ids(engine, id_list):
    if not id_list:
        return []
    res = []
    with engine.connect() as conn:
        for t in [merge_traffic_table, merge_other_table]:
            query = select(t).where(t.c.ID.in_(id_list))
            result = conn.execute(query)
            rows = result.fetchall()
            if rows:
                col_names = result.keys()
                res.extend([dict(zip(col_names, row)) for row in rows])
    return res

def search_by_car_number(engine, car_number: str):
    res = []
    with engine.connect() as conn:
        for t in [merge_traffic_table, merge_other_table]:
            query = select(t).where(t.c.차량번호.like(f"%{car_number}%"))
            result = conn.execute(query)
            rows = result.fetchall()
            if rows:
                col_names = result.keys()
                res.extend([dict(zip(col_names, row)) for row in rows])
    return res

def search_by_report_number(engine, report_number: str):
    res = []
    with engine.connect() as conn:
        for t in [merge_traffic_table, merge_other_table]:
            query = select(t).where(t.c.신고번호.like(f"%{report_number}%"))
            result = conn.execute(query)
            rows = result.fetchall()
            if rows:
                col_names = result.keys()
                res.extend([dict(zip(col_names, row)) for row in rows])
    return res

def sync_rating_status(engine, report_id, status_str="참여 완료"):
    with engine.begin() as conn:
        conn.execute(update(title_table)
                     .where(title_table.c.신고번호 == report_id)
                     .values(만족도조사여부=status_str))
        
        conn.execute(update(merge_traffic_table)
                     .where(merge_traffic_table.c.신고번호 == report_id)
                     .values(만족도조사여부=status_str))
                     
        conn.execute(update(merge_other_table)
                     .where(merge_other_table.c.신고번호 == report_id)
                     .values(만족도조사여부=status_str))