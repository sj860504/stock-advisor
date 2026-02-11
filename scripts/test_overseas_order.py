from services.kis_service import KisService

def test_buy_tesla():
    print("\n?슅 Testing Buy Order (Tesla - TSLA 1 share @ $380)...")
    ticker = "TSLA"
    market = "NASD" # ?섏뒪??
    qty = 1
    price = 380.00 # ?뚯뒪?몄슜 吏?뺢?
    
    print(f"?뱻 Sending Order: Buy {ticker} {qty} share(s) at ${price}")
    result = KisService.send_overseas_order(ticker, qty, price, order_type="buy", market=market)
    
    if result['status'] == 'success':
        print(f"??二쇰Ц ?묒닔 ?깃났! (二쇰Ц踰덊샇: {result['data']['ODNO']})")
    else:
        print(f"??二쇰Ц ?ㅽ뙣: {result.get('msg', 'Unknown Error')}")

if __name__ == "__main__":
    test_buy_tesla()
