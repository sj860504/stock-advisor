from stock_advisor.services.kis_service import KisService

def test_buy_tesla():
    print("\n🚗 Testing Buy Order (Tesla - TSLA 1 share @ $380)...")
    ticker = "TSLA"
    market = "NASD" # 나스닥
    qty = 1
    price = 380.00 # 테스트용 지정가
    
    print(f"📡 Sending Order: Buy {ticker} {qty} share(s) at ${price}")
    result = KisService.send_overseas_order(ticker, qty, price, order_type="buy", market=market)
    
    if result['status'] == 'success':
        print(f"✅ 주문 접수 성공! (주문번호: {result['data']['ODNO']})")
    else:
        print(f"❌ 주문 실패: {result.get('msg', 'Unknown Error')}")

if __name__ == "__main__":
    test_buy_tesla()
