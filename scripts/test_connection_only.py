import requests
import json
import os
from dotenv import load_dotenv

# ?섍꼍蹂??濡쒕뱶
load_dotenv()

APP_KEY = os.getenv("KIS_APP_KEY")
APP_SECRET = os.getenv("KIS_APP_SECRET")
# 媛뺤젣 ?ㅼ쟾?ъ옄 URL ?뚯뒪??
BASE_URL = "https://openapi.koreainvestment.com:9443"

def test_token():
    print("?뵎 Testing Token Issue (REAL SERVER)...")
    print(f"Target URL: {BASE_URL}")
    print(f"App Key (Start): {APP_KEY[:5]}...")
    
    url = f"{BASE_URL}/oauth2/tokenP"
    headers = {"content-type": "application/json"}
    body = {
        "grant_type": "client_credentials",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET
    }
    
    try:
        res = requests.post(url, headers=headers, json=body)
        print(f"Status Code: {res.status_code}")
        
        if res.status_code == 200:
            data = res.json()
            print("??Token Success!")
            print(f"Access Token: {data['access_token'][:10]}...")
            return data['access_token']
        else:
            print("??Token Failed!")
            print(f"Response: {res.text}")
            return None
            
    except Exception as e:
        print(f"??Error: {e}")
        return None

if __name__ == "__main__":
    test_token()
