import datetime
import os
import configparser

# Initialize config parser
config = configparser.ConfigParser()
# The path to config.ini should be relative to the project root, 
# but since settings.py is in a subdirectory, we construct the path carefully.
config_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'config.ini')
config.read(config_path)

# Selenium Grid
remotepath = os.getenv('remotepath', config.get('SELENIUM', 'remotepath', fallback=None))

# 접속 주소
loginurl = "https://www.safetyreport.go.kr/#/main/login/login" # 로그인 URL
myreporturl = "https://www.safetyreport.go.kr/#/mypage/mysafereport" # 마이페이지 URL (신고 전체 건 파악에 필요)
mysafereporturl = "https://www.safetyreport.go.kr/#mypage/mysafereport" # 개별 신고건 접속에 필요
titletable = 'table1'

username = os.getenv('USERNAME', config.get('LOGIN', 'username', fallback=None))
password = os.getenv('PASSWORD', config.get('LOGIN', 'password', fallback=None))

datapath = '/app/data'

table_title = "mysafety"
table_detail = "mysafetydetail"
table_merge = "mysafetymerge"

use_telegram_bot = config.getboolean('TELEGRAM', 'use_telegram_bot', fallback=False)
telegram_token = os.getenv('telegram_token', config.get('TELEGRAM', 'telegram_token', fallback=None))
chat_id = os.getenv('chat_id', config.get('TELEGRAM', 'chat_id', fallback=None))

retry_interval = os.getenv('interval', config.get('SETTINGS', 'interval', fallback=60))
max_retry_attemps = os.getenv('max_retry', config.get('SETTINGS', 'max_retry', fallback=10))
max_empty_pages = os.getenv('max_empty_pages', config.get('SETTINGS', 'max_empty_pages', fallback=3))
log_level = os.getenv('log_level', config.get('SETTINGS', 'log_level', fallback="INFO"))
TZ = os.getenv('TZ', config.get('SETTINGS', 'TZ', fallback="Asia/Seoul"))

resultfile = f'{str(datetime.datetime.now()).replace(":","_")[:19]}_results.xlsx'
resultpath = os.path.join(datapath, 'results')
logfile = f'{str(datetime.datetime.now()).replace(":","_")[:19]}.log'
logpath = os.path.join(datapath, 'logs')
google_api_auth_file = os.path.join(datapath, 'auth/gspread.json')
db_path = os.path.join(datapath, 'data.db')
google_sheet_key = os.getenv('sheet_key', config.get('GOOGLESHEET', 'sheet_key', fallback=None))

google_sheet_enabled = os.path.exists(google_api_auth_file) and google_sheet_key is not None
telegram_enabled = (
    use_telegram_bot and
    telegram_token and telegram_token not in [None, 'your_token'] and
    chat_id and chat_id not in [None, 'your_chat_id']
)

if not google_sheet_enabled:
    google_api_auth_file = None
    google_sheet_key = None