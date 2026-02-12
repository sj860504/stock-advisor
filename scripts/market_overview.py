from datetime import datetime
import pandas as pd
from services.kis.kis_service import KisService
from services.kis.fetch.kis_fetcher import KisFetcher

def get_market_overview():
    # KIS 지수 심볼 매핑
    tickers = {
        'KOSPI': ('0001', 'KRX'),
        'KOSDAQ': ('1001', 'KRX'),
        'S&P 500': ('SPX', 'IDX'),
        'NASDAQ 100': ('NAS', 'IDX'),
        'USD/KRW': ('FX@KRW', 'IDX') # KIS 환율 심볼 예시
    }
    
    print(f"=== MARKET OVERVIEW ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')}) ===")
    
    token = KisService.get_access_token()
    data = []
    
    for name, (symb, excd) in tickers.items():
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
                "Price": f"{price:,.2f}",
                "Change": f"{change:+,.2f}",
                "Change (%)": f"{pct_change:+.2f}%"
            })
        except Exception as e:
            data.append({"Index": name, "Price": "Error", "Change": str(e), "Change (%)": "-"})

    # Display as a formatted table
    df = pd.DataFrame(data)
    print(df.to_markdown(index=False))

if __name__ == "__main__":
    get_market_overview()
