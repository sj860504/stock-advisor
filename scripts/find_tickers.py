import sys
import os
import pandas as pd
import FinanceDataReader as fdr
sys.path.append(os.getcwd())

# Get KRX listing
print("Fetching KRX listing...")
df = fdr.StockListing('KRX')
print(f"Loaded {len(df)} tickers.")

targets = ["ACE", "KODEX", "TIGER", "?쇱꽦?꾩옄"]
search_terms = ["?ㅼ슦", "鍮낇뀒??, "?뚯뒳??, "S&P", "QQQ", "?쇱꽦"]

for term in search_terms:
    print(f"--- Searching for '{term}' ---")
    results = df[df['Name'].str.contains(term, case=False)]
    for idx, row in results.iterrows():
        # Filter for popular ETF issuers to reduce noise
        if any(issuer in row['Name'] for issuer in targets) or "?쇱꽦?꾩옄" in row['Name']:
            print(f"{row['Name']} ({row['Code']})")
