import requests
import json

def test_summary_api():
    url = "http://localhost:8000/api/summary"
    print(f"📡 Calling API: {url}")
    try:
        response = requests.get(url, timeout=5)
        print(f"📄 Status Code: {response.status_code}")
        if response.status_code == 200:
            print("✅ Response Data:")
            print(response.text)
        else:
            print(f"❌ Error: {response.text}")
    except Exception as e:
        print(f"❌ Connection failed: {e}")

if __name__ == "__main__":
    test_summary_api()
