from sqlalchemy import Table, MetaData, Column, String, Integer, Float
import settings.settings as settings

metadata = MetaData()

title_table = Table(settings.table_title, metadata,
                    Column('ID', String, primary_key=True),
                    Column('상태', String),
                    Column('신고번호', String),
                    Column('신고명', String),
                    Column('신고일', String),
                    Column('만족도조사여부', String),
                    Column('별점', Integer),
                    Column('별점사유', String),
                    Column('감시목록', String))

def get_detail_columns():
    return [
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
        Column('주소정규화', String),
        Column('행정구역', String),
        Column('위도', Float),
        Column('경도', Float),
        Column('지오코딩상태', String),
        Column('종결여부', String),
        Column('신고내용', String),
        Column('처리내용', String),
        Column('지도', String),
        Column('첨부사진', String),
        Column('첨부파일', String),
        Column('synced_at', Integer),
        Column('보완횟수', Integer),
        Column('보완_미응답', String),
        Column('보완_요청자', String),
        Column('보완_요청일시', String),
        Column('보완_완료일시', String),
        Column('보완_요청_내용', String),
        Column('보완_신고자_의견', String),
        # 주정차 사진 EXIF 촬영 시각(services/photo_capture_time.py). NULL = 아직 시도 안 함, 사진_촬영수 0 = 촬영 정보 없음
        Column('사진_첫촬영', String),
        Column('사진_끝촬영', String),
        Column('사진_촬영수', Integer),
    ]

detail_traffic_table = Table(settings.table_detail_traffic, metadata, *get_detail_columns())
detail_parking_table = Table(settings.table_detail_parking, metadata, *get_detail_columns())
detail_other_table = Table(settings.table_detail_other, metadata, *get_detail_columns())

def get_merge_columns():
    return [
        Column('ID', String, primary_key=True),
        Column('상태', String),
        Column('신고번호', String),
        Column('신고명', String),
        Column('신고일', String),
        Column('만족도조사여부', String),
        Column('별점', Integer),
        Column('별점사유', String),
        Column('감시목록', String),
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
        Column('주소정규화', String),
        Column('행정구역', String),
        Column('위도', Float),
        Column('경도', Float),
        Column('지오코딩상태', String),
        Column('종결여부', String),
        Column('신고내용', String),
        Column('처리내용', String),
        Column('지도', String),
        Column('첨부사진', String),
        Column('첨부파일', String),
        Column('synced_at', Integer),
        Column('보완횟수', Integer),
        Column('보완_미응답', String),
        Column('보완_요청자', String),
        Column('보완_요청일시', String),
        Column('보완_완료일시', String),
        Column('보완_요청_내용', String),
        Column('보완_신고자_의견', String),
        # 주정차 사진 EXIF 촬영 시각(services/photo_capture_time.py). NULL = 아직 시도 안 함, 사진_촬영수 0 = 촬영 정보 없음
        Column('사진_첫촬영', String),
        Column('사진_끝촬영', String),
        Column('사진_촬영수', Integer),
    ]

merge_traffic_table = Table(settings.table_merge_traffic, metadata, *get_merge_columns())
merge_parking_table = Table(settings.table_merge_parking, metadata, *get_merge_columns())
merge_other_table = Table(settings.table_merge_other, metadata, *get_merge_columns())

watchlist_table = Table('mysafety_watchlist', metadata,
                        Column('신고번호', String, primary_key=True))

admin_users_table = Table('admin_users', metadata,
                          Column('username', String, primary_key=True),
                          Column('password_hash', String, nullable=False),
                          Column('salt', String, nullable=False))

api_keys_table = Table('api_keys', metadata,
                       Column('key', String, primary_key=True),
                       Column('name', String, nullable=False),
                       Column('created_at', String, nullable=False))

entry_value_table = Table('mysafety_entry_value', metadata,
                          Column('ID', String, primary_key=True),
                          Column('entry_value', String, nullable=False))

raw_content_table = Table('mysafety_raw_content', metadata,
                          Column('ID', String, primary_key=True),
                          Column('raw_content', String, nullable=False, default=''),
                          Column('raw_type', String, nullable=False, default=''),
                          Column('saved_at', Integer))

sync_meta_table = Table('mysafety_sync_meta', metadata,
                        Column('key', String, primary_key=True),
                        Column('value', String, default=''))  # 모바일과 같게 NULL 허용(스키마 버전 2)

duplicate_group_table = Table('mysafety_duplicate_group', metadata,
                              Column('group_id', String, primary_key=True),
                              Column('fingerprint', String, nullable=False),
                              Column('match_type', String, nullable=False),
                              Column('status', String, nullable=False),
                              Column('representative_mode', String, nullable=False, default='auto'),
                              Column('representative_id', String),
                              Column('member_count', Integer, nullable=False, default=0),
                              Column('apply_globally', Integer, nullable=False, default=1),
                              Column('note', String),
                              Column('created_at', Integer),
                              Column('updated_at', Integer))

duplicate_member_table = Table('mysafety_duplicate_member', metadata,
                               Column('group_id', String, primary_key=True),
                               Column('report_id', String, primary_key=True),
                               Column('report_number', String, nullable=False),
                               Column('category', String, nullable=False),
                               Column('is_representative', Integer, nullable=False, default=0),
                               Column('priority_score', Integer, nullable=False, default=0),
                               Column('raw_match', Integer, nullable=False, default=0),
                               Column('field_match', Integer, nullable=False, default=0),
                               Column('created_at', Integer),
                               Column('updated_at', Integer))

geocode_cache_table = Table('mysafety_geocode_cache', metadata,
                            Column('주소정규화', String, primary_key=True),
                            Column('원본주소', String),
                            Column('행정구역', String),
                            Column('위도', Float),
                            Column('경도', Float),
                            Column('상태', String, nullable=False, default='ok'),
                            Column('source', String, nullable=False, default='kakao'),
                            Column('error_message', String),
                            Column('updated_at', Integer))

# ── 저장 계층 재설계 R1 (docs/plans/storage-refactor-plan.md §3-5·3-6). 계약: contracts/storage-contract.json ──
# 사용자 수정값: 사이트 원본(detail) 위에 덮어 보여 준다. 재크롤링이 지우지 않는다(결정 D-1). 쓰기는 R2 부터.
report_override_table = Table('mysafety_report_override', metadata,
                              Column('ID', String, primary_key=True),
                              Column('column_name', String, primary_key=True),
                              Column('value', String),
                              Column('updated_at', Integer, nullable=False))

# 중복군 사용자 판단: 그룹 재생성(멤버 재계산)과 분리해 보존한다(결정 D-6). group_id = 본문 sha256. 쓰기는 R2 부터.
duplicate_decision_table = Table('mysafety_duplicate_decision', metadata,
                                 Column('group_id', String, primary_key=True),
                                 Column('status', String, nullable=False),
                                 Column('representative_mode', String, nullable=False),
                                 Column('representative_id', String),
                                 Column('apply_globally', Integer, nullable=False),
                                 Column('note', String),
                                 Column('updated_at', Integer, nullable=False))

# 변경 기록 + 기기별 읽은 위치(결정 D-5). 기기 식별자가 없는 구앱은 device_id='legacy' 한 줄을 함께 쓴다. 쓰기는 R5 부터.
change_log_table = Table('mysafety_change_log', metadata,
                         Column('seq', Integer, primary_key=True, autoincrement=True),
                         Column('created_at', Integer, nullable=False),
                         Column('kind', String, nullable=False),
                         Column('report_id', String),
                         Column('payload', String, nullable=False))

change_cursor_table = Table('mysafety_change_cursor', metadata,
                            Column('device_id', String, primary_key=True),
                            Column('last_seq', Integer, nullable=False),
                            Column('updated_at', Integer, nullable=False))
