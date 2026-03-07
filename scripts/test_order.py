from services.kis.kis_service import KisService
import time

def test_connection():
    print("🔌 API connection test (balance inquiry)...")
    balance = KisService.get_balance()
    if balance:
        print("✅ Connection Successful!")
        summary = balance['summary'][0]
        print(f"💰 Total deposit: {summary['dnca_tot_amt']}")
        print(f"📈 Eval P&L: {summary['evlu_pfls_smtl_amt']}")
    else:
        print("❌ Connection Failed.")

def test_buy_samsung():
    print("\n🛒 삼성전자 1 share market-price buy test...")
    # 삼성전자: 005930
    ticker = "005930"
    qty = 1
    price = 0 # 0 = 시장가
    
    confirm = input(f"⚠️ Buy {qty} shares of {ticker} at market price? (y/n): ")
    if confirm.lower() == 'y':
        result = KisService.send_order(ticker, qty, price, order_type="buy")
        print(f"Result: {result}")
    else:
        print("Order cancelled")

if __name__ == "__main__":
    test_connection()
    # test_buy_samsung() # 필요 시 주석 해제 후 실행
