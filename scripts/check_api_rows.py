import asyncio
import os
import sys
from datetime import datetime, timedelta

# 프로젝트 루트 경로 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.kis.kis_service import KisService
from services.kis.fetch.kis_fetcher import KisFetcher
from utils.logger import get_logger

logger = get_logger("verify_rows")

async def check_api_rows():
    token = KisService.get_access_token()
    ticker = "005930" # 삼성전자
    
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=400)).strftime("%Y%m%d")
    
    print(f"🚀 Fetching domestic daily price for {ticker} from {start_date} to {end_date}...")
    res = KisFetcher.fetch_daily_price(token, ticker, start_date, end_date)
    
    output2 = res.get('output2', [])
    print(f"📊 Domestic (output2) rows: {len(output2)}")
    
    ticker_us = "NVDA" # 엔비디아
    print(f"🚀 Fetching overseas daily price for {ticker_us}...")
    res_us = KisFetcher.fetch_overseas_daily_price(token, ticker_us, start_date, end_date)
    output_us = res_us.get('output', [])
    print(f"📊 Overseas (output) rows: {len(output_us)}")

if __name__ == "__main__":
    asyncio.run(check_api_rows())
