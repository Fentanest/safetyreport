import settings.settings as settings
from sqlalchemy import text
import logger

def merge_title(engine, conn):
    with engine.connect() as conn:
        update_old_query = text(f'''
                                INSERT INTO {settings.table_title} 
                                SELECT * 
                                FROM {settings.table_title_temp} 
                                WHERE true
                                ON CONFLICT(ID) DO UPDATE SET 
                                상태=excluded.상태
                                WHERE excluded.상태 != 상태;
                                ''')
        
        insert_new_query = text(f'''
                                INSERT INTO {settings.table_title}
                                SELECT t.*
                                FROM {settings.table_title_temp} t
                                WHERE NOT EXISTS 
                                    (SELECT 1 FROM {settings.table_title} r
                                    WHERE t.ID = r.ID);
                                ''')
        
        conn.execute(update_old_query)
        logger.LoggerFactory.logbot.debug("기존 title 신고 건 상태 업데이트")
        conn.execute(insert_new_query)
        logger.LoggerFactory.logbot.debug("기존 title에 신규 신고건 추가")
        
        conn.commit()
        logger.LoggerFactory.logbot.info("title_table 병합 완료")

def drop_title_temp(engine, conn):
    with engine.connect() as conn:
        drop_title_temp_query = text(f'DROP TABLE IF EXISTS {settings.table_title_temp};')
        conn.execute(drop_title_temp_query)
        logger.LoggerFactory.logbot.debug("임시 title 테이블 제거")
        conn.commit()

def merge_detail(engine, conn):
    with engine.connect() as conn:
        update_old_query = text(f'''
                                INSERT INTO {settings.table_detail} 
                                SELECT * 
                                FROM {settings.table_detail_temp} 
                                WHERE true
                                ON CONFLICT(ID) DO UPDATE SET 
                                처리상태=excluded.처리상태,
                                위반법규=excluded.위반법규,
                                범칙금_과태료=excluded.범칙금_과태료,
                                벌점=excluded.벌점,
                                처리기관=excluded.처리기관,
                                담당자=excluded.담당자,
                                답변일=excluded.답변일,
                                종결여부=excluded.종결여부
                                WHERE excluded.처리상태 != 처리상태;
                                ''')
                
        insert_new_query = text(f'''
                                INSERT INTO {settings.table_detail}
                                SELECT t.*
                                FROM {settings.table_detail_temp} t
                                WHERE NOT EXISTS 
                                    (SELECT 1 FROM {settings.table_detail} r
                                    WHERE t.ID = r.ID);
                                ''')    
                
        update_withdrawl_query = text(f'''
                                UPDATE {settings.table_detail}
                                SET 처리상태 = '취하', 종결여부 = 'Y'
                                WHERE ID IN (
                                    SELECT ID
                                    FROM {settings.table_title}
                                    WHERE 상태 = '취하');
                                ''')
        
        conn.execute(update_old_query)
        logger.LoggerFactory.logbot.debug("기존 detail 테이블 신고건 상태 업데이트")
        conn.execute(insert_new_query)
        logger.LoggerFactory.logbot.debug("기존 detail 테이블에 신규 신고건 추가")
        conn.execute(update_withdrawl_query)
        logger.LoggerFactory.logbot.debug("상태=취하 적용")
        
        conn.commit()
        logger.LoggerFactory.logbot.info("detail_table 병합 완료")

def drop_detail_temp(engine, conn):
    with engine.connect() as conn:
        drop_detail_temp_query = text(f'DROP TABLE IF EXISTS {settings.table_detail_temp};')
        conn.execute(drop_detail_temp_query)
        logger.LoggerFactory.logbot.debug("임시 detail 테이블 제거")
        conn.commit()

def merge_final(engine, conn):
    with engine.connect() as conn:
        update_old_query = text(f'''
                                INSERT INTO {settings.table_merge} 
                                SELECT * 
                                FROM {settings.table_merge_temp} 
                                WHERE true
                                ON CONFLICT(ID) DO UPDATE SET 
                                상태=excluded.상태,
                                처리상태=excluded.처리상태,
                                위반법규=excluded.위반법규,
                                범칙금_과태료=excluded.범칙금_과태료,
                                벌점=excluded.벌점,
                                처리기관=excluded.처리기관,
                                담당자=excluded.담당자,
                                답변일=excluded.답변일,
                                종결여부=excluded.종결여부
                                WHERE excluded.처리상태 != 처리상태;
                                ''')
        
        insert_new_query = text(f'''
                                INSERT INTO {settings.table_merge}
                                SELECT t.*
                                FROM {settings.table_merge_temp} t
                                WHERE NOT EXISTS 
                                    (SELECT 1 FROM {settings.table_merge} r
                                    WHERE t.ID = r.ID);
                                ''')
        
        update_regret_query = text(f'''
                                UPDATE {settings.table_merge}
                                SET 공개결과 = '불수용'
                                WHERE 처리상태 = '불수용';
                                ''')
        conn.execute(update_old_query)
        logger.LoggerFactory.logbot.debug("기존 데이터 상태 업데이트")

        conn.execute(insert_new_query)
        logger.LoggerFactory.logbot.debug("신규병합 데이터 추가")        
        
        conn.commit()
        logger.LoggerFactory.logbot.info("최종 데이터 병합 완료")

def drop_merge_temp(engine, conn):
    with engine.connect() as conn:
        drop_temp_merge_temp_query = text(f'DROP TABLE IF EXISTS {settings.table_merge_temp};')
        conn.execute(drop_temp_merge_temp_query)
        logger.LoggerFactory.logbot.debug("임시 병합 테이블 제거")
        conn.commit()