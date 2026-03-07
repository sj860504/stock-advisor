import sys
import os
import pandas as pd
import FinanceDataReader as fdr
sys.path.append(os.getcwd())

# Get KRX listing
print("Fetching KRX listing...")
df = fdr.StockListing('KRX')
print(f"Loaded {len(df)} tickers.")

targets = ["ACE", "KODEX", "TIGER", "Samsung"]
search_terms = ["Dow", "BigTech", "Tesla", "S&P", "QQQ", "Samsung"]

for term in search_terms:
    print(f"--- Searching for '{term}' ---")
    results = df[df['Name'].str.contains(term, case=False)]
    for idx, row in results.iterrows():
        if any(issuer in row['Name'] for issuer in targets):
            print(f"{row['Name']} ({row['Code']})")
