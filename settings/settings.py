import datetime
import os
import configparser
import io
import threading
import hashlib

class AppSettings:
    def __init__(self):
        import sys
        self.config = configparser.ConfigParser()
        self._lock = threading.RLock()
        self._draft = threading.local()
        
        if getattr(sys, 'frozen', False):
            # When frozen, use the executable's directory as the root for persistent data
            project_root = os.path.dirname(sys.executable)
        else:
            project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

        from core.utils.runtime_mode import data_dir_override, is_fixture_mode
        override = data_dir_override()
        if is_fixture_mode() and not override:
            raise RuntimeError("SAFETYREPORT_FIXTURE_MODE 는 SAFETYREPORT_DATA_DIR(운영 data/ 가 아닌 경로)와 함께 써야 합니다.")
        self.datapath = override or os.path.join(project_root, 'data')
        self.config_path = os.path.join(self.datapath, 'config.ini')
        os.makedirs(self.datapath, exist_ok=True)
        
        self.table_title = "mysafety"
        self.table_detail_traffic = "mysafetydetail_traffic"
        self.table_detail_parking = "mysafetydetail_parking"
        self.table_detail_other = "mysafetydetail_other"
        self.table_merge_traffic = "mysafetymerge_traffic"
        self.table_merge_parking = "mysafetymerge_parking"
        self.table_merge_other = "mysafetymerge_other"
        
        self.loginurl = "https://www.safetyreport.go.kr/#/main/login/login"
        self.myreporturl = "https://www.safetyreport.go.kr/#/mypage/mysafereport"
        self.mysafereporturl = "https://www.safetyreport.go.kr/#mypage/mysafereport"
        self.titletable = 'table1'
        
        self.load()

    def _fingerprint(self):
        try:
            with open(self.config_path, 'rb') as source:
                return hashlib.sha256(source.read()).digest()
        except FileNotFoundError:
            return None

    def load(self):
        with self._lock:
            config = configparser.ConfigParser()
            try:
                with open(self.config_path, 'rb') as source:
                    payload = source.read()
            except FileNotFoundError:
                payload = None
            if payload is not None:
                config.read_string(payload.decode('utf-8'))
            candidate = object.__new__(AppSettings)
            candidate.__dict__.update(self.__dict__)
            candidate.config = config
            candidate._load_fields()
            candidate._loaded_fingerprint = hashlib.sha256(payload).digest() if payload is not None else None
            self.__dict__.update(candidate.__dict__)

    def _load_fields(self):
        
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

        self.auto_export_excel = self.config.getboolean('SETTINGS', 'auto_export_excel', fallback=True)
        self.auto_export_sheet = self.config.getboolean('SETTINGS', 'auto_export_sheet', fallback=False)
        # 크롤링은 API 방식·전체(full)만 있다(레거시·최소 크롤링은 2026-09-25 제거). 예전 설정값(legacy/min)은 무시한다.
        self.crawl_mode = 'full'
        self.crawl_type = 'api'
        self.exclude_withdraw = self.config.getboolean('SETTINGS', 'exclude_withdraw', fallback=True)
        self.use_representative_records = self.config.getboolean('SETTINGS', 'use_representative_records', fallback=True)
        self.retry_interval = int(self.config.get('SETTINGS', 'retry_interval', fallback=10))
        self.max_retry_attemps = int(self.config.get('SETTINGS', 'max_retry_attemps', fallback=5))
        self.max_empty_pages = int(self.config.get('SETTINGS', 'max_empty_pages', fallback=3))
        self.session_max_age = int(self.config.get('SETTINGS', 'session_max_age', fallback=10800))
        self.log_level = self.config.get('SETTINGS', 'log_level', fallback="INFO")
        self.TZ = self.config.get('SETTINGS', 'TZ', fallback="Asia/Seoul")
        self.trusted_proxies = self.config.get('SETTINGS', 'trusted_proxies', fallback='')
        
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

        from core.utils.runtime_mode import is_fixture_mode
        if is_fixture_mode():
            # 테스트 fixture 에서는 텔레그램 봇/알림과 구글 시트 업로드를 설정과 무관하게 끈다.
            self.telegram_enabled = False
            self.google_sheet_enabled = False

        if not self.google_sheet_enabled:
            self.google_api_auth_file = None
            self.google_sheet_key = None

    def update_config(self, section, key, value):
        # 연속 update가 다른 요청의 미완성 값과 섞이지 않게 요청 스레드에 둔다.
        pending = getattr(self._draft, 'pending', None)
        if pending is None:
            pending = self._draft.pending = {}
        pending[(section, key)] = str(value)

    def save(self):
        from core.utils.atomic_file import write_bytes
        from core.utils.file_lock import exclusive
        pending = getattr(self._draft, 'pending', {})
        self._draft.pending = {}
        with self._lock, exclusive(self.config_path + '.lock'):
            if pending:
                config = configparser.ConfigParser()
                config.read(self.config_path, encoding='utf-8')
                for (section, key), value in pending.items():
                    if not config.has_section(section):
                        config.add_section(section)
                    config.set(section, key, value)
            else:
                if self._fingerprint() != getattr(self, '_loaded_fingerprint', None):
                    raise RuntimeError('설정이 다른 작업에서 바뀌었습니다. 다시 불러온 뒤 저장하세요.')
                config = configparser.ConfigParser()
                config.read_dict({section: dict(self.config[section]) for section in self.config.sections()})
            raw_pw = config.get('LOGIN', 'password', fallback='')
            if raw_pw and not raw_pw.startswith('enc:'):
                from core.utils.security import encrypt_config_value
                config.set('LOGIN', 'password', encrypt_config_value(raw_pw, self.datapath))
            candidate = object.__new__(AppSettings)
            candidate.__dict__.update(self.__dict__)
            candidate.config = config
            candidate._load_fields()  # 불법 int/bool 등은 디스크나 메모리 게시 전에 거절한다.
            output = io.StringIO()
            config.write(output)
            payload = output.getvalue().encode('utf-8')
            write_bytes(self.config_path, payload)
            candidate._loaded_fingerprint = hashlib.sha256(payload).digest()
            self.__dict__.update(candidate.__dict__)
            self._draft.pending = {}

_instance = AppSettings()

def __getattr__(name):
    return getattr(_instance, name)
