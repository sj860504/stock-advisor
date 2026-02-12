from services.kis.kis_service import KisService
import time

def test_connection():
    print("?뵆 Testing API Connection (Balance Check)...")
    balance = KisService.get_balance()
    if balance:
        print("??Connection Successful!")
        summary = balance['summary'][0]
        print(f"?뮥 ?덉닔湲?珥앹븸: {summary['dnca_tot_amt']}??)
        print(f"?뱣 ?됯? ?먯씡: {summary['evlu_pfls_smtl_amt']}??)
    else:
        print("??Connection Failed.")

def test_buy_samsung():
    print("\n?썟 Testing Buy Order (Samsung Electronics 1 share - Market Price)...")
    # ?쇱꽦?꾩옄: 005930
    ticker = "005930"
    qty = 1
    price = 0 # 0 = ?쒖옣媛
    
    confirm = input(f"?좑툘 {ticker} {qty}二쇰? ?쒖옣媛濡??뺣쭚 留ㅼ닔?섏떆寃좎뒿?덇퉴? (y/n): ")
    if confirm.lower() == 'y':
        result = KisService.send_order(ticker, qty, price, order_type="buy")
        print(f"寃곌낵: {result}")
    else:
        print("??二쇰Ц 痍⑥냼??")

if __name__ == "__main__":
    test_connection()
    # test_buy_samsung() # ?꾩슂??二쇱꽍 ?댁젣 ???ㅽ뻾
