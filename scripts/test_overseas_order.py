from services.kis.kis_service import KisService

def test_buy_tesla():
    print("\nTesting Buy Order (Tesla - TSLA 1 share @ $380)...")
    ticker = "TSLA"
    market = "NASD"
    qty = 1
    price = 380.00

    print(f"Sending Order: Buy {ticker} {qty} share(s) at ${price}")
    result = KisService.send_overseas_order(ticker, qty, price, order_type="buy", market=market)

    if result['status'] == 'success':
        print(f"Order succeeded! (Order No: {result['data']['ODNO']})")
    else:
        print(f"Order failed: {result.get('msg', 'Unknown Error')}")

if __name__ == "__main__":
    test_buy_tesla()
