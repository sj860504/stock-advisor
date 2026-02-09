import yfinance as yf
import pandas as pd
from datetime import datetime

def get_market_overview():
    # Define tickers for major indices
    tickers = {
        'KOSPI': '^KS11',
        'KOSDAQ': '^KQ11',
        'S&P 500': '^GSPC',
        'NASDAQ': '^IXIC',
        'USD/KRW': 'KRW=X'
    }
    
    print(f"=== MARKET OVERVIEW ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')}) ===")
    
    data = []
    for name, ticker_symbol in tickers.items():
        try:
            ticker = yf.Ticker(ticker_symbol)
            # Get fast info first (often more reliable for realtime)
            info = ticker.fast_info
            price = info.last_price
            prev_close = info.previous_close
            
            if price and prev_close:
                change = price - prev_close
                pct_change = (change / prev_close) * 100
                data.append({
                    "Index": name,
                    "Price": f"{price:,.2f}",
                    "Change": f"{change:+,.2f}",
                    "Change (%)": f"{pct_change:+.2f}%"
                })
            else:
                # Fallback to history if fast_info fails
                hist = ticker.history(period="2d")
                if len(hist) >= 2:
                    close = hist['Close'].iloc[-1]
                    prev = hist['Close'].iloc[-2]
                    change = close - prev
                    pct_change = (change / prev) * 100
                    data.append({
                        "Index": name,
                        "Price": f"{close:,.2f}",
                        "Change": f"{change:+,.2f}",
                        "Change (%)": f"{pct_change:+.2f}%"
                    })
                elif len(hist) == 1:
                     data.append({
                        "Index": name,
                        "Price": f"{hist['Close'].iloc[-1]:,.2f}",
                        "Change": "N/A",
                        "Change (%)": "N/A"
                    })
                else:
                    data.append({"Index": name, "Price": "No Data", "Change": "-", "Change (%)": "-"})
                    
        except Exception as e:
            data.append({"Index": name, "Price": "Error", "Change": str(e), "Change (%)": "-"})

    # Display as a formatted table
    df = pd.DataFrame(data)
    print(df.to_markdown(index=False))

if __name__ == "__main__":
    get_market_overview()
