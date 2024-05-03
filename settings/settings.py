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
singotable = "table_bbs view singo tb_sty02"

path = './results'
logpath = './logs'
settingspath = './settings'

logfile = f'{str(datetime.datetime.now()).replace(":","_")[:19]}.log'
log_level = os.environ['log_level']
# stdout = f'{str(datetime.datetime.now()).replace(":","_")[:19]}_stdout.txt' # 평시 문구
# stderr = f'{str(datetime.datetime.now()).replace(":","_")[:19]}_stderr.txt' # 에러 문구

db = os.environ['dbfile']
google_api_auth_file = './auth/gspread.json'
google_sheet_key = os.environ['sheet_key']
resultfile = f'{str(datetime.datetime.now()).replace(":","_")[:19]}_results.xlsx'

table_title = "mysafety"
table_title_temp = "mysafety_temp"
table_detail = "mysafetydetail"
table_detail_temp = "mysafetydetail_temp"
table_opendata = "opendata"
table_opendata_temp = "opendata_temp"
table_merge = "mysafetymerge"
table_merge_temp = "mysafetymerge_temp"

retry_interval = os.environ['interval']
max_retry_attemps = os.environ['max_retry']