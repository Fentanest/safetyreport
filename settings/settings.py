import datetime
import os

# Selenium Grid
remotepath = os.getenv('remotepath')

# 접속 주소
loginurl = "https://www.safetyreport.go.kr/#/main/login/login" # 로그인 URL
myreporturl = "https://www.safetyreport.go.kr/#/mypage/mysafereport" # 마이페이지 URL (신고 전체 건 파악에 필요)
mysafereporturl = "https://www.safetyreport.go.kr/#mypage/mysafereport" # 개별 신고건 접속에 필요
titletable = 'table1'

username = os.getenv('USERNAME')
password = os.getenv('PASSWORD')

datapath = '/app/data'

table_title = "mysafety"
table_detail = "mysafetydetail"
table_merge = "mysafetymerge"

telegram_token = os.getenv('telegram_token')
chat_id = os.getenv('chat_id')

retry_interval = os.getenv('interval', 60)
max_retry_attemps = os.getenv('max_retry', 10)
max_empty_pages = os.getenv('max_empty_pages', 3)
log_level = os.getenv('log_level', "INFO")
TZ = os.getenv('TZ', "Asia/Seoul")

resultfile = f'{str(datetime.datetime.now()).replace(":","_")[:19]}_results.xlsx'
resultpath = os.path.join(datapath, 'results')
logfile = f'{str(datetime.datetime.now()).replace(":","_")[:19]}.log'
logpath = os.path.join(datapath, 'logs')
google_api_auth_file = os.path.join(datapath, 'auth/gspread.json')
db_path = os.path.join(datapath, os.getenv('dbfile', 'data.db'))
google_sheet_key = os.getenv('sheet_key')

google_sheet_enabled = os.path.exists(google_api_auth_file) and google_sheet_key is not None

if not google_sheet_enabled:
    google_api_auth_file = None
    google_sheet_key = None