import json
import os
import FinanceDataReader as fdr
import pandas as pd
from datetime import datetime

# Load Portfolio
portfolio_path = 'stock_advisor/data/portfolio_sean.json'
with open(portfolio_path, 'r', encoding='utf-8') as f:
    holdings = json.load(f)

# Load ETF Listing for resolution
print("Loading ETF listing...")
try:
    etf_df = fdr.StockListing('ETF/KR')
    etf_map = {row['Name']: row['Symbol'] for _, row in etf_df.iterrows()}
    # Also create a map for searching partial names if needed
except Exception as e:
    print(f"Failed to load ETF listing: {e}")
    etf_map = {}

# Manual Fixes / Fuzzy Map
manual_map = {
    "samsung": "005930",
    "ACE?ㅼ슦": "ACE 誘멸뎅諛곕떦?ㅼ슦議댁뒪", 
    "ACE鍮낇뀒??: "ACE 誘멸뎅鍮낇뀒?촖OP7 Plus",
    "ACE?뚯뒳??: "ACE ?뚯뒳?쇰갭瑜섏껜?몄븸?곕툕", 
    "KODEX S&P": "KODEX 誘멸뎅S&P500", 
    "TIGER DAWOO": "TIGER 誘멸뎅?ㅼ슦議댁뒪30",
    "TIGER QQQ": "TIGER 誘멸뎅?섏뒪??00",
    "TIGER S&P": "TIGER 誘멸뎅S&P500",
    "TIGER 誘멸뎅S&P500": "TIGER 誘멸뎅S&P500",
    "TIGER 誘멸뎅?섏뒪??00": "TIGER 誘멸뎅?섏뒪??00",
    "TIGER 誘멸뎅諛곕떦?ㅼ슦議댁뒪": "TIGER 誘멸뎅諛곕떦?ㅼ슦議댁뒪",
    "ACE 誘멸뎅諛곕떦?ㅼ슦議댁뒪": "ACE 誘멸뎅諛곕떦?ㅼ슦議댁뒪"
}

# Resolve Names to Codes
# If name in manual_map, use that name to find code in etf_map, or use value as code if it looks like one.
def resolve_code(ticker, name):
    # 1. Check manual map
    target_name = manual_map.get(ticker) or manual_map.get(name)
    if target_name:
        if target_name.isdigit(): return target_name # Direct code
        # Find code for name
        # Try exact match first
        if target_name in etf_map:
            return etf_map[target_name]
        # Try finding in dataframe
        if not etf_df.empty:
            matches = etf_df[etf_df['Name'].str.contains(target_name, case=False, na=False)]
            if not matches.empty:
                return matches.iloc[0]['Symbol']
    
    # 2. If ticker is valid code (6 digits)
    if ticker.isdigit() and len(ticker) == 6:
        return ticker
    
    # 3. If ticker is US symbol (Alpha)
    if ticker.replace('.','').isalpha():
        return ticker
        
    return None

alerts = []

print("Checking prices...")
for h in holdings:
    ticker = h.get('ticker')
    name = h.get('name')
    buy_price = h.get('buy_price')
    qty = h.get('quantity')
    
    if not buy_price or qty == 0:
        continue
        
    code = resolve_code(ticker, name)
    if not code:
        print(f"Could not resolve: {ticker} / {name}")
        continue
        
    try:
        # Fetch price
        df = fdr.DataReader(code)
        if df is None or df.empty:
            # Try appending .KS or .KQ if pure digits?
            # fdr usually handles digits as KRX.
            # But maybe it failed.
            print(f"No data for {code}")
            continue
            
        current_price = float(df['Close'].iloc[-1])
        
        # Calculate Profit
        val = current_price * qty
        inv = buy_price * qty
        profit_pct = ((val - inv) / inv) * 100
        
        if profit_pct <= -10:
            alerts.append({
                "ticker": code, # Show code or name
                "name": name,
                "profit_pct": profit_pct,
                "current_price": current_price,
                "buy_price": buy_price
            })
            
    except Exception as e:
        print(f"Error checking {code}: {e}")

# Output results
if alerts:
    print("\n--- ALERTS ---")
    for a in alerts:
        print(f"- {a['name']} ({a['ticker']}): {a['profit_pct']:.2f}% (Curr: {a['current_price']}, Buy: {a['buy_price']})")
else:
    print("\nNo alerts triggered.")
