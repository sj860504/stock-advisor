import os
import sys

# 프로젝트 루트 경로 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.market.stock_meta_service import StockMetaService
from models.portfolio import Portfolio, PortfolioHolding
import os

def check_tables():
    print("🔍 Checking Database Tables...")
    StockMetaService.init_db()
    engine = StockMetaService.engine
    
    from sqlalchemy import inspect
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    
    expected = ['portfolios', 'portfolio_holdings']
    for table in expected:
        if table in tables:
            print(f"✅ Table '{table}' exists.")
        else:
            print(f"❌ Table '{table}' is MISSING!")

if __name__ == "__main__":
    check_tables()
