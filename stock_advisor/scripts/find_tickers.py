import sys
import os
import pandas as pd
import FinanceDataReader as fdr
sys.path.append(os.getcwd())

# Get KRX listing
print("Fetching KRX listing...")
df = fdr.StockListing('KRX')
print(f"Loaded {len(df)} tickers.")

targets = ["ACE", "KODEX", "TIGER", "삼성전자"]
search_terms = ["다우", "빅테크", "테슬라", "S&P", "QQQ", "삼성"]

for term in search_terms:
    print(f"--- Searching for '{term}' ---")
    results = df[df['Name'].str.contains(term, case=False)]
    for idx, row in results.iterrows():
        # Filter for popular ETF issuers to reduce noise
        if any(issuer in row['Name'] for issuer in targets) or "삼성전자" in row['Name']:
            print(f"{row['Name']} ({row['Code']})")
