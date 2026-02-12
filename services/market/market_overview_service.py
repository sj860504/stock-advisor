import logging
import pandas as pd
from datetime import datetime
from services.kis.kis_service import KisService
from services.kis.fetch.kis_fetcher import KisFetcher
from utils.logger import get_logger

logger = get_logger("market_overview_service")

class MarketOverviewService:
    """
    주요 시장 지수 및 환율 현황 제공 서비스
    """
    
    # KIS 지수 심볼 매핑
    INDEX_TICKERS = {
        'KOSPI': ('0001', 'KRX'),
        'KOSDAQ': ('1001', 'KRX'),
        'S&P 500': ('SPX', 'IDX'),
        'NASDAQ 100': ('NAS', 'IDX'),
        'USD/KRW': ('FX@KRW', 'IDX')
    }

    @classmethod
    def get_market_summary(cls) -> pd.DataFrame:
        """주요 지수 요약 데이터프레임 반환"""
        token = KisService.get_access_token()
        data = []
        
        for name, (symb, excd) in cls.INDEX_TICKERS.items():
            try:
                if excd == "KRX":
                    res = KisFetcher.fetch_domestic_price(token, symb)
                else:
                    res = KisFetcher.fetch_overseas_price(token, symb, meta={"api_market_code": excd})
                
                price = res.get("price", 0)
                change = res.get("change", 0)
                pct_change = res.get("change_rate", 0)
                
                data.append({
                    "Index": name,
                    "Price": price,
                    "Change": change,
                    "ChangeRate": pct_change
                })
            except Exception as e:
                logger.error(f"Error fetching {name}: {e}")
                data.append({"Index": name, "Price": 0, "Change": 0, "ChangeRate": 0})
        
        return pd.DataFrame(data)

    @classmethod
    def print_summary(cls):
        """콘솔 출력용 시장 요약"""
        df = cls.get_market_summary()
        print(f"=== MARKET OVERVIEW ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')}) ===")
        
        display_data = []
        for _, row in df.iterrows():
            display_data.append({
                "Index": row['Index'],
                "Price": f"{row['Price']:,.2f}",
                "Change": f"{row['Change']:+,.2f}",
                "Change (%)": f"{row['ChangeRate']:+.2f}%"
            })
        
        print(pd.DataFrame(display_data).to_markdown(index=False))
