import datetime
import os

# Selenium Grid
remotepath = os.environ['remotepath']

# 접속 주소
loginurl = "https://www.safetyreport.go.kr/#/main/login/login" # 로그인 URL
myreporturl = "https://www.safetyreport.go.kr/#/mypage/mysafereport" # 마이페이지 URL (신고 전체 건 파악에 필요)
mysafereporturl = "https://www.safetyreport.go.kr/#mypage/mysafereport" # 개별 신고건 접속에 필요

username = os.environ['USERNAME']
password = os.environ['PASSWORD']

titletable = "table1"

path = './results'
logpath = './logs'

logfile = f'{str(datetime.datetime.now()).replace(":","_")[:19]}.log'
log_level = os.environ['log_level']

db = os.environ['dbfile']
google_api_auth_file = './auth/gspread.json'
google_sheet_key = os.environ.get('sheet_key')
google_sheet_enabled = os.path.exists(google_api_auth_file) and google_sheet_key is not None

if not google_sheet_enabled:
    google_api_auth_file = None
    google_sheet_key = None
    
resultfile = f'{str(datetime.datetime.now()).replace(":","_")[:19]}_results.xlsx'

table_title = "mysafety"
table_detail = "mysafetydetail"
table_opendata = "opendata"
table_merge = "mysafetymerge"

telegram_token = os.environ['telegram_token']
chat_id = os.environ['chat_id']

retry_interval = os.environ['interval']
max_retry_attemps = os.environ['max_retry']