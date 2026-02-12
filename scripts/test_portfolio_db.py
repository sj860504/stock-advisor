import os
import sys

# 프로젝트 루트 경로 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.trading.portfolio_service import PortfolioService
from services.market.stock_meta_service import StockMetaService

def test_portfolio_db():
    print("🚀 Testing Portfolio DB CRUD...")
    user_id = "test_user"
    holdings = [
        {"ticker": "005930", "name": "삼성전자", "quantity": 10, "buy_price": 70000.0, "current_price": 72000.0, "sector": "Electronics"},
        {"ticker": "AAPL", "name": "Apple", "quantity": 5, "buy_price": 180.0, "current_price": 190.0, "sector": "Tech"}
    ]
    cash = 1000000.0
    
    # 1. Save
    print("💾 Saving test portfolio...")
    success = PortfolioService.save_portfolio(user_id, holdings, cash_balance=cash)
    if success:
        print("✅ Save Successful.")
    else:
        print("❌ Save Failed.")
        return

    # 2. Load
    print("📥 Loading test portfolio...")
    loaded_holdings = PortfolioService.load_portfolio(user_id)
    loaded_cash = PortfolioService.load_cash(user_id)
    
    print(f"💰 Loaded Cash: {loaded_cash}")
    print(f"📦 Loaded Holdings: {len(loaded_holdings)}")
    
    for h in loaded_holdings:
        print(f"  - {h['ticker']}: {h['quantity']} shares @ {h['buy_price']}")

    if len(loaded_holdings) == 2 and loaded_cash == cash:
        print("🎉 DB CRUD Test PASSED!")
    else:
        print("⚠️ Data Mismatch in DB CRUD Test.")

if __name__ == "__main__":
    test_portfolio_db()
