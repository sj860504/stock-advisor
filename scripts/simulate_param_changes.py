"""
5월 거래 데이터로 파라미터 조정 효과 시뮬레이션
- 현재 파라미터 vs 3가지 조정안 비교
- OHLCV 일별 데이터로 매도 시점 재시뮬레이션
"""
import pandas as pd
import sqlite3
from datetime import datetime

DB_PATH = "data/stock_advisor.db"
OHLCV_PATH = "data/may_ohlcv_raw.csv"

# ── 5월 Regime: Neutral (regime_score 54~63) ───────────
# 현재 파라미터 (Neutral 기준)
CURRENT_PARAMS = {
    "take_profit_pct": 7.0,        # 목표 익절 +7%
    "stop_loss_pct":   -5.0,       # 손절 -5%
    "trailing_stop":   -5.0,       # 기본 trailing
    "tight_stop":      -1.0,       # +1.5% 도달 후 tight stop
    "tight_trigger":   1.5,        # tight stop 발동 임계
}

# 조정안 1: Tight Stop 완화 (트레일링 임계 상향)
PROPOSAL_1 = {
    "take_profit_pct": 7.0,
    "stop_loss_pct":   -5.0,
    "trailing_stop":   -5.0,
    "tight_stop":      -3.0,       # -1% → -3% (더 큰 변동 허용)
    "tight_trigger":   3.0,        # +1.5% → +3% (트리거 상향)
}

# 조정안 2: 손절 기준 완화 + 3일 룰
PROPOSAL_2 = {
    "take_profit_pct": 7.0,
    "stop_loss_pct":   -7.0,       # -5% → -7% (즉시 손절 완화)
    "trailing_stop":   -5.0,
    "tight_stop":      -1.0,
    "tight_trigger":   1.5,
    "stop_loss_days":  3,          # 3거래일 연속 -5% 이하 → 손절
}

# 조정안 3: 1+2 통합 (트레일링 완화 + 손절 완화)
PROPOSAL_3 = {
    "take_profit_pct": 10.0,       # +7% → +10% (수익 목표 상향)
    "stop_loss_pct":   -7.0,
    "trailing_stop":   -5.0,
    "tight_stop":      -3.0,
    "tight_trigger":   3.0,
    "stop_loss_days":  3,
}

# ── 데이터 로드 ──────────────────────────────────────────
ohlcv = pd.read_csv(OHLCV_PATH)
ohlcv["date"] = pd.to_datetime(ohlcv["date"]).dt.strftime("%Y-%m-%d")

conn = sqlite3.connect(DB_PATH)
trades = pd.read_sql("""
    SELECT id, ticker, order_type, quantity, price, buy_price_at_trade,
           strategy_name, status, timestamp
    FROM trade_history
    WHERE date(timestamp) >= '2026-05-01'
    ORDER BY timestamp
""", conn)
conn.close()

trades["trade_date"] = pd.to_datetime(trades["timestamp"]).dt.strftime("%Y-%m-%d")
trades["market"] = trades["ticker"].apply(lambda t: "KR" if t.isdigit() else "US")

# ── 매수→매도 페어링 ─────────────────────────────────────
# 각 매도 거래에 대해 매수가, 매도가, 매도일, 그 이후 가격 추이로 시뮬
sells = trades[(trades["order_type"]=="sell") &
               (trades["buy_price_at_trade"].notna()) &
               (trades["buy_price_at_trade"] > 0)].copy()

# ── 시뮬레이션 함수 ─────────────────────────────────────
def simulate_exit(ticker, buy_price, actual_sell_date, sell_qty, params):
    """
    실제 매수가 기준, params 룰로 매도 시점 재시뮬.
    매도 후 향후 OHLCV 추적 → take_profit / trailing / stop_loss 조건 충족 첫 날 매도.
    """
    future = ohlcv[(ohlcv["ticker"]==ticker) & (ohlcv["date"] >= actual_sell_date)].copy()
    future = future.sort_values("date").reset_index(drop=True)
    if future.empty:
        return None, None, "no_data"

    high_since_buy = buy_price
    consec_loss_days = 0

    for i, row in future.iterrows():
        day_high = row["high"]
        day_low  = row["low"]
        day_open = row["open"]
        day_close = row["close"]
        date_str = row["date"]

        # 일중 고가 갱신
        high_since_buy = max(high_since_buy, day_high)
        max_profit_pct = (high_since_buy - buy_price) / buy_price * 100

        # 1) Take Profit (당일 high가 목표 도달)
        tp_price = buy_price * (1 + params["take_profit_pct"]/100)
        if day_high >= tp_price:
            return tp_price, date_str, "take_profit"

        # 2) Tight Stop (max_profit이 tight_trigger 이상 도달했고, 당일 low가 tight_stop 이탈)
        if max_profit_pct >= params["tight_trigger"]:
            tight_threshold = high_since_buy * (1 + params["tight_stop"]/100)
            if day_low <= tight_threshold:
                return tight_threshold, date_str, "tight_stop"

        # 3) Trailing Stop (max_profit < tight_trigger인 경우)
        trail_threshold = high_since_buy * (1 + params["trailing_stop"]/100)
        if day_low <= trail_threshold and max_profit_pct < params["tight_trigger"]:
            return trail_threshold, date_str, "trailing_stop"

        # 4) Stop Loss (당일 종가 기준)
        close_pnl = (day_close - buy_price) / buy_price * 100
        if "stop_loss_days" in params:
            # 3일 연속 룰
            if close_pnl <= params["stop_loss_pct"]:
                consec_loss_days += 1
                if consec_loss_days >= params["stop_loss_days"]:
                    return day_close, date_str, f"stop_loss_{consec_loss_days}d"
            else:
                consec_loss_days = 0
        else:
            # 즉시 손절
            sl_price = buy_price * (1 + params["stop_loss_pct"]/100)
            if day_low <= sl_price:
                return sl_price, date_str, "stop_loss"

    # 5월 말까지 매도 신호 미발생 → 마지막 종가
    last_row = future.iloc[-1]
    return last_row["close"], last_row["date"], "month_end"


# ── 실행: 모든 매도건에 대해 4가지 시나리오 시뮬 ──────────
results = []
for _, sell in sells.iterrows():
    ticker = sell["ticker"]
    buy_price = sell["buy_price_at_trade"]
    actual_sell_date = sell["trade_date"]
    actual_sell_price = sell["price"]
    qty = sell["quantity"]

    actual_pnl = (actual_sell_price - buy_price) * qty
    actual_pct = (actual_sell_price - buy_price) / buy_price * 100

    row = {
        "ticker": ticker,
        "market": sell["market"],
        "buy_price": round(buy_price, 2),
        "qty": qty,
        "actual_sell": round(actual_sell_price, 2),
        "actual_pct": round(actual_pct, 2),
        "actual_pnl": round(actual_pnl, 0),
    }

    # 시뮬: actual_sell_date부터 다시 시작 (즉 그날 안 팔았다고 가정)
    for label, params in [("p1", PROPOSAL_1), ("p2", PROPOSAL_2), ("p3", PROPOSAL_3)]:
        sim_price, sim_date, sim_reason = simulate_exit(
            ticker, buy_price, actual_sell_date, qty, params
        )
        if sim_price is None:
            row[f"{label}_pct"] = None
            row[f"{label}_pnl"] = None
            row[f"{label}_reason"] = "no_data"
            continue
        sim_pct = (sim_price - buy_price) / buy_price * 100
        sim_pnl = (sim_price - buy_price) * qty
        row[f"{label}_pct"] = round(sim_pct, 2)
        row[f"{label}_pnl"] = round(sim_pnl, 0)
        row[f"{label}_reason"] = sim_reason
        row[f"{label}_date"] = sim_date

    results.append(row)

res_df = pd.DataFrame(results)

# ── 요약 ────────────────────────────────────────────────
print("=" * 90)
print("▶ 시나리오별 P&L 비교 (5월 매도 65건 기준)")
print("=" * 90)

def summarize(label, params):
    print(f"\n【{label}】")
    for k, v in params.items():
        print(f"   {k}: {v}")

summarize("현재 파라미터 (적용 전)", CURRENT_PARAMS)
summarize("조정안 1: Tight Stop 완화", PROPOSAL_1)
summarize("조정안 2: 손절 기준 + 3일룰", PROPOSAL_2)
summarize("조정안 3: 통합 (1+2+TP상향)", PROPOSAL_3)

# KR / US 분리
print("\n" + "=" * 90)
print("▶ 시장별 총 P&L 합계")
print("=" * 90)

for market in ["KR", "US"]:
    m_df = res_df[res_df["market"]==market]
    print(f"\n[{market}] 매도 {len(m_df)}건")
    print(f"  실제 (current):     {m_df['actual_pnl'].sum():>15,.0f}")
    print(f"  조정안 1 (Tight↑):  {m_df['p1_pnl'].sum():>15,.0f}  ({(m_df['p1_pnl'].sum()-m_df['actual_pnl'].sum())/abs(m_df['actual_pnl'].sum() or 1)*100:+.1f}%)")
    print(f"  조정안 2 (SL+3d):   {m_df['p2_pnl'].sum():>15,.0f}  ({(m_df['p2_pnl'].sum()-m_df['actual_pnl'].sum())/abs(m_df['actual_pnl'].sum() or 1)*100:+.1f}%)")
    print(f"  조정안 3 (통합):    {m_df['p3_pnl'].sum():>15,.0f}  ({(m_df['p3_pnl'].sum()-m_df['actual_pnl'].sum())/abs(m_df['actual_pnl'].sum() or 1)*100:+.1f}%)")

# 매도 사유 분포 (조정안 3 기준)
print("\n" + "=" * 90)
print("▶ 조정안별 매도 사유 분포")
print("=" * 90)
for label in ["p1", "p2", "p3"]:
    reasons = res_df[f"{label}_reason"].value_counts()
    print(f"\n[{label}]")
    print(reasons.to_string())

# 케이스별 상세
print("\n" + "=" * 90)
print("▶ Early Exit 케이스 (실제 +5% 미만 익절 종목) — 조정안 적용 시")
print("=" * 90)
early_exits = res_df[(res_df["actual_pct"] > 0) & (res_df["actual_pct"] < 5)].copy()
early_exits = early_exits.sort_values("actual_pnl", ascending=False)
print(early_exits[["ticker","actual_pct","p1_pct","p2_pct","p3_pct","p1_reason","p3_reason"]].head(20).to_string(index=False))

print("\n" + "=" * 90)
print("▶ 손절 케이스 (실제 음수) — 조정안 적용 시")
print("=" * 90)
losses = res_df[res_df["actual_pct"] < 0].copy()
losses = losses.sort_values("actual_pnl")
print(losses[["ticker","actual_pct","p1_pct","p2_pct","p3_pct","p2_reason","p3_reason"]].to_string(index=False))

# 전체 합계 정리표
print("\n" + "=" * 90)
print("▶ 최종 요약 (KRW + USD*1300 환산 단일 통화 기준 추정)")
print("=" * 90)
EXR = 1350  # 환율 가정
res_df["actual_krw"] = res_df.apply(lambda r: r["actual_pnl"] * (EXR if r["market"]=="US" else 1), axis=1)
res_df["p1_krw"]     = res_df.apply(lambda r: (r["p1_pnl"] or 0) * (EXR if r["market"]=="US" else 1), axis=1)
res_df["p2_krw"]     = res_df.apply(lambda r: (r["p2_pnl"] or 0) * (EXR if r["market"]=="US" else 1), axis=1)
res_df["p3_krw"]     = res_df.apply(lambda r: (r["p3_pnl"] or 0) * (EXR if r["market"]=="US" else 1), axis=1)

base = res_df["actual_krw"].sum()
print(f"  실제 (현재 파라미터):       {base:>15,.0f} 원")
print(f"  조정안 1 (Tight Stop 완화):  {res_df['p1_krw'].sum():>15,.0f} 원  (Δ {res_df['p1_krw'].sum()-base:+,.0f})")
print(f"  조정안 2 (손절 + 3일룰):     {res_df['p2_krw'].sum():>15,.0f} 원  (Δ {res_df['p2_krw'].sum()-base:+,.0f})")
print(f"  조정안 3 (통합):             {res_df['p3_krw'].sum():>15,.0f} 원  (Δ {res_df['p3_krw'].sum()-base:+,.0f})")

# CSV 저장
res_df.to_csv("data/may_simulation_result.csv", index=False)
print(f"\n✅ 상세 결과: data/may_simulation_result.csv")
