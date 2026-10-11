"""화면용 표(mysafety_report_effective) 읽기·검색(EO R-06에서 database.py 에서 분리). database 모듈이 같은 이름을 다시 내보낸다."""
import pandas as pd
from sqlalchemy import select

import settings.settings as settings
from services import report_category, report_policy

from .models import report_effective_table as effective, watchlist_table


def load_results(engine, conn=None):
    """전체 카테고리 합본 (레거시 호환). 새 코드는 load_results_by_category 사용 권장."""
    cats = load_results_by_category(engine)
    parts = [df for df in cats.values() if not df.empty]
    return pd.concat(parts) if parts else pd.DataFrame()


def load_results_by_category(engine):
    """카테고리별 분리 결과. 엑셀/구글시트 시트별 저장용.
    반환: {"교통위반": df, "주정차위반": df, "안전신고": df, "생활불편": df, "기타": df} (report_category.CATEGORY_LABELS 순서)"""
    with engine.connect() as conn:
        conn.exec_driver_sql('BEGIN')  # 모든 분류를 한 스냅샷에서 읽는다(기술일지 A1-07)
        df_watch = pd.read_sql_query(select(watchlist_table.c.신고번호), conn)
        watch_ids = set(df_watch['신고번호'].tolist())
        df_all = pd.read_sql_query(select(effective), conn)

    result = {}
    for category in report_category.CATEGORIES:
        df = df_all[df_all['category'] == category].reset_index(drop=True) if not df_all.empty else pd.DataFrame(columns=df_all.columns)
        if not df.empty:
            df['감시목록'] = df['신고번호'].apply(lambda x: 'Y' if x in watch_ids else 'N')
            if settings.exclude_withdraw:
                df = df[report_policy.status_series(df) != report_policy.WITHDRAWN_STATUS]
        result[report_category.CATEGORY_LABELS[category]] = df
    return result


def _rows(engine, where):
    with engine.connect() as conn:
        result = conn.execute(select(effective).where(where))
        return [dict(row) for row in result.mappings().all()]


def get_merged_records_by_report_numbers(engine, report_numbers):
    """신고번호(SPP-…)로 화면용 표 행을 읽는다. 별점 작업은 신고번호로 움직인다."""
    if not report_numbers:
        return []
    return _rows(engine, effective.c["신고번호"].in_(list(report_numbers)))


def get_merged_records_by_ids(engine, id_list):
    if not id_list:
        return []
    return _rows(engine, effective.c.ID.in_(list(id_list)))


def search_by_car_number(engine, car_number: str):
    return _rows(engine, effective.c.차량번호.like(f"%{car_number}%"))


def search_by_report_number(engine, report_number: str):
    return _rows(engine, effective.c.신고번호.like(f"%{report_number}%"))
