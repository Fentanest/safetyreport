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
        
        # uvicorn 관련 로거들도 파일에 기록되도록 핸들러 추가
        for logger_name in ["uvicorn", "uvicorn.access", "uvicorn.error"]:
            u_logger = logging.getLogger(logger_name)
            u_logger.addHandler(file_handler)
            # uvicorn 로거는 기본적으로 전파(propagate)가 꺼져있는 경우가 많으므로 
            # 필요 시 전파를 켜거나 핸들러를 직접 붙이는 방식을 사용합니다.
            u_logger.propagate = False # 직접 핸들러를 붙였으므로 이중 로깅 방지를 위해 False 유지
        
    @classmethod
    def get_logger(cls) :
        return cls.logbot