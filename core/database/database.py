import settings.settings as settings
import pandas as pd
from sqlalchemy import select, func, exists, update, text, inspect
from sqlalchemy.dialects.sqlite import insert
from core.utils import logger
from datetime import datetime
from dateutil.relativedelta import relativedelta

from .models import (metadata, title_table, detail_traffic_table, detail_parking_table, detail_other_table,
                     merge_traffic_table, merge_parking_table, merge_other_table, watchlist_table, admin_users_table,
                     api_keys_table, entry_value_table)

def category_from_entry_value(entry_value: str) -> str:
    """entry_value 문자열로부터 카테고리를 결정합니다."""
    if "자동차·교통위반" in entry_value:
        return "traffic"
    elif "불법주정차신고" in entry_value:
        return "parking"
    else:
        return "other"

def migrate_by_entry_value(engine):
    """entry_value 테이블 기반으로 잘못 분류된 신고를 올바른 detail 테이블로 이동합니다."""
    detail_tables = {
        "traffic": detail_traffic_table,
        "other":   detail_other_table,
        "parking": detail_parking_table,
    }

    with engine.connect() as conn:
        rows = conn.execute(select(entry_value_table)).fetchall()
        if not rows:
            return

        moved = 0
        for row in rows:
            record_id = row.ID
            correct_cat = category_from_entry_value(row.entry_value)
            correct_table = detail_tables[correct_cat]

            # 현재 어느 테이블에 있는지 찾기
            current_table = None
            current_cat = None
            for cat, tbl in detail_tables.items():
                try:
                    res = conn.execute(select(tbl).where(tbl.c.ID == record_id)).first()
                    if res:
                        current_table = tbl
                        current_cat = cat
                        break
                except Exception:
                    continue

            if current_table is None or current_cat == correct_cat:
                continue  # 이미 올바른 테이블이거나 DB에 없음

            # 올바른 테이블로 이동
            record = dict(conn.execute(select(current_table).where(current_table.c.ID == record_id)).first()._mapping)
            ins = insert(correct_table).values(record)
            ins = ins.on_conflict_do_update(
                index_elements=['ID'],
                set_={col.name: getattr(ins.excluded, col.name) for col in correct_table.c if col.name != 'ID'}
            )
            conn.execute(ins)
            conn.execute(current_table.delete().where(current_table.c.ID == record_id))
            moved += 1
            logger.LoggerFactory.logbot.info(
                f"[migrate] ID={record_id} {current_cat} → {correct_cat} (entry_value: {row.entry_value[:40]})"
            )

        conn.commit()

    if moved:
        logger.LoggerFactory.logbot.info(f"[migrate] entry_value 기반 {moved}건 재분류 완료. merge_final 재실행.")
        merge_final(engine)
    else:
        logger.LoggerFactory.logbot.debug("[migrate] entry_value 기반 재분류: 이동할 항목 없음.")


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

    migrate_by_entry_value(engine)

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
        ~exists().where(title_table.c.ID == detail_parking_table.c.ID)
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

    query_changed_parking = select(title_table.c.ID).select_from(
        title_table.join(detail_parking_table, title_table.c.ID == detail_parking_table.c.ID)
    ).where(
        (title_table.c.상태 != detail_parking_table.c.처리상태) &
        (detail_parking_table.c.종결여부 != 'Y')
    )

    query_changed_other = select(title_table.c.ID).select_from(
        title_table.join(detail_other_table, title_table.c.ID == detail_other_table.c.ID)
    ).where(
        (title_table.c.상태 != detail_other_table.c.처리상태) &
        (detail_other_table.c.종결여부 != 'Y')
    )

    df_new = pd.read_sql_query(query_new, conn)
    df_changed_t = pd.read_sql_query(query_changed_traffic, conn)
    df_changed_p = pd.read_sql_query(query_changed_parking, conn)
    df_changed_o = pd.read_sql_query(query_changed_other, conn)

    merged = pd.concat([df_new, df_changed_t, df_changed_p, df_changed_o]).drop_duplicates()
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
        for item in dataframes_with_category:
            # (df, category) 또는 (df, category, entry_value) 형태 모두 지원
            if len(item) == 3:
                df, category, entry_value = item
            else:
                df, category = item
                entry_value = None

            if category == "traffic":
                target_table = detail_traffic_table
            elif category == "parking":
                target_table = detail_parking_table
            else:
                target_table = detail_other_table

            records = df.to_dict('records')
            if not records:
                continue

            new_record = records[0]
            record_id = new_record['ID']

            # entry_value 저장
            if entry_value is not None:
                ev_stmt = insert(entry_value_table).values(ID=record_id, entry_value=entry_value)
                ev_stmt = ev_stmt.on_conflict_do_update(index_elements=['ID'], set_={'entry_value': entry_value})
                conn.execute(ev_stmt)
            total_records += 1

            select_stmt = select(target_table).where(target_table.c.ID == record_id)
            existing_record_proxy = conn.execute(select_stmt).first()

            is_new = existing_record_proxy is None
            is_changed = False

            if is_new:
                changed_item_ids.append({"id": record_id, "change_type": "신규"})
            else:
                existing_record = dict(existing_record_proxy._mapping)
                for key, new_value in new_record.items():
                    if key in existing_record and str(existing_record[key]) != str(new_value):
                        is_changed = True
                        break

                if is_changed:
                    changed_item_ids.append({"id": record_id, "change_type": "변경"})
            
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
        _merge_for_table(conn, merge_parking_table, detail_parking_table)
        _merge_for_table(conn, merge_other_table, detail_other_table)
        conn.commit()
        logger.LoggerFactory.logbot.info("최종 데이터 병합 완료 (Traffic/Parking/Other 분리)")

def clear_old_attachments(engine):
    six_months_ago = datetime.now() - relativedelta(months=6)
    six_months_ago_str = six_months_ago.strftime('%Y-%m-%d')

    with engine.connect() as conn:
        for t in [merge_traffic_table, merge_parking_table, merge_other_table]:
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
        query_p = select(merge_parking_table)
        query_o = select(merge_other_table)
        df_t = pd.DataFrame(pd.read_sql_query(query_t, conn))
        df_p = pd.DataFrame(pd.read_sql_query(query_p, conn))
        df_o = pd.DataFrame(pd.read_sql_query(query_o, conn))
        df = pd.concat([df_t, df_p, df_o]) if not (df_t.empty and df_p.empty and df_o.empty) else pd.DataFrame()
        
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
                    idx = x.find('경찰서')
                    if idx != -1:
                        return x[:idx + 3]
                    return x
                df['처리기관'] = df['처리기관'].apply(norm_police)
            
        return df

def get_merged_records_by_ids(engine, id_list):
    if not id_list:
        return []
    res = []
    with engine.connect() as conn:
        for t in [merge_traffic_table, merge_parking_table, merge_other_table]:
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
        for t in [merge_traffic_table, merge_parking_table, merge_other_table]:
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
        for t in [merge_traffic_table, merge_parking_table, merge_other_table]:
            query = select(t).where(t.c.신고번호.like(f"%{report_number}%"))
            result = conn.execute(query)
            rows = result.fetchall()
            if rows:
                col_names = result.keys()
                res.extend([dict(zip(col_names, row)) for row in rows])
    return res

# ── 관리자 계정 CRUD ─────────────────────────────────────────────────────────

def has_admin_user(engine) -> bool:
    with engine.connect() as conn:
        count = conn.execute(select(func.count()).select_from(admin_users_table)).scalar()
        return count > 0


def get_admin_user(engine, username: str):
    with engine.connect() as conn:
        result = conn.execute(
            select(admin_users_table).where(admin_users_table.c.username == username)
        ).first()
        return dict(result._mapping) if result else None


def create_admin_user(engine, username: str, password: str):
    from core.utils.security import hash_password
    salt, pwd_hash = hash_password(password)
    with engine.begin() as conn:
        conn.execute(admin_users_table.insert().values(
            username=username, password_hash=pwd_hash, salt=salt
        ))


def update_admin_user(engine, old_username: str, new_username: str, new_password: str):
    from core.utils.security import hash_password
    salt, pwd_hash = hash_password(new_password)
    with engine.begin() as conn:
        conn.execute(
            update(admin_users_table)
            .where(admin_users_table.c.username == old_username)
            .values(username=new_username, password_hash=pwd_hash, salt=salt)
        )


def sync_rating_status(engine, report_id, status_str="참여 완료"):
    with engine.begin() as conn:
        conn.execute(update(title_table)
                     .where(title_table.c.신고번호 == report_id)
                     .values(만족도조사여부=status_str))
        
        conn.execute(update(merge_traffic_table)
                     .where(merge_traffic_table.c.신고번호 == report_id)
                     .values(만족도조사여부=status_str))

        conn.execute(update(merge_parking_table)
                     .where(merge_parking_table.c.신고번호 == report_id)
                     .values(만족도조사여부=status_str))

        conn.execute(update(merge_other_table)
                     .where(merge_other_table.c.신고번호 == report_id)
                     .values(만족도조사여부=status_str))


# ── API Key CRUD ──────────────────────────────────────────────────────────────

def create_api_key(engine, name: str) -> str:
    import uuid
    key = "sk-" + uuid.uuid4().hex
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with engine.begin() as conn:
        conn.execute(api_keys_table.insert().values(key=key, name=name, created_at=created_at))
    return key


def get_all_api_keys(engine) -> list:
    with engine.connect() as conn:
        result = conn.execute(select(api_keys_table).order_by(api_keys_table.c.created_at.desc()))
        return [dict(row._mapping) for row in result]


def delete_api_key(engine, key: str):
    with engine.begin() as conn:
        conn.execute(api_keys_table.delete().where(api_keys_table.c.key == key))


def validate_api_key(engine, key: str) -> bool:
    with engine.connect() as conn:
        result = conn.execute(
            select(api_keys_table).where(api_keys_table.c.key == key)
        ).first()
        return result is not None

def get_api_key_name(engine, key: str) -> str:
    with engine.connect() as conn:
        result = conn.execute(
            select(api_keys_table.c.name).where(api_keys_table.c.key == key)
        ).first()
        return result[0] if result else "알 수 없는 기기"