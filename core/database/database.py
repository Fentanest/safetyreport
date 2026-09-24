import settings.settings as settings
import pandas as pd
from sqlalchemy import select, func, exists, update, text, inspect, bindparam, or_
from sqlalchemy.dialects.sqlite import insert
from core.utils import logger
from datetime import datetime
from dateutil.relativedelta import relativedelta
import re

from .models import (metadata, title_table, detail_traffic_table, detail_parking_table, detail_other_table,
                     merge_traffic_table, merge_parking_table, merge_other_table, watchlist_table, admin_users_table,
                     api_keys_table, entry_value_table, raw_content_table, sync_meta_table,
                     duplicate_group_table, duplicate_member_table)


def _current_epoch_millis() -> int:
    return int(datetime.now().timestamp() * 1000)


_DATE_ONLY_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DETAIL_TABLES = [detail_traffic_table, detail_parking_table, detail_other_table]
_FINAL_RAW_STATUSES = ("수용", "일부수용", "불수용", "기타", "답변완료", "취하", "이송")
_LEGACY_ONGOING_STATUSES = ("", "진행", "진행중", "처리중", "검토중")


def _parse_epoch_millis_from_text(value, *, prefer_end_of_day: bool = False):
    raw = str(value or "").strip()
    if not raw:
        return None

    parsed = pd.to_datetime(raw, errors="coerce")
    if pd.isna(parsed):
        return None

    timestamp = pd.Timestamp(parsed)
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_convert(None)

    if prefer_end_of_day and _DATE_ONLY_PATTERN.match(raw):
        timestamp = timestamp + pd.Timedelta(days=1) - pd.Timedelta(milliseconds=1)

    return int(timestamp.to_pydatetime().timestamp() * 1000)


def _derive_backfill_synced_at(answer_date, report_date):
    return (
        _parse_epoch_millis_from_text(answer_date, prefer_end_of_day=True)
        or _parse_epoch_millis_from_text(report_date, prefer_end_of_day=True)
    )


def _refresh_duplicate_groups(engine, *, track_changes: bool = False):
    try:
        from services import duplicate_group_service
        return duplicate_group_service.refresh_duplicate_groups(engine, track_changes=track_changes)
    except Exception as exc:
        logger.LoggerFactory.logbot.error(f"[duplicate] 중복군 재생성 실패: {exc}")
        return {"group_count": 0, "member_count": 0, "changes": []}


def backfill_synced_at(engine):
    """기존 서버 DB의 synced_at 공백을 답변일/신고일 기준으로 채운다."""
    updated_total = 0
    unresolved_total = 0

    with engine.begin() as conn:
        for detail_table in _DETAIL_TABLES:
            join_stmt = detail_table.outerjoin(title_table, detail_table.c.ID == title_table.c.ID)
            rows = conn.execute(
                select(
                    detail_table.c.ID,
                    detail_table.c.답변일,
                    title_table.c.신고일,
                )
                .select_from(join_stmt)
                .where(detail_table.c.synced_at.is_(None))
            ).fetchall()

            updates = []
            unresolved = 0
            for row in rows:
                synced_at = _derive_backfill_synced_at(row.답변일, row.신고일)
                if synced_at is None:
                    unresolved += 1
                    continue
                updates.append({"target_id": row.ID, "synced_at": synced_at})

            if updates:
                conn.execute(
                    update(detail_table)
                    .where(detail_table.c.ID == bindparam("target_id"))
                    .values(synced_at=bindparam("synced_at")),
                    updates,
                )
                updated_total += len(updates)

            if rows:
                unresolved_total += unresolved
                logger.LoggerFactory.logbot.info(
                    f"[migration] {detail_table.name} synced_at 백필: {len(updates)}건, 미해결 {unresolved}건"
                )

    if updated_total:
        logger.LoggerFactory.logbot.info(
            f"[migration] synced_at 백필 완료: 총 {updated_total}건 갱신, merge 재생성 시작"
        )
        merge_final(engine)
    elif unresolved_total:
        logger.LoggerFactory.logbot.warning(
            f"[migration] synced_at 백필 대상이 있었지만 날짜 파싱 불가로 {unresolved_total}건은 유지되었습니다."
        )
    else:
        logger.LoggerFactory.logbot.debug("[migration] synced_at 백필 대상 없음.")

    return updated_total


def _normalize_processing_layers(engine):
    updated_total = 0
    final_status_ids = select(title_table.c.ID).where(title_table.c.상태.in_(_FINAL_RAW_STATUSES))

    with engine.begin() as conn:
        for detail_table in _DETAIL_TABLES:
            raw_status_value = (
                select(title_table.c.상태)
                .where(title_table.c.ID == detail_table.c.ID)
                .scalar_subquery()
            )
            supplement_status_ids = select(title_table.c.ID).where(title_table.c.상태 == "보완요청")
            result = conn.execute(
                update(detail_table)
                .where(detail_table.c.ID.in_(final_status_ids))
                .where(
                    or_(
                        detail_table.c.처리상태.is_(None),
                        detail_table.c.처리상태.in_(_LEGACY_ONGOING_STATUSES),
                        detail_table.c.보완_미응답 == 'Y',
                    )
                )
                .values(
                    처리상태=raw_status_value,
                    종결여부='Y',
                    보완_미응답='N',
                )
            )
            updated_total += result.rowcount or 0

            result = conn.execute(
                update(detail_table)
                .where(detail_table.c.ID.in_(supplement_status_ids))
                .where(~detail_table.c.ID.in_(final_status_ids))
                .where(
                    or_(
                        detail_table.c.처리상태.is_(None),
                        detail_table.c.처리상태.in_(_LEGACY_ONGOING_STATUSES),
                        detail_table.c.처리상태 == '보완요청',
                    )
                )
                .values(처리상태='보완요청', 종결여부='N', 보완_미응답='Y')
            )
            updated_total += result.rowcount or 0

            result = conn.execute(
                update(detail_table)
                .where(detail_table.c.보완_미응답 == 'Y')
                .where(~detail_table.c.ID.in_(final_status_ids))
                .where(func.coalesce(detail_table.c.처리상태, '') != '보완요청')
                .values(처리상태='보완요청', 종결여부='N')
            )
            updated_total += result.rowcount or 0

            result = conn.execute(
                update(detail_table)
                .where(~detail_table.c.ID.in_(final_status_ids))
                .where(func.coalesce(detail_table.c.보완_미응답, '') != 'Y')  # NULL 행도 포함(S-20)
                .where(
                    or_(
                        detail_table.c.처리상태.is_(None),
                        detail_table.c.처리상태.in_(_LEGACY_ONGOING_STATUSES),
                    )
                )
                .values(처리상태='처리중', 종결여부='N')
            )
            updated_total += result.rowcount or 0

    if updated_total:
        logger.LoggerFactory.logbot.info(
            f"[migration] raw/canonical 상태 정규화 완료: 총 {updated_total}건 갱신"
        )
    else:
        logger.LoggerFactory.logbot.debug("[migration] raw/canonical 상태 정규화 대상 없음.")

    return updated_total

def normalize_police_agency(x: str) -> str:
    idx = x.find('경찰서')
    return x[:idx + 3] if idx != -1 else x

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
    _refuse_newer_schema(engine)
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
    backfill_synced_at(engine)
    normalized_rows = _normalize_processing_layers(engine)
    if normalized_rows:
        merge_final(engine)
    _refresh_duplicate_groups(engine)
    _apply_versioned_migrations(engine)


# 서버 DB 스키마 버전(PRAGMA user_version). contracts/storage-contract.json 의 schema_version.server 와 같아야 한다.
# 위의 열 추가식 upgrade 는 그대로 두고, 이후 데이터 이동이 필요한 변경은 번호 붙은 단계로 쌓는다(저장 계층 재설계 R1).
SCHEMA_VERSION = 2


def _migration_1_storage_tables(conn):
    """R1: 수정값·중복 판단·변경 기록 표. 표 생성은 위 metadata 경로가 하므로 여기서는 확인만 한다."""
    names = {row[0] for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
    missing = {"mysafety_report_override", "mysafety_duplicate_decision", "mysafety_change_log", "mysafety_change_cursor"} - names
    if missing:
        raise RuntimeError(f"스키마 버전 1 표 누락: {sorted(missing)}")


def _migration_2_sync_meta_and_watch_flags(conn):
    """R1b: sync_meta.value 를 NULL 허용으로(모바일과 같게, 표 재생성), 서버 sync_meta 의 감시목록 사본 삭제(원천은 mysafety_watchlist),
    title/merge 의 `감시목록` 열을 감시목록 표 기준으로 다시 계산."""
    conn.execute(text("CREATE TABLE mysafety_sync_meta_new (key VARCHAR NOT NULL PRIMARY KEY, value VARCHAR)"))
    conn.execute(text("INSERT INTO mysafety_sync_meta_new (key, value) SELECT key, value FROM mysafety_sync_meta WHERE key != 'watchlist'"))
    conn.execute(text("DROP TABLE mysafety_sync_meta"))
    conn.execute(text("ALTER TABLE mysafety_sync_meta_new RENAME TO mysafety_sync_meta"))
    refresh_watch_flags(conn)


_VERSIONED_MIGRATIONS = {1: _migration_1_storage_tables, 2: _migration_2_sync_meta_and_watch_flags}


def refresh_watch_flags(conn, report_numbers=None):
    """title·merge 의 `감시목록` 열 = mysafety_watchlist 에 있으면 'Y' 아니면 'N' (계산값, 계약 owner=derived)."""
    for table in (title_table, merge_traffic_table, merge_parking_table, merge_other_table):
        flag = text(
            f"UPDATE {table.name} SET 감시목록 = CASE WHEN 신고번호 IN (SELECT 신고번호 FROM mysafety_watchlist) THEN 'Y' ELSE 'N' END"
            + (" WHERE 신고번호 IN :numbers" if report_numbers else "")
        )
        if report_numbers:
            flag = flag.bindparams(bindparam("numbers", expanding=True))
            conn.execute(flag, {"numbers": list(report_numbers)})
        else:
            conn.execute(flag)


def get_schema_version(engine) -> int:
    with engine.connect() as conn:
        return int(conn.execute(text("PRAGMA user_version")).scalar() or 0)


def _refuse_newer_schema(engine):
    current = get_schema_version(engine)
    if current > SCHEMA_VERSION:
        # 더 새 서버가 만든 DB. 모르는 구조를 건드리지 않도록 upgrade 전에 멈춘다.
        raise RuntimeError(f"DB 스키마 버전 {current} 은 이 서버({SCHEMA_VERSION})보다 새 버전입니다. 서버를 업데이트하세요.")


def _apply_versioned_migrations(engine):
    current = get_schema_version(engine)
    for version in range(current + 1, SCHEMA_VERSION + 1):
        with engine.begin() as conn:
            _VERSIONED_MIGRATIONS[version](conn)
            conn.execute(text(f"PRAGMA user_version = {version}"))
        logger.LoggerFactory.logbot.info(f"[schema] DB 스키마 버전 {version} 적용")

def _get_title_ids_for_scan(conn, *, message: str):
    logger.LoggerFactory.logbot.info(message)
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

    incomplete_queries = [
        # 종결여부 NULL(모름)도 미종결로 본다. `!= 'Y'` 만 쓰면 NULL 행이 영영 빠진다(S-19).
        select(t.c.ID).where(func.coalesce(t.c.종결여부, '') != 'Y')
        for t in (detail_traffic_table, detail_parking_table, detail_other_table)
    ]

    df_new = pd.read_sql_query(query_new, conn)
    df_incomplete = pd.concat(
        [pd.read_sql_query(q, conn) for q in incomplete_queries],
        ignore_index=True,
    )

    merged = pd.concat([
        df_new, df_incomplete,
    ]).drop_duplicates()
    return merged

def get_pending_detail_ids(engine, force=False):
    with engine.connect() as conn:
        if force:
            df = _get_title_ids_for_scan(conn, message="전체 신고 건을 다시 스캔합니다.")
        else:
            detail_rows = sum(
                conn.execute(select(func.count()).select_from(t)).scalar()
                for t in (detail_traffic_table, detail_parking_table, detail_other_table)
            )
            if detail_rows == 0:  # 주정차 표도 센다(S-19)
                df = _get_title_ids_for_scan(conn, message="detail 테이블 비어 있어 전체 스캔 시작")
            else:
                df = _get_new_and_incomplete_ids(conn)
        
        if df.empty:
            return []

        df_sorted = df.sort_values(by='ID', ascending=True)
        detaillist = df_sorted['ID'].tolist()
        logger.LoggerFactory.logbot.debug("스캔대상 ID 리스트화 완료")
        logger.LoggerFactory.logbot.info(f"스캔대상 ID 총 {len(detaillist)}건")
        return detaillist


def get_cNo(engine, force=False):
    return get_pending_detail_ids(engine=engine, force=force)

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

    # SQLite SQLITE_MAX_VARIABLE_NUMBER 한계 대응 (구버전 999, 신버전 32766)
    # mysafety 테이블 컬럼 수 = 7 → 배치당 최대 100행 (700 변수)
    TITLE_COLS = 7
    BATCH_SIZE = 100
    ID_CHUNK = 500  # in_() 쿼리용

    with engine.connect() as conn:
        # 기존 ID 조회 - in_() 변수 한계 대응을 위해 청크로 분할
        existing_ids = set()
        for i in range(0, len(incoming_ids), ID_CHUNK):
            chunk_ids = incoming_ids[i:i + ID_CHUNK]
            q = select(title_table.c.ID).where(title_table.c.ID.in_(chunk_ids))
            chunk_result = pd.read_sql(q, conn)
            existing_ids.update(chunk_result['ID'].tolist())

        new_df = combined_df[~combined_df['ID'].isin(existing_ids)]
        if not new_df.empty:
            new_report_numbers = new_df['신고번호'].tolist()

        from sqlalchemy import case as sa_case
        records = combined_df.to_dict('records')

        # 배치 단위로 upsert (too many SQL variables 방지)
        for i in range(0, len(records), BATCH_SIZE):
            batch = records[i:i + BATCH_SIZE]
            insert_stmt = insert(title_table).values(batch)
            # 만족도조사여부: 새 값이 비어있으면 기존 값을 유지 (재크롤링 시 덮어쓰기 방지)
            # 새 값이 비었으면 유지, '참여 완료' → 다른 값으로 되돌리기는 금지(결정 D-3, S-26).
            # 확정 미참여 재분류는 상세 저장(만족도 조회 결과)만 할 수 있다.
            poll_update = sa_case(
                (func.coalesce(insert_stmt.excluded.만족도조사여부, '') == '', title_table.c.만족도조사여부),
                (title_table.c.만족도조사여부 == '참여 완료', title_table.c.만족도조사여부),
                else_=insert_stmt.excluded.만족도조사여부
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

_TITLE_STATUS_FROM_PROGRESS = {
    '답변완료': '답변완료',
    '수용':     '답변완료',  # 레거시 HTML '진행상황' 텍스트
    '불수용':   '불수용',
    '일부수용': '일부수용',
    '기타':     '기타',
    '취하':     '취하',
    '이송':     '이송',
}

_PHOTO_COLUMNS = ("사진_첫촬영", "사진_끝촬영", "사진_촬영수")


def _resolve_photo_capture(engine, target_table, record_id, category, entry_value, new_record):
    """주정차 신고 사진 촬영 시각. 이미 값(또는 시도 결과 0)이 있으면 그대로 이어받고, 없을 때만 받아 온다.

    네트워크 요청은 DB 트랜잭션 밖에서 한다(SQLite 쓰기 잠금을 오래 잡지 않기 위해).
    upsert 가 모든 컬럼을 덮어쓰므로, 이어받지 않으면 재크롤링 때 기존 값이 NULL 로 지워진다.
    """
    existing = {}
    try:
        with engine.connect() as read_conn:
            row = read_conn.execute(
                select(*[target_table.c[name] for name in _PHOTO_COLUMNS]).where(target_table.c.ID == record_id)
            ).first()
            if row is not None:
                existing = dict(row._mapping)
    except Exception:
        existing = {}
    carried = {name: existing.get(name) for name in _PHOTO_COLUMNS}
    if existing.get("사진_촬영수") is not None:
        return carried
    from services import photo_capture_time
    if not photo_capture_time.is_parking_report(category, entry_value):
        return carried
    try:
        collected = photo_capture_time.collect(new_record.get("첨부사진"))
    except Exception as exc:  # fixture 차단 포함 — 크롤링은 멈추지 않는다
        logger.LoggerFactory.logbot.warning(f"[photo] ID {record_id} 촬영 시각 수집 실패: {exc}")
        collected = None
    return collected if collected is not None else carried


def detail_to_sql(dataframes_with_category, engine, conn=None):
    if not dataframes_with_category:
        return []

    changed_item_ids = []
    total_records = 0
    geo_columns = ["주소정규화", "행정구역", "위도", "경도", "지오코딩상태"]

    for item in dataframes_with_category:
        # 2~7-tuple 모두 지원
        # (df, category)
        # (df, category, entry_value)
        # (df, category, entry_value, progress_status)
        # (df, category, entry_value, progress_status, title_fields)
        # (df, category, entry_value, progress_status, title_fields, raw_content, raw_type)
        progress_status = None
        title_fields = None
        raw_content = None
        raw_type = ""
        if len(item) == 7:
            df, category, entry_value, progress_status, title_fields, raw_content, raw_type = item
        elif len(item) == 6:
            df, category, entry_value, progress_status, title_fields, raw_content = item
        elif len(item) == 5:
            df, category, entry_value, progress_status, title_fields = item
        elif len(item) == 4:
            df, category, entry_value, progress_status = item
        elif len(item) == 3:
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
        photo_values = _resolve_photo_capture(engine, target_table, record_id, category, entry_value, new_record)

        try:
            with engine.begin() as conn:
                now_ms = _current_epoch_millis()
                select_stmt = select(target_table).where(target_table.c.ID == record_id)
                existing_record_proxy = conn.execute(select_stmt).first()

                is_new = existing_record_proxy is None
                is_changed = False
                existing_record = dict(existing_record_proxy._mapping) if existing_record_proxy else {}

                try:
                    from services import geocode_service
                    # 동일 트랜잭션 연결을 재사용해 SQLite self-lock을 피한다.
                    geo_payload = geocode_service.prepare_geo_payload(
                        engine,
                        new_record.get("위반장소", ""),
                        existing_record=existing_record,
                        conn=conn,
                    )
                except Exception as exc:
                    logger.LoggerFactory.logbot.warning(f"[geocode] ID {record_id} 지오코딩 준비 실패: {exc}")
                    new_address = new_record.get("위반장소", "")
                    existing_address = existing_record.get("주소정규화") or existing_record.get("위반장소") or ""
                    if geocode_service.normalize_address(existing_address) == geocode_service.normalize_address(new_address):
                        geo_payload = geocode_service.extract_geo_payload(existing_record, fallback_address=new_address)
                    else:
                        geo_payload = geocode_service.build_pending_geo_payload(new_address, status="error")

                for column_name in geo_columns:
                    new_record[column_name] = geo_payload.get(column_name)
                new_record.update(photo_values)

                # entry_value 저장
                if entry_value is not None:
                    ev_stmt = insert(entry_value_table).values(ID=record_id, entry_value=entry_value)
                    ev_stmt = ev_stmt.on_conflict_do_update(index_elements=['ID'], set_={'entry_value': entry_value})
                    conn.execute(ev_stmt)
                total_records += 1

                if raw_content is not None and str(raw_content).strip():
                    existing_raw_proxy = conn.execute(
                        select(raw_content_table).where(raw_content_table.c.ID == record_id)
                    ).first()
                    existing_raw = dict(existing_raw_proxy._mapping) if existing_raw_proxy else {}
                    raw_payload = {
                        "ID": record_id,
                        "raw_content": str(raw_content),
                        "raw_type": str(raw_type or ""),
                        "saved_at": existing_raw.get("saved_at"),
                    }
                    if (
                        existing_raw.get("raw_content") != raw_payload["raw_content"]
                        or existing_raw.get("raw_type", "") != raw_payload["raw_type"]
                        or raw_payload["saved_at"] is None
                    ):
                        raw_payload["saved_at"] = now_ms

                    raw_stmt = insert(raw_content_table).values(**raw_payload)
                    raw_stmt = raw_stmt.on_conflict_do_update(
                        index_elements=['ID'],
                        set_={
                            "raw_content": raw_payload["raw_content"],
                            "raw_type": raw_payload["raw_type"],
                            "saved_at": raw_payload["saved_at"],
                        },
                    )
                    conn.execute(raw_stmt)

                if is_new:
                    changed_item_ids.append({"id": record_id, "change_type": "신규"})
                    new_record["synced_at"] = now_ms
                else:
                    for key, new_value in new_record.items():
                        # 사진 촬영 시각은 보조 메타데이터라 신고 변경(synced_at·변경 알림)으로 보지 않는다.
                        if key == "synced_at" or key in _PHOTO_COLUMNS:
                            continue
                        if key in existing_record and str(existing_record[key]) != str(new_value):
                            is_changed = True
                            break

                    if is_changed:
                        changed_item_ids.append({"id": record_id, "change_type": "변경"})
                        new_record["synced_at"] = now_ms
                    else:
                        new_record["synced_at"] = existing_record.get("synced_at")

                insert_stmt = insert(target_table).values(new_record)
                update_dict = {col.name: getattr(insert_stmt.excluded, col.name) for col in target_table.c if col.name != 'ID'}

                upsert_query = insert_stmt.on_conflict_do_update(
                    index_elements=['ID'],
                    set_=update_dict
                )
                conn.execute(upsert_query)

                if title_fields:
                    from sqlalchemy import case as sa_case
                    # 만족도조사여부: '참여 완료' → 다운그레이드 금지 (단 별점 조회로 미참여 확인된 경우만 예외)
                    poll = title_fields.get('만족도조사여부', '')
                    has_rating_field = '별점' in title_fields  # fetcher가 점수 조회 시도했음을 의미
                    if has_rating_field and poll == '참여 가능':
                        # 미참여 재분류: 다운그레이드 허용
                        poll_expr = poll
                    elif poll:
                        poll_expr = sa_case(
                            (title_table.c.만족도조사여부 == '참여 완료', title_table.c.만족도조사여부),
                            else_=poll
                        )
                    else:
                        poll_expr = title_table.c.만족도조사여부

                    update_values = dict(
                        상태=title_fields['상태'],
                        신고번호=title_fields['신고번호'],
                        신고명=title_fields['신고명'],
                        신고일=title_fields['신고일'],
                        만족도조사여부=poll_expr,
                    )
                    if has_rating_field:
                        update_values['별점'] = title_fields.get('별점')
                        update_values['별점사유'] = title_fields.get('별점사유') or ''
                    conn.execute(
                        update(title_table)
                        .where(title_table.c.ID == record_id)
                        .values(**update_values)
                    )
                else:
                    # title_fields 없을 때 기존 동작: 상태=='진행'인 경우만 동기화
                    title_status = _TITLE_STATUS_FROM_PROGRESS.get(progress_status)
                    if title_status:
                        conn.execute(
                            update(title_table)
                            .where(title_table.c.ID == record_id)
                            .where(title_table.c.상태 == '진행')
                            .values(상태=title_status)
                        )
                # engine.begin() 블록 종료 시 자동 commit
        except Exception as e:
            logger.LoggerFactory.logbot.error(f"ID {record_id} upsert 실패, 건너뜀: {e}")

    logger.LoggerFactory.logbot.info(f"총 {total_records}건 detail 테이블 upsert 완료. (변경/신규: {len(changed_item_ids)}건)")
    return changed_item_ids


def deatil_to_sql(dataframes_with_category, engine, conn=None):
    return detail_to_sql(dataframes_with_category=dataframes_with_category, engine=engine, conn=conn)

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
        title_table.c.별점,
        title_table.c.별점사유,
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
        detail_source.c.주소정규화,
        detail_source.c.행정구역,
        detail_source.c.위도,
        detail_source.c.경도,
        detail_source.c.지오코딩상태,
        detail_source.c.종결여부,
        detail_source.c.신고내용,
        detail_source.c.처리내용,
        detail_source.c.지도,
        detail_source.c.첨부사진,
        detail_source.c.첨부파일,
        detail_source.c.synced_at,
        detail_source.c.보완횟수,
        detail_source.c.보완_미응답,
        detail_source.c.보완_요청자,
        detail_source.c.보완_요청일시,
        detail_source.c.보완_완료일시,
        detail_source.c.보완_요청_내용,
        detail_source.c.보완_신고자_의견,
        detail_source.c.사진_첫촬영,
        detail_source.c.사진_끝촬영,
        detail_source.c.사진_촬영수,
    ).select_from(j_inner)

    insert_stmt = merge_target.insert().from_select([c.name for c in merge_target.c], select_stmt)
    conn.execute(insert_stmt)

def merge_final(engine, conn=None, *, track_duplicate_changes: bool = False):
    with engine.connect() as conn:
        _merge_for_table(conn, merge_traffic_table, detail_traffic_table)
        _merge_for_table(conn, merge_parking_table, detail_parking_table)
        _merge_for_table(conn, merge_other_table, detail_other_table)
        refresh_watch_flags(conn)
        conn.commit()
        logger.LoggerFactory.logbot.info("최종 데이터 병합 완료 (Traffic/Parking/Other 분리)")
    return _refresh_duplicate_groups(engine, track_changes=track_duplicate_changes)

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
    """전체 카테고리 합본 (레거시 호환). 새 코드는 load_results_by_category 사용 권장."""
    cats = load_results_by_category(engine)
    parts = [df for df in cats.values() if not df.empty]
    return pd.concat(parts) if parts else pd.DataFrame()


def load_results_by_category(engine):
    """카테고리별 분리 결과. 엑셀/구글시트 시트별 저장용.
    반환: {"교통위반": df_t, "주정차위반": df_p, "기타위반": df_o}"""
    with engine.connect() as conn:
        df_watch = pd.read_sql_query(select(watchlist_table.c.신고번호), conn)
        watch_ids = set(df_watch['신고번호'].tolist())

        result = {}
        for label, t in [("교통위반", merge_traffic_table),
                         ("주정차위반", merge_parking_table),
                         ("기타위반", merge_other_table)]:
            df = pd.DataFrame(pd.read_sql_query(select(t), conn))
            if not df.empty:
                df['감시목록'] = df['신고번호'].apply(lambda x: 'Y' if x in watch_ids else 'N')
                if settings.exclude_withdraw:
                    df = df[df['처리상태'] != '취하']
                if settings.normalize_police and '처리기관' in df.columns:
                    df['처리기관'] = df['처리기관'].apply(normalize_police_agency)
            result[label] = df
        return result

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
    tables = [title_table, merge_traffic_table, merge_parking_table, merge_other_table]
    with engine.begin() as conn:
        for t in tables:
            conn.execute(update(t).where(t.c.신고번호 == report_id).values(만족도조사여부=status_str))


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
