import logging
import settings.settings as settings
import os
import datetime


def _get_formatter():
    return logging.Formatter('[%(asctime)s][%(levelname)s|%(filename)s-%(funcName)s:%(lineno)s] >> %(message)s')


def _make_stream_handler():
    import sys
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(_get_formatter())
    return h


def _make_file_handler(path):
    # delay=True: 실제로 로그가 기록될 때까지 파일을 생성하지 않음
    h = logging.FileHandler(path, encoding='utf-8', delay=True)
    h.setFormatter(_get_formatter())
    return h


class LoggerFactory:
    logbot = None    # core 로거 (메인 프로세스) 또는 crawl 로거 (크롤링 서브프로세스)
    star_log = None  # 별점 로거 (메인 프로세스 전용)
    _active_log_paths: list = []  # 현재 사용 중인 로그 파일 경로 목록 (삭제 보호용)

    @staticmethod
    def create_logger(mode='core'):
        """
        mode='core' : 메인 앱용. logbot(코어)과 star_log(별점) 두 로거를 생성.
                      파일: core_<timestamp>.log, star_<timestamp>.log
        mode='crawl': 크롤링 서브프로세스용. logbot(크롤) 하나만 생성.
                      stdout → current_crawl.log 파이프 + crawl_<timestamp>.log 파일
        """
        if not os.path.exists(settings.logpath):
            os.makedirs(settings.logpath, exist_ok=True)

        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H_%M_%S")
        level_str = settings.log_level.upper() if isinstance(settings.log_level, str) else 'INFO'
        level = getattr(logging, level_str, logging.INFO)

        if mode == 'core':
            # ── 코어 로거 (웹서버, 스케줄러, DB 초기화 등) ──
            core_log_path = os.path.join(settings.logpath, f'core_{now_str}.log')
            core = logging.getLogger('safetyreport.core')
            core.setLevel(level)
            core.propagate = False
            if not core.handlers:
                core.addHandler(_make_stream_handler())
                core.addHandler(_make_file_handler(core_log_path))
            LoggerFactory.logbot = core

            # ── 별점 로거 ──
            star_log_path = os.path.join(settings.logpath, f'star_{now_str}.log')
            star = logging.getLogger('safetyreport.star')
            star.setLevel(level)
            star.propagate = False
            if not star.handlers:
                star.addHandler(_make_stream_handler())
                star.addHandler(_make_file_handler(star_log_path))
            LoggerFactory.star_log = star

            # 현재 사용 중인 로그 파일 경로 등록 (파일 브라우저 삭제 보호용)
            LoggerFactory._active_log_paths = [
                os.path.abspath(core_log_path),
                os.path.abspath(star_log_path),
            ]

            # ── uvicorn 로거 → 코어 파일 핸들러에 연결 ──
            core_file_h = core.handlers[1]
            for lname in ["uvicorn", "uvicorn.access", "uvicorn.error"]:
                u = logging.getLogger(lname)
                u.propagate = False
                if core_file_h not in u.handlers:
                    u.addHandler(core_file_h)

        else:  # 'crawl'
            # ── 크롤링 서브프로세스: 루트 로거 사용
            # stdout이 Popen에 의해 current_crawl.log로 파이프됨.
            # FileHandler를 별도로 두지 않음 — wait_and_rotate_log()가 완료 후 백업을 처리함.
            crawl = logging.getLogger()
            crawl.setLevel(level)
            if not crawl.handlers:
                crawl.addHandler(_make_stream_handler())
            LoggerFactory.logbot = crawl

    @classmethod
    def get_logger(cls):
        return cls.logbot
