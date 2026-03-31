import datetime
import os
import configparser

class AppSettings:
    def __init__(self):
        import sys
        self.config = configparser.ConfigParser()
        
        if getattr(sys, 'frozen', False):
            # When frozen, use the executable's directory as the root for persistent data
            project_root = os.path.dirname(sys.executable)
        else:
            project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
            
        self.datapath = os.path.join(project_root, 'data')
        self.config_path = os.path.join(self.datapath, 'config.ini')
        os.makedirs(self.datapath, exist_ok=True)
        
        self.table_title = "mysafety"
        self.table_detail_traffic = "mysafetydetail_traffic"
        self.table_detail_other = "mysafetydetail_other"
        self.table_merge_traffic = "mysafetymerge_traffic"
        self.table_merge_other = "mysafetymerge_other"
        
        self.loginurl = "https://www.safetyreport.go.kr/#/main/login/login"
        self.myreporturl = "https://www.safetyreport.go.kr/#/mypage/mysafereport"
        self.mysafereporturl = "https://www.safetyreport.go.kr/#mypage/mysafereport"
        self.titletable = 'table1'
        
        self.load()

    def load(self):
        self.config.read(self.config_path)
        
        self.remotepath = self.config.get('SELENIUM', 'remotepath', fallback="http://localhost:4444/wd/hub")
        self.chrome_mode = self.config.get('SELENIUM', 'chrome_mode', fallback='hub')
        self.remote_debug_port = self.config.get('SELENIUM', 'remote_debug_port', fallback='9222')
        self.headless = self.config.getboolean('SELENIUM', 'headless', fallback=False)

        self.username = self.config.get('LOGIN', 'username', fallback=None)
        _raw_pw = self.config.get('LOGIN', 'password', fallback=None)
        if _raw_pw:
            from core.utils.security import decrypt_config_value
            self.password = decrypt_config_value(_raw_pw, self.datapath)
        else:
            self.password = None

        self.telegram_token = self.config.get('TELEGRAM', 'telegram_token', fallback=None)
        self.chat_id = self.config.get('TELEGRAM', 'chat_id', fallback=None)

        self.scheduler_enabled = self.config.getboolean('SCHEDULER', 'enabled', fallback=False)
        self.scheduler_mode = self.config.get('SCHEDULER', 'mode', fallback='interval')
        self.scheduler_interval_hours = int(self.config.get('SCHEDULER', 'interval_hours', fallback=24))
        self.scheduler_cron_times = self.config.get('SCHEDULER', 'cron_times', fallback='09:00')
        self.scheduler_interval_start = self.config.get('SCHEDULER', 'interval_start', fallback='00:00')

        self.phone_number = self.config.get('RATING', 'phone_number', fallback='')

        self.normalize_police = self.config.getboolean('SETTINGS', 'normalize_police', fallback=True)
        self.exclude_withdraw = self.config.getboolean('SETTINGS', 'exclude_withdraw', fallback=True)
        self.retry_interval = int(self.config.get('SETTINGS', 'retry_interval', fallback=10))
        self.max_retry_attemps = int(self.config.get('SETTINGS', 'max_retry_attemps', fallback=3))
        self.max_empty_pages = int(self.config.get('SETTINGS', 'max_empty_pages', fallback=3))
        self.session_max_age = int(self.config.get('SETTINGS', 'session_max_age', fallback=10800))
        self.log_level = self.config.get('SETTINGS', 'log_level', fallback="INFO")
        self.TZ = self.config.get('SETTINGS', 'TZ', fallback="Asia/Seoul")
        
        now_str = str(datetime.datetime.now()).replace(":","_")[:19]
        
        self.resultfile = f'{now_str}_results.xlsx'
        self.resultpath = os.path.join(self.datapath, 'results')
        self.logfile = f'{now_str}.log'
        self.logpath = os.path.join(self.datapath, 'logs')
        self.google_api_auth_file = os.path.join(self.datapath, 'auth/gspread.json')
        self.db_path = os.path.join(self.datapath, 'data.db')
        self.google_sheet_key = self.config.get('GOOGLESHEET', 'sheet_key', fallback=None)

        self.google_sheet_enabled = os.path.exists(self.google_api_auth_file) and self.google_sheet_key is not None
        self.telegram_enabled = (
            self.telegram_token and self.telegram_token not in [None, 'your_token'] and
            self.chat_id and self.chat_id not in [None, 'your_chat_id']
        )

        if not self.google_sheet_enabled:
            self.google_api_auth_file = None
            self.google_sheet_key = None

    def update_config(self, section, key, value):
        if not self.config.has_section(section):
            self.config.add_section(section)
        self.config.set(section, key, str(value))
        
    def save(self):
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        # 비밀번호가 평문이면 저장 전에 암호화
        raw_pw = self.config.get('LOGIN', 'password', fallback='')
        if raw_pw and not raw_pw.startswith('enc:'):
            from core.utils.security import encrypt_config_value
            self.config.set('LOGIN', 'password', encrypt_config_value(raw_pw, self.datapath))
        with open(self.config_path, 'w') as configfile:
            self.config.write(configfile)
        self.load()

_instance = AppSettings()

def __getattr__(name):
    return getattr(_instance, name)