"""병합 표 읽기·검색(EO R-06에서 database.py 에서 분리). database 모듈이 같은 이름을 다시 내보낸다."""
import pandas as pd
from sqlalchemy import select

import settings.settings as settings
from services import report_policy

from .models import merge_other_table, merge_parking_table, merge_traffic_table, watchlist_table


def load_results(engine, conn=None):
    """전체 카테고리 합본 (레거시 호환). 새 코드는 load_results_by_category 사용 권장."""
    cats = load_results_by_category(engine)
    parts = [df for df in cats.values() if not df.empty]
    return pd.concat(parts) if parts else pd.DataFrame()


def load_results_by_category(engine):
    """카테고리별 분리 결과. 엑셀/구글시트 시트별 저장용.
    반환: {"교통위반": df_t, "주정차위반": df_p, "기타위반": df_o}"""
    with engine.connect() as conn:
        conn.exec_driver_sql('BEGIN')  # 세 분류를 한 스냅샷에서 읽는다(기술일지 A1-07)
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
                    df = df[report_policy.status_series(df) != report_policy.WITHDRAWN_STATUS]
            result[label] = df
        return result

def get_merged_records_by_report_numbers(engine, report_numbers):
    """신고번호(SPP-…)로 병합 표 행을 읽는다. 별점 작업은 신고번호로 움직인다."""
    if not report_numbers:
        return []
    res = []
    with engine.connect() as conn:
        for t in [merge_traffic_table, merge_parking_table, merge_other_table]:
            result = conn.execute(select(t).where(t.c["신고번호"].in_(list(report_numbers))))
            col_names = result.keys()
            res.extend(dict(zip(col_names, row)) for row in result.fetchall())
    return res


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
