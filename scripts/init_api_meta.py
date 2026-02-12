import os
import sys
from datetime import datetime

# 프로젝트 루트 경로 추가
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(project_root)

from services.market.stock_meta_service import StockMetaService
from models.stock_meta import ApiTrMeta

def populate_tr_ids():
    print("📦 KIS TR ID 데이터베이스 연동 및 초기화 중...")
    StockMetaService.init_db()
    count = StockMetaService.init_api_tr_meta()
    print(f"✅ 총 {count}개의 TR ID 정보가 저장되었습니다.")

if __name__ == "__main__":
    populate_tr_ids()
