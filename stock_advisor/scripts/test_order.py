from stock_advisor.services.kis_service import KisService
import time

def test_connection():
    print("🔌 Testing API Connection (Balance Check)...")
    balance = KisService.get_balance()
    if balance:
        print("✅ Connection Successful!")
        summary = balance['summary'][0]
        print(f"💰 예수금 총액: {summary['dnca_tot_amt']}원")
        print(f"📉 평가 손익: {summary['evlu_pfls_smtl_amt']}원")
    else:
        print("❌ Connection Failed.")

def test_buy_samsung():
    print("\n🛒 Testing Buy Order (Samsung Electronics 1 share - Market Price)...")
    # 삼성전자: 005930
    ticker = "005930"
    qty = 1
    price = 0 # 0 = 시장가
    
    confirm = input(f"⚠️ {ticker} {qty}주를 시장가로 정말 매수하시겠습니까? (y/n): ")
    if confirm.lower() == 'y':
        result = KisService.send_order(ticker, qty, price, order_type="buy")
        print(f"결과: {result}")
    else:
        print("⛔ 주문 취소됨.")

if __name__ == "__main__":
    test_connection()
    # test_buy_samsung() # 필요시 주석 해제 후 실행
