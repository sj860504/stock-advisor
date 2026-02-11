import sqlite3
import os

db_path = 'data/stock_advisor.db'
if not os.path.exists(db_path):
    print(f"File not found: {db_path}")
    exit(1)

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

print("--- ApiTrMeta ---")
cursor.execute('SELECT category, api_name, tr_id_real, tr_id_vts FROM api_tr_meta WHERE api_name LIKE "%순위%";')
for row in cursor.fetchall():
    print(row)

print("\n--- StockMeta Count ---")
cursor.execute('SELECT market_type, exchange_code, COUNT(*) FROM stock_meta GROUP BY market_type, exchange_code;')
for row in cursor.fetchall():
    print(row)

conn.close()
