import os
import sys
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# 프로젝트 루트 경로 추가
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(project_root)

from services.market.stock_meta_service import StockMetaService
from models.stock_meta import ApiTrMeta

def dump_api_meta():
    print("📊 Current api_tr_meta table contents:")
    session = StockMetaService.get_session()
    metas = session.query(ApiTrMeta).all()
    
    print(f"{'ID':<4} | {'API Name':<20} | {'VTS TR':<15} | {'Path'}")
    print("-" * 100)
    for m in metas:
        print(f"{m.id:<4} | {m.api_name:<20} | {m.tr_id_vts or '':<15} | {m.api_path or ''}")

if __name__ == "__main__":
    dump_api_meta()
