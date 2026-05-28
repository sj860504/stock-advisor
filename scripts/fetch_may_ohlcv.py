"""
5월 거래 종목 일별 OHLCV 데이터 수집
output: data/may_ohlcv_raw.csv
"""
import ssl
ssl._create_default_https_context = ssl._create_unverified_context

import yfinance as yf
import pandas as pd
import sqlite3
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# SSL 검증 비활성화 세션 주입
session = requests.Session()
session.verify = False
adapter = HTTPAdapter(max_retries=Retry(total=3, backoff_factor=0.5))
session.mount("https://", adapter)
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

DB_PATH = "data/stock_advisor.db"
OUTPUT_PATH = "data/may_ohlcv_raw.csv"
START = "2026-04-30"
END = "2026-05-29"

# 1. 5월 거래 종목 조회
conn = sqlite3.connect(DB_PATH)
tickers_df = pd.read_sql("""
    SELECT DISTINCT ticker FROM trade_history
    WHERE date(timestamp) >= '2026-05-01'
    ORDER BY ticker
""", conn)
conn.close()

kr_tickers = [t for t in tickers_df["ticker"] if t.isdigit()]
us_tickers  = [t for t in tickers_df["ticker"] if not t.isdigit()]

print(f"KR: {len(kr_tickers)}개, US: {len(us_tickers)}개")

# 2. yfinance용 심볼 변환 (KR: .KS suffix)
yf_map = {}
for t in kr_tickers:
    yf_map[t + ".KS"] = t
for t in us_tickers:
    yf_map[t] = t

all_symbols = list(yf_map.keys())

# 3. 일별 OHLCV 다운로드
print(f"Downloading {len(all_symbols)} symbols from {START} to {END} ...")

# 개별 다운로드 (SSL 세션 적용)
records = []
price_cols = ["Open", "High", "Low", "Close", "Volume"]

failed = []
for sym in all_symbols:
    ticker_orig = yf_map[sym]
    try:
        t = yf.Ticker(sym, session=session)
        df_t = t.history(start=START, end=END, interval="1d", auto_adjust=True)
        if df_t.empty:
            print(f"  ⚠ 데이터 없음: {sym}")
            failed.append(sym)
            continue
        for dt, row_t in df_t.iterrows():
            row = {
                "date": dt.strftime("%Y-%m-%d"),
                "ticker": ticker_orig,
                "yf_symbol": sym,
                "market": "KR" if ticker_orig.isdigit() else "US",
                "open":   round(float(row_t["Open"]),   4) if not pd.isna(row_t["Open"])   else None,
                "high":   round(float(row_t["High"]),   4) if not pd.isna(row_t["High"])   else None,
                "low":    round(float(row_t["Low"]),    4) if not pd.isna(row_t["Low"])    else None,
                "close":  round(float(row_t["Close"]),  4) if not pd.isna(row_t["Close"])  else None,
                "volume": int(row_t["Volume"]) if not pd.isna(row_t["Volume"]) else None,
            }
            records.append(row)
        print(f"  ✓ {sym} ({ticker_orig}): {len(df_t)}일")
    except Exception as e:
        print(f"  ✗ {sym}: {e}")
        failed.append(sym)

if failed:
    print(f"\n실패 종목 ({len(failed)}개): {failed}")

df = pd.DataFrame(records)
if df.empty:
    print("❌ 데이터 없음")
    exit(1)

df = df.sort_values(["ticker", "date"]).reset_index(drop=True)
df.to_csv(OUTPUT_PATH, index=False)

print(f"\n✅ 저장 완료: {OUTPUT_PATH}")
print(f"   총 {len(df)}행, {df['ticker'].nunique()}개 종목")
print(f"   날짜 범위: {df['date'].min()} ~ {df['date'].max()}")
print(f"\n[샘플 - KR]")
print(df[df["market"]=="KR"].head(10).to_string(index=False))
print(f"\n[샘플 - US]")
print(df[df["market"]=="US"].head(10).to_string(index=False))
