import logging
import os
from logging.handlers import RotatingFileHandler

def get_logger(name: str):
    """
    애플리케이션 전역 로거 설정
    - 콘솔 출력 (INFO 레벨)
    - 파일 저장 (DEBUG 레벨, rotating)
    """
    logger = logging.getLogger(name)
    
    if not logger.handlers:
        logger.setLevel(logging.DEBUG)
        
        # 포맷 설정
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        
        # 1. 콘솔 핸들러
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
        
        # 2. 파일 핸들러 (logs 디렉토리에 저장)
        log_dir = "logs"
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)
            
        file_handler = RotatingFileHandler(
            os.path.join(log_dir, "app.log"), 
            maxBytes=10*1024*1024, # 10MB
            backupCount=5,
            encoding='utf-8'
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        
    return logger
