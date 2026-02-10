import FinanceDataReader as fdr
df = fdr.StockListing('ETF/KR')
with open("etf_names.txt", "w") as f:
    for name in df['Name']:
        if any(x in name for x in ["ACE", "KODEX", "TIGER"]):
            f.write(name + "\n")
