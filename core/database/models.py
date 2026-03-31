from sqlalchemy import Table, MetaData, Column, String, Integer
import settings.settings as settings

metadata = MetaData()

title_table = Table(settings.table_title, metadata,
                    Column('ID', String, primary_key=True),
                    Column('상태', String),
                    Column('신고번호', String),
                    Column('신고명', String),
                    Column('신고일', String),
                    Column('만족도조사여부', String),
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
        Column('종결여부', String),
        Column('신고내용', String),
        Column('처리내용', String),
        Column('지도', String),
        Column('첨부사진', String),
        Column('첨부파일', String)
    ]

detail_traffic_table = Table(settings.table_detail_traffic, metadata, *get_detail_columns())
detail_other_table = Table(settings.table_detail_other, metadata, *get_detail_columns())

def get_merge_columns():
    return [
        Column('ID', String, primary_key=True),
        Column('상태', String),
        Column('신고번호', String),
        Column('신고명', String),
        Column('신고일', String),
        Column('만족도조사여부', String),
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
        Column('종결여부', String),
        Column('신고내용', String),
        Column('처리내용', String),
        Column('지도', String),
        Column('첨부사진', String),
        Column('첨부파일', String)
    ]

merge_traffic_table = Table(settings.table_merge_traffic, metadata, *get_merge_columns())
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
