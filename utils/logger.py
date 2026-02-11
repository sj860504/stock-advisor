import logging
import os
from logging.handlers import RotatingFileHandler

def get_logger(name: str):
    """
    ?좏뵆由ъ??댁뀡 ?꾩뿭 濡쒓굅 ?ㅼ젙
    - 肄섏넄 異쒕젰 (INFO ?덈꺼)
    - ?뚯씪 ???(DEBUG ?덈꺼, rotating)
    """
    logger = logging.getLogger(name)
    
    if not logger.handlers:
        logger.setLevel(logging.DEBUG)
        
        # ?щ㎎ ?ㅼ젙
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        
        # 1. 肄섏넄 ?몃뱾??
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
        
        # 2. ?뚯씪 ?몃뱾??(logs ?붾젆?좊━?????
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
