import logging
import settings.settings as settings
import os

class LoggerFactory(object) :
    logbot = None
    
    @staticmethod
    def create_logger() :
        # 루트 로거 생성
        LoggerFactory.logbot = logging.getLogger()
        LoggerFactory.logbot.setLevel(settings.log_level)
        
        # 로그 폴더 있는지 확인
        if not os.path.exists(settings.logpath):
            LoggerFactory.logbot.warning("로그 저장 경로 없음")
            LoggerFactory.logbot.info("로그 저장 경로 생성")
            os.makedirs(settings.logpath, exist_ok=True)
        else:
            LoggerFactory.logbot.info("로그 저장 경로 있음")
        
        # 로그 포맷 생성
        formatter = logging.Formatter('[%(asctime)s][%(levelname)s|%(filename)s-%(funcName)s:%(lineno)s] >> %(message)s')    
        
        # 핸들러 생성
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        file_handler = logging.FileHandler(os.path.join(settings.logpath, settings.logfile))
        file_handler.setFormatter(formatter)
        LoggerFactory.logbot.addHandler(stream_handler)
        LoggerFactory.logbot.addHandler(file_handler)
        
    @classmethod
    def get_logger(cls) :
        return cls.logbot