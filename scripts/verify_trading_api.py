import sys
import os
import argparse

# 프로젝트 루트 경로 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.config.settings_service import SettingsService
from services.trading.order_service import OrderService
from services.market.stock_meta_service import StockMetaService
from models.trade_history import TradeHistory
from models.settings import Settings

def run_verification():
    print("🚀 [Verification] Starting Trading API Verification...")
    
    # 1. DB 초기화 (테이블 생성 확인)
    print("🔹 [1/4] Initialize DB and Models...")
    StockMetaService.init_db()
    SettingsService.init_defaults()
    print("✅ DB Initialized.")

    # 2. 설정 서비스 테스트
    print("\n🔹 [2/4] Testing SettingsService...")
    # 기본값 확인
    buy_threshold = SettingsService.get_int("STRATEGY_BUY_THRESHOLD")
    print(f"Current BUY_THRESHOLD: {buy_threshold}")
    
    # 값 변경 확인
    test_key = "STRATEGY_BUY_THRESHOLD"
    original_val = SettingsService.get_setting(test_key)
    new_val = "80"
    
    SettingsService.set_setting(test_key, new_val)
    updated_val = SettingsService.get_setting(test_key)
    
    if updated_val == new_val:
        print(f"✅ Settings update success: {original_val} -> {updated_val}")
    else:
        print(f"❌ Settings update failed: expected {new_val}, got {updated_val}")
        
    # 복구
    SettingsService.set_setting(test_key, original_val)

    # 3. 매매 내역 서비스 테스트
    print("\n🔹 [3/4] Testing OrderService...")
    ticker = "TEST001"
    qty = 10
    price = 50000
    
    # 기록
    trade = OrderService.record_trade(ticker, "buy", qty, price, "Test Trade", "verification_script")
    if trade and trade.id:
        print(f"✅ Trade recorded: ID {trade.id}")
    else:
        print("❌ Trade record failed")
        
    # 조회
    history = OrderService.get_trade_history(limit=5)
    last_trade = next((t for t in history if t.ticker == ticker), None)
    if last_trade:
        print(f"✅ Trade history retrieval success: {last_trade.ticker} {last_trade.quantity}qty")
    else:
        print("❌ Trade history retrieval failed")

    # 4. 결론
    print("\n🎉 Verification Completed!")

if __name__ == "__main__":
    run_verification()
