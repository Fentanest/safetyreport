import re
import pandas as pd
from sqlalchemy import select, desc
from sqlalchemy.exc import OperationalError
from core.database import database
import settings.settings as app_settings
from datetime import datetime, timedelta
import os

def _extract_fine_amount(s) -> int:
    """'과태료: 40,000원' 형식에서 금액(정수) 추출. 과태료만 포함, 범칙금 제외. 없으면 0."""
    if not s:
        return 0
    s = str(s)
    if '과태료' not in s:
        return 0
    m = re.search(r'([\d,]+)\s*원', s)
    if m:
        return int(m.group(1).replace(',', ''))
    return 0

def _normalize_police_agency(x: str) -> str:
    idx = x.find('경찰서')
    return x[:idx + 3] if idx != -1 else x

_REPORT_FIELDS = ["ID", "신고번호", "신고명", "신고일", "답변일", "처리기관", "담당자",
                  "처리상태", "범칙금_과태료", "벌점", "차량번호", "위반법규", "위반장소",
                  "발생일자", "발생시각", "신고내용", "처리내용", "첨부사진", "첨부파일", "지도"]

def _row_to_dict(row) -> dict:
    d = {f: row.get(f, '') for f in _REPORT_FIELDS}
    d["ID"] = str(d["ID"])
    d["결과"] = d["처리상태"]
    return d

def _safe_read(conn, table):
    """테이블이 아직 생성되지 않은 경우 빈 DataFrame 반환"""
    try:
        return pd.read_sql_query(select(table), conn)
    except OperationalError:
        return pd.DataFrame()

def get_dashboard_stats(engine):
    total = 0
    accept_count = 0
    partial_count = 0
    reject_count = 0
    processing_count = 0
    completed_count = 0
    withdraw_count = 0
    
    t_fine_count = 0
    t_penalty_count = 0
    t_reject_count = 0
    t_unconfirmed_count = 0
    
    recent_answers = []
    watchlist_items = []

    last_crawl_time = "기록 없음"
    log_file = os.path.join(app_settings.datapath, 'logs', 'current_crawl.log')
    if os.path.exists(log_file):
        mtime = os.path.getmtime(log_file)
        last_crawl_time = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")

    with engine.connect() as conn:
        for t in [database.merge_traffic_table, database.merge_parking_table, database.merge_other_table]:
            try:
                df = pd.read_sql_query(select(t), conn)
            except OperationalError:
                continue
            if not df.empty:
                total += len(df)
                accept_count += len(df[df['처리상태'] == '수용'])
                reject_count += len(df[df['처리상태'].isin(['불수용', '기타'])])
                partial_count += len(df[df['처리상태'] == '일부수용'])
                processing_count += len(df[df['처리상태'].isin(['처리중', '진행', '진행중'])])
                completed_count += len(df[df['처리상태'].isin(['수용', '불수용', '일부수용', '기타', '답변완료'])])
                withdraw_count += len(df[df['처리상태'] == '취하'])
                if t == database.merge_traffic_table:
                    t_fine_count += len(df[df['범칙금_과태료'].str.contains('과태료', na=False)])
                    t_penalty_count += len(df[df['범칙금_과태료'].str.contains('경고|범칙금', na=False)])
                    t_reject_count += len(df[df['처리상태'].isin(['불수용', '기타'])])
                    t_unconfirmed_count += len(df[(df['범칙금_과태료'] == '미확인') & (~df['처리상태'].isin(['불수용', '기타']))])
                
                # Recent answers (3 days)
                three_days_ago = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
                recent_df = df[(df['답변일'] >= three_days_ago) & (df['답변일'] <= datetime.now().strftime("%Y-%m-%d"))]
                if app_settings.exclude_withdraw:
                    recent_df = recent_df[recent_df['처리상태'] != '취하']
                for _, row in recent_df.iterrows():
                    recent_answers.append(_row_to_dict(row))

        # Watchlist
        df_watch = pd.read_sql_query(select(database.watchlist_table.c.신고번호), conn)
        watch_ids = df_watch['신고번호'].tolist()

        if watch_ids:
            for t in [database.merge_traffic_table, database.merge_parking_table, database.merge_other_table]:
                query = select(t).where(t.c.신고번호.in_(watch_ids))
                try:
                    df_watch_part = pd.read_sql_query(query, conn)
                except OperationalError:
                    continue
                for _, row in df_watch_part.iterrows():
                    watchlist_items.append(_row_to_dict(row))

    recent_answers.sort(key=lambda x: x['답변일'], reverse=True)
    watchlist_items.sort(key=lambda x: x['신고번호'] or '', reverse=True)
    
    valid_total = (accept_count + partial_count + reject_count + processing_count) if app_settings.exclude_withdraw else total
    t_bar_total = t_fine_count + t_penalty_count + t_reject_count + t_unconfirmed_count
    
    return {
        "last_crawl_time": last_crawl_time,
        "total": total,
        "acceptCount": accept_count,
        "partialCount": partial_count,
        "rejectCount": reject_count,
        "processingCount": processing_count,
        "completedCount": completed_count,
        "withdrawCount": withdraw_count,
        
        "tFineCount": t_fine_count,
        "tPenaltyCount": t_penalty_count,
        "tRejectCount": t_reject_count,
        "tUnconfirmedCount": t_unconfirmed_count,
        
        "accept_pct": round((accept_count / valid_total * 100), 1) if valid_total > 0 else 0,
        "partial_pct": round((partial_count / valid_total * 100), 1) if valid_total > 0 else 0,
        "reject_pct": round((reject_count / valid_total * 100), 1) if valid_total > 0 else 0,
        "processing_pct": round((processing_count / valid_total * 100), 1) if valid_total > 0 else 0,
        "withdraw_pct": round((withdraw_count / valid_total * 100), 1) if valid_total > 0 else 0,
        
        "tfine_pct": round((t_fine_count / t_bar_total * 100), 1) if t_bar_total > 0 else 0,
        "tpenalty_pct": round((t_penalty_count / t_bar_total * 100), 1) if t_bar_total > 0 else 0,
        "treject_pct": round((t_reject_count / t_bar_total * 100), 1) if t_bar_total > 0 else 0,
        "tunconfirmed_pct": round((t_unconfirmed_count / t_bar_total * 100), 1) if t_bar_total > 0 else 0,
        
        "recent_answers": recent_answers[:20],
        "watchlist": watchlist_items,
        "exclude_withdraw": app_settings.exclude_withdraw
    }

def _get_records_from_table(engine, table_obj, filters=None):
    try:
        with engine.connect() as conn:
            df = pd.read_sql_query(select(table_obj).order_by(desc('신고번호')), conn)
    except Exception:
        return []
    with engine.connect() as conn:

        df_watch = pd.read_sql_query(select(database.watchlist_table.c.신고번호), conn)
        watch_ids = set(df_watch['신고번호'].tolist())

        if app_settings.exclude_withdraw and not df.empty:
            df = df[df['처리상태'] != '취하']

        if app_settings.normalize_police and not df.empty and '처리기관' in df.columns:
            df['처리기관'] = df['처리기관'].apply(_normalize_police_agency)

        if filters and not df.empty:
            status = filters.get('status')
            if status:
                if status == '처리중':
                    df = df[df['처리상태'].isin(['처리중', '진행', '진행중'])]
                elif status == '완료':
                    df = df[df['처리상태'].isin(['수용', '불수용', '일부수용', '기타', '답변완료'])]
                elif status == '불수용':
                    df = df[df['처리상태'].isin(['불수용', '기타'])]
                else:
                    df = df[df['처리상태'] == status]

            fine = filters.get('fine')
            if fine and '범칙금_과태료' in df.columns:
                if fine == '과태료':
                    df = df[df['범칙금_과태료'].str.contains('과태료', na=False)]
                elif fine == '경고':
                    df = df[df['범칙금_과태료'].str.contains('경고|범칙금', na=False)]
                elif fine == '미확인':
                    df = df[(df['범칙금_과태료'] == '미확인') & (~df['처리상태'].isin(['불수용', '기타']))]

            agency = filters.get('agency')
            if agency and '처리기관' in df.columns:
                if filters.get('agencyExact'):
                    df = df[df['처리기관'] == agency]
                else:
                    df = df[df['처리기관'].str.contains(agency, na=False, regex=False)]

            person = filters.get('person')
            if person and '담당자' in df.columns:
                df = df[df['담당자'] == person]

            law = filters.get('law')
            if law and '위반법규' in df.columns:
                if law == '__없음__':
                    df = df[df['위반법규'].fillna('').astype(str).str.strip() == '']
                else:
                    df = df[df['위반법규'].str.contains(law, na=False, regex=False)]

        if not df.empty:
            df['감시목록'] = df['신고번호'].apply(lambda x: 'Y' if x in watch_ids else 'N')
            df = df.fillna('')

        return df.to_dict(orient="records") if not df.empty else []

def get_traffic_records(engine, filters=None):
    return _get_records_from_table(engine, database.merge_traffic_table, filters)

def get_parking_records(engine, filters=None):
    return _get_records_from_table(engine, database.merge_parking_table, filters)

def get_other_records(engine, filters=None):
    return _get_records_from_table(engine, database.merge_other_table, filters)

def get_all_records(engine, filters=None):
    traffic = _get_records_from_table(engine, database.merge_traffic_table, filters)
    parking = _get_records_from_table(engine, database.merge_parking_table, filters)
    other = _get_records_from_table(engine, database.merge_other_table, filters)
    combined = traffic + parking + other
    combined.sort(key=lambda x: x.get('신고번호', '') or '', reverse=True)
    return combined

def search_by_vehicle(engine, vehicle_number: str):
    """차량번호로 전체 카테고리 검색 (부분 일치). 신고번호 역순 정렬."""
    vehicle_number = vehicle_number.strip()
    if not vehicle_number:
        return []

    results = []
    with engine.connect() as conn:
        df_watch = pd.read_sql_query(select(database.watchlist_table.c.신고번호), conn)
        watch_ids = set(df_watch['신고번호'].tolist())

        for t in [database.merge_traffic_table, database.merge_parking_table, database.merge_other_table]:
            if '차량번호' not in t.c:
                continue
            query = select(t).where(t.c.차량번호.contains(vehicle_number)).order_by(desc(t.c.신고번호))
            try:
                df = pd.read_sql_query(query, conn)
            except OperationalError:
                continue
            if df.empty:
                continue
            df = df.fillna('')
            if app_settings.exclude_withdraw and '처리상태' in df.columns:
                df = df[df['처리상태'] != '취하']
            if df.empty:
                continue
            df['감시목록'] = df['신고번호'].apply(lambda x: 'Y' if x in watch_ids else 'N')
            results.extend(df.to_dict(orient='records'))

    results.sort(key=lambda x: x.get('신고번호', '') or '', reverse=True)
    return results


def search_by_address(engine, address: str):
    """위반장소로 전체 카테고리 검색 (부분 일치). 신고번호 역순 정렬."""
    address = address.strip()
    if not address:
        return []

    results = []
    with engine.connect() as conn:
        df_watch = pd.read_sql_query(select(database.watchlist_table.c.신고번호), conn)
        watch_ids = set(df_watch['신고번호'].tolist())

        for t in [database.merge_traffic_table, database.merge_parking_table, database.merge_other_table]:
            if '위반장소' not in t.c:
                continue
            query = select(t).where(t.c.위반장소.contains(address)).order_by(desc(t.c.신고번호))
            try:
                df = pd.read_sql_query(query, conn)
            except OperationalError:
                continue
            if df.empty:
                continue
            df = df.fillna('')
            if app_settings.exclude_withdraw and '처리상태' in df.columns:
                df = df[df['처리상태'] != '취하']
            if df.empty:
                continue
            df['감시목록'] = df['신고번호'].apply(lambda x: 'Y' if x in watch_ids else 'N')
            results.extend(df.to_dict(orient='records'))

    results.sort(key=lambda x: x.get('신고번호', '') or '', reverse=True)
    return results


def get_duplicate_records(engine):
    with engine.connect() as conn:
        df_t = pd.read_sql_query(select(database.merge_traffic_table), conn)
        df_p = _safe_read(conn, database.merge_parking_table)
        df_o = pd.read_sql_query(select(database.merge_other_table), conn)
        df_all = pd.concat([df_t, df_p, df_o])
        
        if df_all.empty:
            return []
            
        df_watch = pd.read_sql_query(select(database.watchlist_table.c.신고번호), conn)
        watch_ids = set(df_watch['신고번호'].tolist())
        df_all['감시목록'] = df_all['신고번호'].apply(lambda x: 'Y' if x in watch_ids else 'N')

        df_all = df_all[df_all['차량번호'].str.strip() != '']
        # 전체 횟수 및 유효 횟수(취하 제외) 미리 계산
        total_counts = df_all['차량번호'].value_counts().to_dict()
        valid_counts = df_all[df_all['처리상태'] != '취하']['차량번호'].value_counts().to_dict()

        counts = df_all['차량번호'].value_counts()
        duplicates = counts[counts > 1].index.tolist()
        df_dups = df_all[df_all['차량번호'].isin(duplicates)].copy()
        
        # 횟수 정보 맵핑
        df_dups['total_count'] = df_dups['차량번호'].map(total_counts)
        df_dups['valid_count'] = df_dups['차량번호'].map(valid_counts).fillna(0).astype(int)

        # 1. 각 차량별 가장 최신 신고번호를 구함
        max_rnums = df_dups.groupby('차량번호')['신고번호'].max().reset_index()
        max_rnums.rename(columns={'신고번호': '최근신고번호'}, inplace=True)
        
        # 2. 원본 데이터프레임에 최근신고번호 컬럼을 조인
        df_dups = df_dups.merge(max_rnums, on='차량번호')
        
        # 3. 정렬 순서: 최신신고번호를 가진 그룹 순(내림차순) -> 같은 그룹 내 차량번호 묶음 -> 개별 신고번호(내림차순)
        df_dups = df_dups.sort_values(by=['최근신고번호', '차량번호', '신고번호'], ascending=[False, True, False])
        df_dups = df_dups.drop(columns=['최근신고번호'])

        if app_settings.exclude_withdraw:
            df_dups = df_dups[df_dups['처리상태'] != '취하']
            # 취하 제거 후 단 1건만 남은 차량은 '중복'가 아니므로 제외
            if not df_dups.empty:
                remaining_counts = df_dups['차량번호'].value_counts()
                single_after_filter = remaining_counts[remaining_counts <= 1].index.tolist()
                if single_after_filter:
                    df_dups = df_dups[~df_dups['차량번호'].isin(single_after_filter)]

        return df_dups.fillna('').to_dict('records')

def get_agency_stats(engine, filters=None):
    with engine.connect() as conn:
        df_t = pd.read_sql_query(select(database.merge_traffic_table), conn)
        df_p = _safe_read(conn, database.merge_parking_table)
        df_o = pd.read_sql_query(select(database.merge_other_table), conn)

    # available_years: 필터 무관하게 전체 데이터의 답변일 연도 목록
    _all_years = set()
    for _df in [df_t, df_p, df_o]:
        if not _df.empty and '답변일' in _df.columns:
            _ys = _df['답변일'].dropna().str[:4]
            _all_years.update(_ys[_ys.str.match(r'^\d{4}$', na=False)].unique())
    available_years = sorted(_all_years, reverse=True)

    def _calc_avg_days(group_df):
        try:
            d_end = pd.to_datetime(group_df['답변일'], errors='coerce')
            d_start = pd.to_datetime(group_df['신고일'], errors='coerce')
            days = (d_end - d_start).dt.days.dropna()
            days = days[days >= 0]
            return round(float(days.mean()), 1) if len(days) > 0 else None
        except Exception:
            return None

    def calc_stats(df):
        _empty = {
            "by_agency": [], "by_person": [],
            "police_by_agency": [], "police_by_person": [],
            "other_by_agency": [], "other_by_person": [],
            "by_law": [], "total_fine_amount": 0,
            "available_laws": [],
        }
        if df.empty:
            return _empty

        if filters:
            # 연도 필터 (답변일 기준)
            if filters.get('year') and filters['year'] not in ('all', '', None):
                if '답변일' in df.columns:
                    df = df[df['답변일'].str.startswith(filters['year'], na=False)]

            if filters.get('reportName') and '신고명' in df.columns:
                df = df[df['신고명'].str.contains(filters['reportName'], na=False, regex=False)]
            if filters.get('location') and '위반장소' in df.columns:
                df = df[df['위반장소'].str.contains(filters['location'], na=False, regex=False)]

            if filters.get('reportDateStart') and '신고일' in df.columns:
                df = df[df['신고일'] >= filters['reportDateStart']]
            if filters.get('reportDateEnd') and '신고일' in df.columns:
                df = df[df['신고일'] <= filters['reportDateEnd'] + ' 23:59:59']

            if filters.get('occurDateStart') and '발생일자' in df.columns:
                df = df[df['발생일자'] >= filters['occurDateStart']]
            if filters.get('occurDateEnd') and '발생일자' in df.columns:
                df = df[df['발생일자'] <= filters['occurDateEnd']]

            if filters.get('responseDateStart') and '답변일' in df.columns:
                df = df[df['답변일'] >= filters['responseDateStart']]
            if filters.get('responseDateEnd') and '답변일' in df.columns:
                df = df[df['답변일'] <= filters['responseDateEnd']]

            if filters.get('occurTimeStart') and '발생시각' in df.columns:
                df = df[df['발생시각'] >= filters['occurTimeStart']]
            if filters.get('occurTimeEnd') and '발생시각' in df.columns:
                df = df[df['발생시각'] <= filters['occurTimeEnd']]

            if filters.get('agency') and '처리기관' in df.columns:
                if filters.get('agencyExact'):
                    df = df[df['처리기관'] == filters['agency']]
                else:
                    df = df[df['처리기관'].str.contains(filters['agency'], na=False, regex=False)]

            if filters.get('excludePolice') and '처리기관' in df.columns:
                df = df[~df['처리기관'].str.contains('경찰', na=False)]
            if filters.get('onlyPolice') and '처리기관' in df.columns:
                df = df[df['처리기관'].str.contains('경찰', na=False)]

        # law 필터 적용 전에 사용 가능한 법규 목록 + 빈 법규 존재 여부 추출
        if '위반법규' in df.columns:
            _l = df['위반법규'].dropna().astype(str)
            _nonempty = _l[_l.str.strip() != '']
            available_laws = sorted(_nonempty.unique().tolist())
            has_empty_law = bool((df['위반법규'].fillna('').astype(str).str.strip() == '').any())
        else:
            available_laws = []
            has_empty_law = False

        # law 필터 적용
        if filters and filters.get('law') and '위반법규' in df.columns:
            if filters['law'] == '__없음__':
                df = df[df['위반법규'].fillna('').astype(str).str.strip() == '']
            else:
                df = df[df['위반법규'].str.contains(filters['law'], na=False, regex=False)]

        if df.empty:
            return _empty

        if app_settings.normalize_police and '처리기관' in df.columns:
            def norm_police(x):
                x = str(x)
                idx = x.find('경찰서')
                if idx != -1:
                    return x[:idx + 3]
                return x
            df['처리기관'] = df['처리기관'].apply(norm_police)

        df['처리기관'] = df.get('처리기관', pd.Series()).fillna('알수없음')
        df['담당자'] = df.get('담당자', pd.Series()).fillna('미지정')
        df['처리상태'] = df.get('처리상태', pd.Series()).fillna('진행중')
        df['범칙금_과태료'] = df.get('범칙금_과태료', pd.Series()).fillna('')

        # 아직 담당자가 배정되지 않아 데이터가 없는 '처리중'이나 '취하' 건 제외
        df = df[~((df['담당자'].isin(['', '미지정'])) & (df['처리상태'].isin(['처리중', '진행', '진행중', '취하'])))]

        stats_person = []
        for name, group in df.groupby(['처리기관', '담당자']):
            agency, person = name
            total = len(group)
            rejects = len(group[group['처리상태'].isin(['불수용', '기타'])])
            fines = len(group[group['범칙금_과태료'].str.contains('과태료', na=False)])
            warnings = len(group[group['범칙금_과태료'].str.contains('경고|범칙금', na=False)])
            avg = _calc_avg_days(group)
            total_fine = int(group['범칙금_과태료'].apply(_extract_fine_amount).sum())

            stats_person.append({
                "agency": agency,
                "person": person,
                "total": total,
                "avg_days": avg,
                "total_fine_amount": total_fine,
                "fines": fines,
                "fines_pct": round((fines / total) * 100, 1) if total > 0 else 0,
                "warnings": warnings,
                "warnings_pct": round((warnings / total) * 100, 1) if total > 0 else 0,
                "rejects": rejects,
                "rejects_pct": round((rejects / total) * 100, 1) if total > 0 else 0
            })

        stats_agency = []
        for agency, group in df.groupby('처리기관'):
            agency = agency[0] if isinstance(agency, tuple) else agency
            total = len(group)
            rejects = len(group[group['처리상태'].isin(['불수용', '기타'])])
            fines = len(group[group['범칙금_과태료'].str.contains('과태료', na=False)])
            warnings = len(group[group['범칙금_과태료'].str.contains('경고|범칙금', na=False)])
            avg = _calc_avg_days(group)
            total_fine = int(group['범칙금_과태료'].apply(_extract_fine_amount).sum())

            stats_agency.append({
                "agency": agency,
                "total": total,
                "avg_days": avg,
                "total_fine_amount": total_fine,
                "fines": fines,
                "fines_pct": round((fines / total) * 100, 1) if total > 0 else 0,
                "warnings": warnings,
                "warnings_pct": round((warnings / total) * 100, 1) if total > 0 else 0,
                "rejects": rejects,
                "rejects_pct": round((rejects / total) * 100, 1) if total > 0 else 0
            })

        stats_law = []
        if '위반법규' in df.columns:
            df_law = df.copy()
            df_law['위반법규'] = df_law['위반법규'].fillna('').astype(str)
            df_law = df_law[df_law['위반법규'].str.strip() != '']
            for law, group in df_law.groupby('위반법규'):
                total = len(group)
                rejects = len(group[group['처리상태'].isin(['불수용', '기타'])])
                fines = len(group[group['범칙금_과태료'].str.contains('과태료', na=False)])
                warnings = len(group[group['범칙금_과태료'].str.contains('경고|범칙금', na=False)])
                avg = _calc_avg_days(group)
                total_fine = int(group['범칙금_과태료'].apply(_extract_fine_amount).sum())
                stats_law.append({
                    "law": law,
                    "total": total,
                    "avg_days": avg,
                    "total_fine_amount": total_fine,
                    "fines": fines,
                    "fines_pct": round((fines / total) * 100, 1) if total > 0 else 0,
                    "warnings": warnings,
                    "warnings_pct": round((warnings / total) * 100, 1) if total > 0 else 0,
                    "rejects": rejects,
                    "rejects_pct": round((rejects / total) * 100, 1) if total > 0 else 0,
                })
        category_total_fine = int(df['범칙금_과태료'].apply(_extract_fine_amount).sum())

        def _sort(lst, key='total'): return pd.DataFrame(lst).sort_values(by=[key], ascending=False).to_dict('records') if lst else []
        all_agency = _sort(stats_agency)
        all_person = _sort(stats_person)
        all_law = _sort(stats_law)
        # 경찰/비경찰 분리
        police_agency  = [r for r in all_agency if '경찰' in r['agency']]
        police_person  = [r for r in all_person if '경찰' in r['agency']]
        other_agency   = [r for r in all_agency if '경찰' not in r['agency']]
        other_person   = [r for r in all_person if '경찰' not in r['agency']]
        return {
            "by_agency":         all_agency,
            "by_person":         all_person,
            "police_by_agency":  police_agency,
            "police_by_person":  police_person,
            "other_by_agency":   other_agency,
            "other_by_person":   other_person,
            "by_law":            all_law,
            "total_fine_amount": category_total_fine,
            "available_laws":    available_laws,
            "has_empty_law":     has_empty_law,
        }

    res_t = calc_stats(df_t)
    res_p = calc_stats(df_p)
    res_o = calc_stats(df_o)

    return {
        "traffic": res_t,
        "parking": res_p,
        "other": res_o,
        "available_years": available_years,
        "traffic_total_fine": int(df_t['범칙금_과태료'].apply(_extract_fine_amount).sum()) if not df_t.empty else 0,
    }

def resolve_to_report_numbers(engine, mixed_list):
    final_rnums = set()
    with engine.connect() as conn:
        df_t = pd.read_sql_query(select(database.merge_traffic_table.c.ID, database.merge_traffic_table.c.신고번호), conn)
        _df_p = _safe_read(conn, database.merge_parking_table)
        df_p = _df_p[['ID', '신고번호']] if not _df_p.empty else pd.DataFrame(columns=['ID', '신고번호'])
        df_o = pd.read_sql_query(select(database.merge_other_table.c.ID, database.merge_other_table.c.신고번호), conn)
        df = pd.concat([df_t, df_p, df_o])
        
        if df.empty:
            return []

        for val in mixed_list:
            if val in df['신고번호'].values:
                final_rnums.add(val)
            elif val in df['ID'].values:
                matching_rnums = df[df['ID'] == val]['신고번호'].tolist()
                for mr in matching_rnums: final_rnums.add(mr)

    return list(final_rnums)

def resolve_ids_for_rating(engine, id_list):
    # Backward compatibility wrap targeting Report Numbers directly.
    return resolve_to_report_numbers(engine, id_list)

def get_unrated_records(engine):
    """별점 주기 페이지용: 참여 완료/불가이거나 처리상태가 취하인 건은 제외"""
    with engine.connect() as conn:
        results = []
        for t in [database.merge_traffic_table, database.merge_parking_table, database.merge_other_table]:
            try:
                df = pd.read_sql_query(select(t), conn)
            except OperationalError:
                continue
            if df.empty:
                continue
            # 이미 참여 완료 또는 참여 불가(취하)인 항목 제외
            df = df[~df['만족도조사여부'].isin(['참여 완료', '참여 불가'])]
            # 답변이 없는 상태(처리중/취하 등)는 만족도조사 불가 → 제외
            df = df[~df['처리상태'].isin(['취하', '답변 대기', '처리중', '진행', '진행중'])]
            results.append(df)
        if not results:
            return []
        df_all = pd.concat(results, ignore_index=True)
        df_all = df_all.sort_values(by='신고번호', ascending=False)
        df_all = df_all.fillna('')
        return df_all.to_dict('records')

def get_all_watchlist(engine):
    with engine.connect() as conn:
        df_watch = pd.read_sql_query(select(database.watchlist_table.c.신고번호), conn)
        if df_watch.empty:
            return []
        watch_ids = set(df_watch['신고번호'].tolist())

        df_t = pd.read_sql_query(select(database.merge_traffic_table), conn)
        df_p = _safe_read(conn, database.merge_parking_table)
        df_o = pd.read_sql_query(select(database.merge_other_table), conn)

        df = pd.concat([df_t, df_p, df_o], ignore_index=True)
        if df.empty:
            return []
            
        df = df[df['신고번호'].isin(watch_ids)]
        if df.empty:
            return []
            
        df['감시목록'] = 'Y'
        df = df.sort_values(by='신고번호', ascending=False)
        df = df.fillna('')
        return df.to_dict('records')

def save_crawl_changes(engine, changed_item_ids):
    """크롤링으로 변경된 신고건의 상세 정보를 파일에 저장 (모바일 개별 알림용).
    changed_item_ids: [{"id": ..., "change_type": "신규"/"변경"}, ...]"""
    import json
    if not changed_item_ids:
        return

    # change_type 맵 구성
    change_type_map = {item["id"]: item["change_type"] for item in changed_item_ids}
    all_ids = list(change_type_map.keys())

    changed_records = database.get_merged_records_by_ids(engine, all_ids)
    if not changed_records:
        return

    changes = []
    for r in changed_records:
        rid = r.get('ID', '')
        changes.append({
            "ID": str(rid),
            "change_type": change_type_map.get(rid, "변경"),
            "신고번호": str(r.get('신고번호', '')),
            "신고명": str(r.get('신고명', '')),
            "신고일": str(r.get('신고일', '')),
            "처리기관": str(r.get('처리기관', '')),
            "담당자": str(r.get('담당자', '')),
            "처리상태": str(r.get('처리상태', '')),
            "범칙금_과태료": str(r.get('범칙금_과태료', '')),
            "벌점": str(r.get('벌점', '')),
            "답변일": str(r.get('답변일', '')),
            "차량번호": str(r.get('차량번호', '')),
            "위반법규": str(r.get('위반법규', '')),
            "위반장소": str(r.get('위반장소', '')),
            "발생일자": str(r.get('발생일자', '')),
            "발생시각": str(r.get('발생시각', '')),
            "신고내용": str(r.get('신고내용', '')),
            "처리내용": str(r.get('처리내용', '')),
            "첨부사진": str(r.get('첨부사진', '')),
            "첨부파일": str(r.get('첨부파일', '')),
            "지도": str(r.get('지도', '')),
        })

    changes_file = os.path.join(app_settings.datapath, 'crawl_changes.json')
    with open(changes_file, 'w', encoding='utf-8') as f:
        json.dump(changes, f, ensure_ascii=False)


def clear_crawl_changes():
    """변경사항 없는 크롤링 완료 시 이전 결과 파일 삭제 (재브로드캐스트 방지)"""
    changes_file = os.path.join(app_settings.datapath, 'crawl_changes.json')
    try:
        if os.path.exists(changes_file):
            os.remove(changes_file)
    except Exception:
        pass


def peek_crawl_changes():
    """크롤링 변경 결과 조회 (파일 삭제 없음 — WS 브로드캐스트용)"""
    import json
    changes_file = os.path.join(app_settings.datapath, 'crawl_changes.json')
    if not os.path.exists(changes_file):
        return []
    try:
        with open(changes_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []


def get_and_clear_crawl_changes():
    """크롤링 변경 결과 조회 후 파일 삭제 (모바일 알림용)"""
    import json
    changes_file = os.path.join(app_settings.datapath, 'crawl_changes.json')
    if not os.path.exists(changes_file):
        return []
    try:
        with open(changes_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        os.remove(changes_file)
        return data
    except Exception:
        return []


def save_crawl_done(changed_count: int):
    """크롤링 완료 마커 저장 (Flutter 앱 폴링용)"""
    import json
    from datetime import datetime
    done_file = os.path.join(app_settings.datapath, 'crawl_done.json')
    with open(done_file, 'w', encoding='utf-8') as f:
        json.dump({
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "changed_count": changed_count
        }, f, ensure_ascii=False)


def get_and_clear_crawl_done():
    """크롤링 완료 마커 조회 후 삭제"""
    import json
    done_file = os.path.join(app_settings.datapath, 'crawl_done.json')
    if not os.path.exists(done_file):
        return None
    try:
        with open(done_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        os.remove(done_file)
        return data
    except Exception:
        return None


def save_crawl_done_ext(changed_count: int, changes: list):
    """크롤링 완료 마커 저장 (크롬 확장용 — 신고번호/신고명 포함)"""
    import json
    from datetime import datetime
    done_file = os.path.join(app_settings.datapath, 'crawl_done_ext.json')
    with open(done_file, 'w', encoding='utf-8') as f:
        json.dump({
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "changed_count": changed_count,
            "changes": [
                {"신고번호": c.get("신고번호", ""), "신고명": c.get("신고명", ""), "처리상태": c.get("처리상태", "")}
                for c in (changes or [])
            ],
        }, f, ensure_ascii=False)


def get_and_clear_crawl_done_ext():
    """크롬 확장용 크롤링 완료 마커 조회 후 삭제"""
    import json
    done_file = os.path.join(app_settings.datapath, 'crawl_done_ext.json')
    if not os.path.exists(done_file):
        return None
    try:
        with open(done_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        os.remove(done_file)
        return data
    except Exception:
        return None


def update_watchlist_status(engine, rnums, status):
    if not rnums:
        return 0
        
    with engine.begin() as conn:
        if status == 'Y':
            records = [{'신고번호': r} for r in rnums]
            from sqlalchemy.dialects.sqlite import insert as sqlite_insert
            stmt = sqlite_insert(database.watchlist_table).values(records)
            stmt = stmt.on_conflict_do_nothing(index_elements=['신고번호'])
            conn.execute(stmt)
        else:
            from sqlalchemy import delete
            stmt = delete(database.watchlist_table).where(database.watchlist_table.c.신고번호.in_(rnums))
            conn.execute(stmt)
            
    return len(rnums)
