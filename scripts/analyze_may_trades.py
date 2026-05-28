"""
5월 거래 알고리즘 분석 — OHLCV + trade_history 조합
목표: 파라미터 조정 방안 도출
"""
import pandas as pd
import sqlite3

DB_PATH = "data/stock_advisor.db"
OHLCV_PATH = "data/may_ohlcv_raw.csv"

# ── 데이터 로드 ──────────────────────────────────────────
ohlcv = pd.read_csv(OHLCV_PATH)
ohlcv["date"] = pd.to_datetime(ohlcv["date"])

conn = sqlite3.connect(DB_PATH)
trades = pd.read_sql("""
    SELECT id, ticker, order_type, quantity, price, buy_price_at_trade,
           strategy_name, status, timestamp
    FROM trade_history
    WHERE date(timestamp) >= '2026-05-01'
    ORDER BY timestamp
""", conn)
conn.close()

trades["timestamp"] = pd.to_datetime(trades["timestamp"])
trades["trade_date"] = trades["timestamp"].dt.date.astype(str)
trades["market"] = trades["ticker"].apply(lambda t: "KR" if t.isdigit() else "US")

# ── 1. 실제 거래 vs 당일 OHLC 비교 ──────────────────────────
merged = trades.merge(
    ohlcv[["date","ticker","open","high","low","close"]],
    left_on=["trade_date","ticker"],
    right_on=[ohlcv["date"].astype(str),"ticker"],
    how="left"
)

# 매수: 실제 체결가 vs 당일 저가(최적 매수), 시가
# 매도: 실제 체결가 vs 당일 고가(최적 매도)
buy_trades  = merged[merged["order_type"]=="buy"].copy()
sell_trades = merged[merged["order_type"]=="sell"].copy()

buy_trades["slippage_vs_low"]   = ((buy_trades["price"] - buy_trades["low"]) / buy_trades["low"] * 100).round(2)
buy_trades["slippage_vs_open"]  = ((buy_trades["price"] - buy_trades["open"]) / buy_trades["open"] * 100).round(2)

sell_trades["slippage_vs_high"] = ((sell_trades["high"] - sell_trades["price"]) / sell_trades["high"] * 100).round(2)
sell_trades["slippage_vs_open"] = ((sell_trades["price"] - sell_trades["open"]) / sell_trades["open"] * 100).round(2)

print("=" * 70)
print("▶ 1. 매수 슬리피지 분석 (실제체결 vs 당일 저가/시가)")
print("=" * 70)
print(f"  매수 건수: {len(buy_trades)}건")
print(f"  실제가 vs 당일 저가 차이 (%)  — 평균: {buy_trades['slippage_vs_low'].mean():.2f}%  중앙값: {buy_trades['slippage_vs_low'].median():.2f}%")
print(f"  실제가 vs 당일 시가 차이 (%)  — 평균: {buy_trades['slippage_vs_open'].mean():.2f}%  중앙값: {buy_trades['slippage_vs_open'].median():.2f}%")
print(f"  → 저가 대비 평균 {buy_trades['slippage_vs_low'].mean():.2f}% 비싸게 매수")

print()
print("=" * 70)
print("▶ 2. 매도 슬리피지 분석 (당일 고가 vs 실제체결)")
print("=" * 70)
print(f"  매도 건수: {len(sell_trades)}건")
print(f"  당일 고가 vs 실제가 차이 (%) — 평균: {sell_trades['slippage_vs_high'].mean():.2f}%  중앙값: {sell_trades['slippage_vs_high'].median():.2f}%")
print(f"  → 고가 대비 평균 {sell_trades['slippage_vs_high'].mean():.2f}% 싸게 매도")

# ── 2. 손실 매도 종목: "더 기다렸으면?" 분석 ─────────────────
print()
print("=" * 70)
print("▶ 3. 손실 매도 종목 — 매도 후 반등 여부")
print("=" * 70)

loss_sells = sell_trades[
    (sell_trades["buy_price_at_trade"].notna()) &
    (sell_trades["buy_price_at_trade"] > 0) &
    (sell_trades["price"] < sell_trades["buy_price_at_trade"])
].copy()
loss_sells["pnl_pct"] = ((loss_sells["price"] - loss_sells["buy_price_at_trade"]) / loss_sells["buy_price_at_trade"] * 100).round(2)

# 매도일 이후 최고가 조회
recovery = []
for _, row in loss_sells.iterrows():
    future = ohlcv[(ohlcv["ticker"] == row["ticker"]) & (ohlcv["date"].astype(str) > row["trade_date"])]
    if future.empty:
        max_future_high = None
        best_day = None
    else:
        idx = future["high"].idxmax()
        max_future_high = future.loc[idx, "high"]
        best_day = future.loc[idx, "date"]
    recovery.append({
        "ticker": row["ticker"],
        "market": row["market"],
        "sell_date": row["trade_date"],
        "sell_price": round(row["price"], 1),
        "buy_price": round(row["buy_price_at_trade"], 1),
        "pnl_pct": row["pnl_pct"],
        "future_high": round(max_future_high, 1) if max_future_high else None,
        "could_recover": (max_future_high >= row["buy_price_at_trade"]) if max_future_high else False,
        "best_day": str(best_day)[:10] if best_day else None,
    })

rec_df = pd.DataFrame(recovery).drop_duplicates("ticker")
could_recover = rec_df[rec_df["could_recover"]==True]
print(f"  손실 매도 종목: {len(rec_df)}개")
print(f"  이후 매수가 회복 가능 종목: {len(could_recover)}개 ({len(could_recover)/len(rec_df)*100:.0f}%)")
print()
print(rec_df.to_string(index=False))

# ── 3. 수익 매도 종목: "너무 일찍 팔았나?" ────────────────────
print()
print("=" * 70)
print("▶ 4. 수익 매도 종목 — 더 오른 경우 (early exit)")
print("=" * 70)

win_sells = sell_trades[
    (sell_trades["buy_price_at_trade"].notna()) &
    (sell_trades["buy_price_at_trade"] > 0) &
    (sell_trades["price"] >= sell_trades["buy_price_at_trade"])
].copy()

early_exit = []
for _, row in win_sells.iterrows():
    future = ohlcv[(ohlcv["ticker"] == row["ticker"]) & (ohlcv["date"].astype(str) > row["trade_date"])]
    if future.empty:
        continue
    idx = future["high"].idxmax()
    max_future_high = future.loc[idx, "high"]
    best_day = future.loc[idx, "date"]
    missed_gain = ((max_future_high - row["price"]) / row["price"] * 100)
    actual_gain = ((row["price"] - row["buy_price_at_trade"]) / row["buy_price_at_trade"] * 100)
    early_exit.append({
        "ticker": row["ticker"],
        "sell_date": row["trade_date"],
        "actual_gain%": round(actual_gain, 2),
        "max_future_high": round(max_future_high, 1),
        "missed_upside%": round(missed_gain, 2),
        "best_day": str(best_day)[:10],
    })

ee_df = pd.DataFrame(early_exit).drop_duplicates("ticker")
ee_df = ee_df.sort_values("missed_upside%", ascending=False)
significant = ee_df[ee_df["missed_upside%"] > 3]
print(f"  수익 매도 후 3%+ 추가 상승 종목: {len(significant)}개")
print()
print(ee_df.head(20).to_string(index=False))

# ── 4. 매수 타이밍 분석: 당일 어느 시점에 매수? ─────────────
print()
print("=" * 70)
print("▶ 5. 시장별 매수 타이밍 (시간대)")
print("=" * 70)
buy_trades["hour"] = pd.to_datetime(buy_trades["timestamp"]).dt.hour
print("KR 매수 시간대:")
print(buy_trades[buy_trades["market"]=="KR"]["hour"].value_counts().sort_index().to_string())
print("\nUS 매수 시간대:")
print(buy_trades[buy_trades["market"]=="US"]["hour"].value_counts().sort_index().to_string())

# ── 5. 종목별 최종 P&L 요약 ──────────────────────────────────
print()
print("=" * 70)
print("▶ 6. 종목별 실현 P&L 요약 (매도 기준)")
print("=" * 70)
pnl_by_ticker = sell_trades[sell_trades["buy_price_at_trade"] > 0].groupby(["ticker","market"]).apply(
    lambda g: pd.Series({
        "total_qty": g["quantity"].sum(),
        "avg_sell": round(g["price"].mean(), 2),
        "avg_buy": round(g["buy_price_at_trade"].mean(), 2),
        "realized_pnl": round(((g["price"] - g["buy_price_at_trade"]) * g["quantity"]).sum(), 0),
        "pnl_pct": round(((g["price"] - g["buy_price_at_trade"]) / g["buy_price_at_trade"] * 100).mean(), 2),
        "trade_cnt": len(g),
    })
).reset_index()
pnl_by_ticker = pnl_by_ticker.sort_values("realized_pnl", ascending=False)
print(pnl_by_ticker.to_string(index=False))

total_pnl = pnl_by_ticker["realized_pnl"].sum()
kr_pnl = pnl_by_ticker[pnl_by_ticker["market"]=="KR"]["realized_pnl"].sum()
us_pnl = pnl_by_ticker[pnl_by_ticker["market"]=="US"]["realized_pnl"].sum()
win_rate = (pnl_by_ticker["realized_pnl"] > 0).sum() / len(pnl_by_ticker) * 100
print(f"\n  KR 실현 P&L: {kr_pnl:,.0f}원")
print(f"  US 실현 P&L: {us_pnl:,.0f}달러 (USD)")
print(f"  종목 승률: {win_rate:.1f}%")
print(f"  전체 거래 승률: {((sell_trades['price'] > sell_trades['buy_price_at_trade']).sum() / sell_trades['buy_price_at_trade'].notna().sum() * 100):.1f}%")
