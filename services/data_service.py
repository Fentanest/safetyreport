import pandas as pd
from sqlalchemy import select, desc
import database
import settings.settings as app_settings
from datetime import datetime, timedelta
import os

def get_dashboard_stats(engine):
    total = 0
    accept_count = 0
    partial_count = 0
    reject_count = 0
    fine_count = 0
    penalty_count = 0
    processing_count = 0
    completed_count = 0
    withdraw_count = 0
    
    recent_answers = []
    watchlist_items = []

    last_crawl_time = "기록 없음"
    log_file = os.path.join(app_settings.datapath, 'logs', 'current_crawl.log')
    if os.path.exists(log_file):
        mtime = os.path.getmtime(log_file)
        last_crawl_time = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")

    with engine.connect() as conn:
        for t in [database.merge_traffic_table, database.merge_other_table]:
            df = pd.read_sql_query(select(t), conn)
            if not df.empty:
                total += len(df)
                accept_count += len(df[df['처리상태'] == '수용'])
                reject_count += len(df[df['처리상태'].isin(['불수용', '기타'])])
                partial_count += len(df[df['처리상태'] == '일부수용'])
                processing_count += len(df[df['처리상태'].isin(['처리중', '진행', '진행중'])])
                completed_count += len(df[df['처리상태'].isin(['수용', '불수용', '일부수용', '기타', '답변완료'])])
                withdraw_count += len(df[df['처리상태'] == '취하'])
                
                fine_count += len(df[df['범칙금_과태료'].str.contains('과태료', na=False)])
                penalty_count += len(df[df['범칙금_과태료'].str.contains('경고|범칙금', na=False)])
                
                # Recent answers (3 days)
                three_days_ago = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
                recent_df = df[(df['답변일'] >= three_days_ago) & (df['답변일'] <= datetime.now().strftime("%Y-%m-%d"))]
                for _, row in recent_df.iterrows():
                    recent_answers.append({
                        "신고번호": row.get('신고번호', ''),
                        "신고명": row.get('신고명', ''),
                        "신고일": row.get('신고일', ''),
                        "처리기관": row.get('처리기관', ''),
                        "담당자": row.get('담당자', ''),
                        "상태": row.get('처리상태', ''),
                        "결과": row.get('처리상태', ''),
                        "범칙금_과태료": row.get('범칙금_과태료', ''),
                        "답변일": row.get('답변일', ''),
                        "차량번호": row.get('차량번호', '')
                    })

        # Watchlist
        for t in [database.merge_traffic_table, database.merge_other_table]:
            query = select(t).where(t.c.감시목록 == 'Y')
            df_watch = pd.read_sql_query(query, conn)
            for _, row in df_watch.iterrows():
                watchlist_items.append({
                    "신고번호": row.get('신고번호', ''),
                    "신고명": row.get('신고명', ''),
                    "신고일": row.get('신고일', ''),
                    "답변일": row.get('답변일', ''),
                    "처리기관": row.get('처리기관', ''),
                    "담당자": row.get('담당자', ''),
                    "상태": row.get('처리상태', ''),
                    "범칙금_과태료": row.get('범칙금_과태료', ''),
                    "차량번호": row.get('차량번호', '')
                })

    recent_answers.sort(key=lambda x: x['답변일'], reverse=True)
    return {
        "last_crawl_time": last_crawl_time,
        "total": total,
        "acceptCount": accept_count,
        "partialCount": partial_count,
        "rejectCount": reject_count,
        "processingCount": processing_count,
        "completedCount": completed_count,
        "withdrawCount": withdraw_count,
        "fineCount": fine_count,
        "penaltyCount": penalty_count,
        "recent_answers": recent_answers[:20],
        "watchlist": watchlist_items,
        "last_crawl_time": last_crawl_time
    }

def get_traffic_records(engine):
    with engine.connect() as conn:
        df = pd.read_sql_query(select(database.merge_traffic_table).order_by(desc('신고일')), conn)
        if app_settings.exclude_withdraw and not df.empty:
            df = df[df['처리상태'] != '취하']
            
        if app_settings.normalize_police and not df.empty and '처리기관' in df.columns:
            def norm_police(x):
                x = str(x)
                idx = x.find('경찰서')
                if idx != -1:
                    return x[:idx + 3]
                return x
            df['처리기관'] = df['처리기관'].apply(norm_police)
            
        return df.to_dict(orient="records") if not df.empty else []

def get_other_records(engine):
    with engine.connect() as conn:
        df = pd.read_sql_query(select(database.merge_other_table).order_by(desc('신고일')), conn)
        if app_settings.exclude_withdraw and not df.empty:
            df = df[df['처리상태'] != '취하']

        if app_settings.normalize_police and not df.empty and '처리기관' in df.columns:
            def norm_police(x):
                x = str(x)
                idx = x.find('경찰서')
                if idx != -1:
                    return x[:idx + 3]
                return x
            df['처리기관'] = df['처리기관'].apply(norm_police)

        return df.to_dict('records') if not df.empty else []

def get_duplicate_records(engine):
    with engine.connect() as conn:
        df_t = pd.read_sql_query(select(database.merge_traffic_table), conn)
        df_o = pd.read_sql_query(select(database.merge_other_table), conn)
        df_all = pd.concat([df_t, df_o])
        
        if df_all.empty:
            return []

        df_all = df_all[df_all['차량번호'].str.strip() != '']
        counts = df_all['차량번호'].value_counts()
        duplicates = counts[counts > 1].index.tolist()
        df_dups = df_all[df_all['차량번호'].isin(duplicates)].copy()
        
        # 1. 각 차량별 가장 최신 신고일을 구함
        max_dates = df_dups.groupby('차량번호')['신고일'].max().reset_index()
        max_dates.rename(columns={'신고일': '최근신고일'}, inplace=True)
        
        # 2. 원본 데이터프레임에 최근신고일 컬럼을 조인
        df_dups = df_dups.merge(max_dates, on='차량번호')
        
        # 3. 정렬 순서: 최신신고일을 가진 그룹 순(내림차순) -> 같은 그룹 내 차량번호 묶음 -> 개별 신고일(내림차순)
        df_dups = df_dups.sort_values(by=['최근신고일', '차량번호', '신고일'], ascending=[False, True, False])
        df_dups = df_dups.drop(columns=['최근신고일'])

        if app_settings.exclude_withdraw:
            df_dups = df_dups[df_dups['처리상태'] != '취하']
            # 취하 제거 후 단 1건만 남은 차량은 '중복'가 아니므로 제외
            if not df_dups.empty:
                remaining_counts = df_dups['차량번호'].value_counts()
                single_after_filter = remaining_counts[remaining_counts <= 1].index.tolist()
                if single_after_filter:
                    df_dups = df_dups[~df_dups['차량번호'].isin(single_after_filter)]

        return df_dups.to_dict('records')

def get_agency_stats(engine, filters=None):
    with engine.connect() as conn:
        df_t = pd.read_sql_query(select(database.merge_traffic_table), conn)
        df_o = pd.read_sql_query(select(database.merge_other_table), conn)

    def calc_stats(df):
        if df.empty:
            return []

        if filters:
            if filters.get('reportName') and '신고명' in df.columns:
                df = df[df['신고명'].str.contains(filters['reportName'], na=False, regex=True)]
            if filters.get('law') and '위반법규' in df.columns:
                df = df[df['위반법규'].str.contains(filters['law'], na=False, regex=True)]
            if filters.get('location') and '위반장소' in df.columns:
                df = df[df['위반장소'].str.contains(filters['location'], na=False, regex=True)]
            
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

        if df.empty:
            return []

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
        
        stats = []
        for name, group in df.groupby(['처리기관', '담당자']):
            agency, person = name
            total = len(group)
            
            unanswered = len(group[group['처리상태'].isin(['처리중', '진행', '진행중'])])
            rejects = len(group[group['처리상태'].isin(['불수용', '기타'])])
            
            fines = len(group[group['범칙금_과태료'].str.contains('과태료|범칙금', na=False)])
            warnings = len(group[group['범칙금_과태료'].str.contains('경고', na=False)])
            
            stats.append({
                "agency": agency,
                "person": person,
                "total": total,
                "unanswered": unanswered,
                "unanswered_pct": round((unanswered / total) * 100, 1) if total > 0 else 0,
                "fines": fines,
                "fines_pct": round((fines / total) * 100, 1) if total > 0 else 0,
                "warnings": warnings,
                "warnings_pct": round((warnings / total) * 100, 1) if total > 0 else 0,
                "rejects": rejects,
                "rejects_pct": round((rejects / total) * 100, 1) if total > 0 else 0
            })
            
        return pd.DataFrame(stats).sort_values(by=['total'], ascending=False).to_dict('records') if stats else []

    return {
        "traffic": calc_stats(df_t),
        "other": calc_stats(df_o)
    }

def resolve_to_report_numbers(engine, mixed_list):
    final_rnums = set()
    with engine.connect() as conn:
        df_t = pd.read_sql_query(select(database.merge_traffic_table.c.ID, database.merge_traffic_table.c.신고번호), conn)
        df_o = pd.read_sql_query(select(database.merge_other_table.c.ID, database.merge_other_table.c.신고번호), conn)
        df = pd.concat([df_t, df_o])
        
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
        for t in [database.merge_traffic_table, database.merge_other_table]:
            df = pd.read_sql_query(select(t), conn)
            if df.empty:
                continue
            # 이미 참여 완료 또는 참여 불가(취하)인 항목 제외
            df = df[~df['만족도조사여부'].isin(['참여 완료', '참여 불가'])]
            # 처리상태가 취하인 항목도 제외 (만족도 조사 불가)
            df = df[df['처리상태'] != '취하']
            results.append(df)
        if not results:
            return []
        df_all = pd.concat(results, ignore_index=True)
        df_all = df_all.sort_values(by='신고일', ascending=False)
        df_all = df_all.fillna('')
        return df_all.to_dict('records')

def get_all_watchlist(engine):
    with engine.connect() as conn:
        df_t = pd.read_sql_query(select(database.merge_traffic_table).where(database.merge_traffic_table.c.감시목록 == 'Y'), conn)
        df_o = pd.read_sql_query(select(database.merge_other_table).where(database.merge_other_table.c.감시목록 == 'Y'), conn)
        
        df = pd.concat([df_t, df_o], ignore_index=True)
        if df.empty:
            return []
            
        df['신고일'] = pd.to_datetime(df['신고일'], errors='coerce')
        df = df.sort_values(by='신고일', ascending=False)
        df['신고일'] = df['신고일'].dt.strftime('%Y-%m-%d %H:%M')
        
        df = df.fillna('')
        return df.to_dict('records')

def update_watchlist_status(engine, rnums, status):
    from sqlalchemy import update
    if not rnums:
        return 0
        
    with engine.begin() as conn:
        conn.execute(update(database.merge_traffic_table)
                     .where(database.merge_traffic_table.c.신고번호.in_(rnums))
                     .values(감시목록=status))
        conn.execute(update(database.merge_other_table)
                     .where(database.merge_other_table.c.신고번호.in_(rnums))
                     .values(감시목록=status))
        
    return len(rnums)
