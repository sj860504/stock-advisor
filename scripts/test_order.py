from services.kis.kis_service import KisService
import time

def test_connection():
    print("🔌 API 연결 테스트 (잔고 조회)...")
    balance = KisService.get_balance()
    if balance:
        print("✅ Connection Successful!")
        summary = balance['summary'][0]
        print(f"💰 예수금 총액: {summary['dnca_tot_amt']}")
        print(f"📈 평가 손익: {summary['evlu_pfls_smtl_amt']}")
    else:
        print("❌ Connection Failed.")

def test_buy_samsung():
    print("\n🛒 삼성전자 1주 시장가 매수 테스트...")
    # 삼성전자: 005930
    ticker = "005930"
    qty = 1
    price = 0 # 0 = 시장가
    
    confirm = input(f"⚠️ {ticker} {qty}주를 시장가로 매수할까요? (y/n): ")
    if confirm.lower() == 'y':
        result = KisService.send_order(ticker, qty, price, order_type="buy")
        print(f"결과: {result}")
    else:
        print("주문 취소")

if __name__ == "__main__":
    test_connection()
    # test_buy_samsung() # 필요 시 주석 해제 후 실행
