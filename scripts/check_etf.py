import FinanceDataReader as fdr
try:
    df = fdr.StockListing('ETF/KR')
    print(df.head())
    print("ACE check:")
    print(df[df['Name'].str.contains('ACE')].head())
except Exception as e:
    print(e)
