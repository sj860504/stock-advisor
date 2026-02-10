import sys
import os
import FinanceDataReader as fdr
sys.path.append(os.getcwd())

df = fdr.StockListing('KRX')
print(df[df['Name'].str.contains('ACE')].head(10)[['Name', 'Code']])
