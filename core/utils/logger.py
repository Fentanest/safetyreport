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

            # ── 별점 로거 (파일 핸들러는 별점 작업 시작 시 set_star_log_file()로 설정) ──
            star = logging.getLogger('safetyreport.star')
            star.setLevel(level)
            star.propagate = False
            if not star.handlers:
                star.addHandler(_make_stream_handler())
            LoggerFactory.star_log = star

            # 현재 사용 중인 로그 파일 경로 등록 (파일 브라우저 삭제 보호용)
            LoggerFactory._active_log_paths = [
                os.path.abspath(core_log_path),
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

    @classmethod
    def set_star_log_file(cls, path):
        """별점 작업 시작 시 star_log의 파일 핸들러를 current_rating.log로 교체."""
        star = cls.star_log
        if star is None:
            return
        for h in list(star.handlers):
            if isinstance(h, logging.FileHandler):
                h.close()
                star.removeHandler(h)
        star.addHandler(_make_file_handler(path))
        # 활성 경로에서 이전 rating 로그 경로 교체
        cls._active_log_paths = [p for p in cls._active_log_paths if 'star_' not in os.path.basename(p) and 'current_rating' not in os.path.basename(p)]
        cls._active_log_paths.append(os.path.abspath(path))
