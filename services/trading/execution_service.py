import requests
import json
import os
from typing import Optional

from utils.market import is_kr

class ExecutionService:
    """
    Real-time order execution service via KIS (Korea Investment & Securities) API
    """
    _base_url = "https://openapivts.koreainvestment.com:29443" # Paper trading URL
    _access_token: Optional[str] = None
    
    @classmethod
    def _get_token(cls):
        """Issue token for API access."""
        # Load API keys from environment variables
        app_key = os.getenv("KIS_APP_KEY")
        app_secret = os.getenv("KIS_APP_SECRET")
        
        if not app_key or not app_secret:
            print("KIS API keys are not configured.")
            return None

        url = f"{cls._base_url}/oauth2/tokenP"
        payload = {
            "grant_type": "client_credentials",
            "appkey": app_key,
            "appsecret": app_secret
        }
        
        try:
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
            cls._access_token = response.json().get("access_token")
            print("KIS API token issued successfully")
            return cls._access_token
        except Exception as e:
            print(f"Token issuance error: {e}")
            return None

    @classmethod
    def buy_market_order(cls, ticker: str, quantity: int):
        """Market price buy order."""
        if not cls._access_token:
            cls._get_token()
            
        url = f"{cls._base_url}/uapi/domestic-stock/v1/trading/order-cash" # Domestic stock order URL

        # Overseas stocks use different URL/headers
        if not is_kr(ticker): # Overseas stock (US, etc.)
            url = f"{cls._base_url}/uapi/overseas-stock/v1/trading/order"

        headers = {
            "Content-Type": "application/json",
            "authorization": f"Bearer {cls._access_token}",
            "appkey": os.getenv("KIS_APP_KEY"),
            "appsecret": os.getenv("KIS_APP_SECRET"),
            "tr_id": "VTTT0001U" if is_kr(ticker) else "VTTT1002U" # Paper trading buy TR ID
        }
        
        # Actual order data requires account info
        # Currently operates in simulation mode since no account info is configured
        print(f"[{ticker}] {quantity} shares market buy order attempt...")
        return {"status": "ready", "message": "Actual orders will be executed once API account info is configured."}

    @classmethod
    def get_balance(cls):
        """Query account balance and holdings."""
        print("📊 Fetching account balance...")
        return {"cash": 10000000, "stocks": []} # Dummy data for testing
