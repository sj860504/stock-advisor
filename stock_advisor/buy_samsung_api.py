import sys
import os
import requests
import json

# Add project root to path so we can import modules
sys.path.append(os.getcwd())

# Define local API endpoint
API_URL = "http://localhost:8000/trading/order"

def buy_samsung_api():
    print("🚀 삼성전자(005930) 1주 시장가 매수 주문 시도 (via Local API)...")
    
    order_data = {
        "ticker": "005930",
        "quantity": 1,
        "price": 0,
        "order_type": "buy"
    }
    
    try:
        res = requests.post(API_URL, json=order_data)
        
        if res.status_code == 200:
            result = res.json()
            if result['status'] == 'success':
                print(f"✅ 매수 주문 성공!")
                print(f"주문 번호: {result['data']['ODNO']}")
                print(f"상세 데이터: {result}")
            else:
                print(f"❌ 매수 주문 실패 (API OK, but Order Failed): {result.get('msg')}")
        else:
            print(f"❌ API 요청 실패: {res.status_code} - {res.text}")
            
    except Exception as e:
        print(f"❌ 오류 발생: {e}")
        print("💡 팁: 서버가 실행 중인지 확인하세요. (uvicorn stock_advisor.main:app --reload)")

if __name__ == "__main__":
    buy_samsung_api()
