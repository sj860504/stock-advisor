"""2020년 3월 코로나 폭락 백테스트.

기간: 2020-02-01 ~ 2020-05-31 (V자 회복 완료까지)
KOSPI: 2,200 → 1,439 (-34%, 3/19 저점) → 1,975 (5/29) → V자 회복

가정:
- 현재 운영 보유 종목 15개를 2020-02-01에 매수 (가상)
- 매수가는 그 시점의 종가
- 초기 현금: 매수 비용의 25% (총자본의 20% 현금)
- 동일 우상향 DCA 알고리즘 적용
"""
import ssl
ssl._create_default_https_context = ssl._create_unverified_context

import sqlite3
import yfinance as yf
import pandas as pd
import requests, urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
session = requests.Session(); session.verify = False

DB = "data/stock_advisor.db"
START = "2020-02-01"; END = "2020-06-01"

# 1) 보유 종목 (현재 운영 보유)
conn = sqlite3.connect(DB)
holdings = pd.read_sql("""
    SELECT ticker, name, quantity
    FROM portfolio_holdings
    WHERE portfolio_id = (SELECT id FROM portfolios WHERE user_id='sean')
""", conn)
conn.close()
print(f"보유 {len(holdings)}종목 (2020 가상 매수)")

# 2) 2020년 OHLCV
ohlcv = {}
for _, row in holdings.iterrows():
    sym = row["ticker"] + ".KS"
    try:
        df = yf.Ticker(sym, session=session).history(start=START, end=END, interval="1d", auto_adjust=True)
        if not df.empty:
            ohlcv[row["ticker"]] = df
    except Exception:
        pass

# 종목 수 확인 — 2020년에 상장 안 됐던 것은 제외
print(f"  2020 데이터 있는 종목: {len(ohlcv)}/{len(holdings)}")
missing = [t for t in holdings["ticker"] if t not in ohlcv]
if missing:
    print(f"  데이터 없음: {missing}")

# 매수가 = 2020-02-03 종가 (월요일)
buy_prices = {}
for t in ohlcv:
    df = ohlcv[t]
    first_row = df.iloc[0]
    buy_prices[t] = float(first_row["Close"])

# 초기 평가액 = 매수가 × 수량
holdings_2020 = holdings[holdings["ticker"].isin(ohlcv.keys())].copy()
holdings_2020["buy_price"] = holdings_2020["ticker"].map(buy_prices)
total_invested = (holdings_2020["buy_price"] * holdings_2020["quantity"]).sum()
init_cash = total_invested * 0.25  # 자본의 20% (=총자산 25/125 = 20%) 현금
total_capital = total_invested + init_cash
print(f"  총 매수금액: {total_invested:,.0f}원, 초기 현금: {init_cash:,.0f}원, 총자본: {total_capital:,.0f}원")

# 3) KOSPI 일별 1d / 5d
kospi = yf.Ticker("^KS11", session=session).history(start="2020-01-15", end=END, interval="1d", auto_adjust=True)
kospi_daily = {}
closes = list(kospi["Close"])
dates = [d.strftime("%Y-%m-%d") for d in kospi.index]
for i, d in enumerate(dates):
    if d < START:
        continue
    chg_1d = (closes[i] - closes[i-1])/closes[i-1]*100 if i > 0 else 0
    chg_5d = (closes[i] - closes[i-5])/closes[i-5]*100 if i >= 5 else 0
    kospi_daily[d] = (chg_1d, chg_5d, closes[i])

# VIX 일별 (KS11 변동성 대용으로 VIX 사용)
vix_data = yf.Ticker("^VIX", session=session).history(start="2020-01-15", end=END, interval="1d", auto_adjust=True)
vix_daily = {d.strftime("%Y-%m-%d"): float(r["Close"]) for d, r in vix_data.iterrows()}

print(f"  KOSPI 2020-02-03: {kospi_daily.get('2020-02-03', (0,0,0))[2]:.0f}")
print(f"  KOSPI 2020-03-19 저점 부근: {kospi_daily.get('2020-03-19', (0,0,0))[2]:.0f}")
print(f"  KOSPI 2020-05-29 회복: {kospi_daily.get('2020-05-29', (0,0,0))[2]:.0f}")


def kospi_mult(d):
    if d not in kospi_daily:
        return 1.0
    _, chg_5d, _ = kospi_daily[d]
    if chg_5d < -18: return 2.5
    if chg_5d < -12: return 2.0
    if chg_5d < -7:  return 1.5
    return 1.0

def is_crash(d):
    """매도/손절 보류 — 강화 임계 (사용자 추천)."""
    if d not in kospi_daily:
        return False
    chg_1d, chg_5d, _ = kospi_daily[d]
    vix = vix_daily.get(d, 20)
    return chg_1d < -5 or chg_5d < -10 or vix > 35

def is_frozen(d):
    return vix_daily.get(d, 20) > 50


def simulate(name, params):
    p = params
    cash = init_cash
    positions = {}
    for _, h in holdings_2020.iterrows():
        positions[h["ticker"]] = {
            "name": h["name"], "qty": int(h["quantity"]),
            "init_qty": int(h["quantity"]),
            "buy_price": h["buy_price"], "high_since_buy": h["buy_price"],
            "partial_taken": False, "remaining_high": h["buy_price"],
            "dca_done": {-3: False, -8: False, -15: False},
            "consec_loss_days": 0, "sold": False,
        }
    actions = []
    trading_days = sorted([d for d in kospi_daily if START <= d < END])

    for d in trading_days:
        crash = is_crash(d) if p.get("crash_guard") else False
        frozen = is_frozen(d) if p.get("frozen_guard") else False
        mult = kospi_mult(d) if p.get("kospi_bonus") else 1.0

        for ticker, pos in positions.items():
            if pos["qty"] == 0 or ticker not in ohlcv:
                continue
            day = ohlcv[ticker][ohlcv[ticker].index.strftime("%Y-%m-%d") == d]
            if day.empty:
                continue
            row = day.iloc[0]
            day_high = float(row["High"]); day_low = float(row["Low"]); day_close = float(row["Close"])
            buy = pos["buy_price"]
            pos["high_since_buy"] = max(pos["high_since_buy"], day_high)
            close_pnl = (day_close - buy) / buy * 100
            max_profit_pct = (pos["high_since_buy"] - buy) / buy * 100

            # 1) 부분 익절 +10% 50%
            if p.get("partial_take") and not pos["partial_taken"]:
                tp = buy * (1 + p["take_profit"] / 100)
                if day_high >= tp:
                    half = pos["qty"] // 2 if pos["qty"] > 1 else (1 if pos["qty"] == 1 else 0)
                    if half > 0 and not crash:
                        pnl = (tp - buy) * half
                        cash += tp * half
                        actions.append((d, ticker, "SELL50", half, tp, "partial_take", pnl))
                        pos["qty"] -= half
                        pos["partial_taken"] = True
                        pos["remaining_high"] = day_high
                        if pos["qty"] == 0:
                            pos["sold"] = True
                            continue

            # 2) 잔여 trailing -8%
            if p.get("partial_take") and pos["partial_taken"] and pos["qty"] > 0:
                pos["remaining_high"] = max(pos["remaining_high"], day_high)
                trail_th = pos["remaining_high"] * (1 + p["trailing_remaining"] / 100)
                if day_low <= trail_th and not crash:
                    pnl = (trail_th - buy) * pos["qty"]
                    cash += trail_th * pos["qty"]
                    actions.append((d, ticker, "SELL", pos["qty"], trail_th, "trailing_remaining", pnl))
                    pos["qty"] = 0; pos["sold"] = True
                    continue

            # 3) Normal 손실 trailing (Normal Mode only)
            if p.get("normal_trailing") and not pos["partial_taken"]:
                trail_th = pos["high_since_buy"] * (1 + p.get("trailing", -5) / 100)
                if day_low <= trail_th and not crash:
                    pnl = (trail_th - buy) * pos["qty"]
                    cash += trail_th * pos["qty"]
                    actions.append((d, ticker, "SELL", pos["qty"], trail_th, "trailing_loss", pnl))
                    pos["qty"] = 0; pos["sold"] = True
                    continue

            # 4) 손절 — Crash 중에는 보류 (사용자 추천 B)
            if p.get("stop_loss"):
                if close_pnl <= p["stop_loss"]:
                    pos["consec_loss_days"] += 1
                    if pos["consec_loss_days"] >= p["stop_loss_days"] and not crash:
                        pnl = (day_close - buy) * pos["qty"]
                        cash += day_close * pos["qty"]
                        actions.append((d, ticker, "SELL", pos["qty"], day_close,
                                        f"stop_loss_{pos['consec_loss_days']}d", pnl))
                        pos["qty"] = 0; pos["sold"] = True
                        continue
                else:
                    pos["consec_loss_days"] = 0

            # 5) DCA
            if p.get("dca") and not frozen:
                total_assets = cash + sum(p2["qty"] * day_close for t2, p2 in positions.items()
                                          if not p2["sold"] and t2 in ohlcv)
                min_cash = total_assets * p.get("min_cash_ratio", 0.0)
                max_pos = total_assets * p.get("max_position_pct", 0.15)
                for stage in (-3, -8, -15):
                    if pos["dca_done"][stage]:
                        continue
                    if close_pnl <= stage:
                        ratio = p["dca_ratios"][stage] / 100 * mult
                        budget = total_assets * ratio
                        cur_pos_val = pos["qty"] * day_close
                        if cur_pos_val + budget > max_pos:
                            budget = max(0, max_pos - cur_pos_val)
                        if cash - budget < min_cash:
                            budget = max(0, cash - min_cash)
                        add_qty = int(budget / day_close)
                        if add_qty <= 0:
                            continue
                        spent = add_qty * day_close
                        new_qty = pos["qty"] + add_qty
                        pos["buy_price"] = (pos["qty"] * pos["buy_price"] + spent) / new_qty
                        pos["qty"] = new_qty
                        cash -= spent
                        pos["dca_done"][stage] = True
                        actions.append((d, ticker, "BUY+", add_qty, day_close,
                                        f"dca_{stage}pct_mult{mult:.1f}", 0))

    # 최종
    realized = sum(a[6] for a in actions if a[2] in ("SELL", "SELL50"))
    unrealized = 0
    last_prices = {}
    for t in positions:
        if t in ohlcv and not ohlcv[t].empty:
            last_prices[t] = float(ohlcv[t]["Close"].iloc[-1])
    for ticker, pos in positions.items():
        if pos["qty"] > 0 and last_prices.get(ticker):
            unrealized += (last_prices[ticker] - pos["buy_price"]) * pos["qty"]
    return {"name": name, "realized": realized, "unrealized": unrealized,
            "total": realized + unrealized, "cash_end": cash,
            "actions": actions, "positions": positions,
            "last_prices": last_prices, "total_invested": total_invested}


PARAMS_NORMAL = {
    "partial_take": False,
    "normal_trailing": True, "trailing": -5,
    "take_profit": 7,
    "dca": False, "crash_guard": False, "frozen_guard": False, "kospi_bonus": False,
}

PARAMS_UPTREND = {
    # 부분익절: +15% (was 10%) → V자 회복 후반 캡처
    "partial_take": True, "take_profit": 15, "trailing_remaining": -10,
    "normal_trailing": False,
    # 손절: -25% × 7일 연속 (was -20% × 5) + Crash 중엔 보류 (B)
    "stop_loss": -25, "stop_loss_days": 7,
    "dca": True,
    "dca_ratios": {-3: 3, -8: 4, -15: 5},
    # Crash 강화: 5d<-10% / 1d<-5% / VIX>35 (was -8/-3/30)
    "crash_guard": True, "frozen_guard": True, "kospi_bonus": True,
    "min_cash_ratio": 0.0,
    "max_position_pct": 0.15,
}


res_n = simulate("Normal", PARAMS_NORMAL)
res_u = simulate("Uptrend DCA", PARAMS_UPTREND)

def fmt(v): return f"{v:>+18,.0f}원"

print("\n" + "=" * 80)
print(f"{'항목':<28} {'Normal (현재)':>22} {'Uptrend DCA':>22}")
print("-" * 80)
sells_n = sum(1 for a in res_n["actions"] if a[2] in ("SELL", "SELL50"))
sells_u = sum(1 for a in res_u["actions"] if a[2] in ("SELL", "SELL50"))
buys_u = sum(1 for a in res_u["actions"] if a[2] == "BUY+")
print(f"{'매도 (부분익절 포함)':<28} {sells_n:>22}건 {sells_u:>20}건")
print(f"{'DCA 추매':<28} {0:>22}건 {buys_u:>20}건")
print(f"{'실현 P&L':<28} {fmt(res_n['realized']):>22} {fmt(res_u['realized']):>22}")
print(f"{'미실현 P&L (5/29 종가)':<28} {fmt(res_n['unrealized']):>22} {fmt(res_u['unrealized']):>22}")
print(f"{'총 P&L':<28} {fmt(res_n['total']):>22} {fmt(res_u['total']):>22}")
print(f"{'기말 현금':<28} {fmt(res_n['cash_end']):>22} {fmt(res_u['cash_end']):>22}")
print(f"{'(참고) 초기 투자':<28} {fmt(res_n['total_invested']):>22} {fmt(res_u['total_invested']):>22}")

# 수익률 (총자본 대비)
total_capital = res_n["total_invested"] + init_cash
ret_n = res_n["total"] / total_capital * 100
ret_u = res_u["total"] / total_capital * 100
print(f"{'수익률 (vs 총자본)':<28} {ret_n:>21.2f}%   {ret_u:>20.2f}%")

# 회복 시나리오: 5/29 종가 → +N%
print("\n" + "=" * 80)
print("추가 회복 시나리오 (5/29 종가 → +N% 가정)")
print("-" * 80)
for pct in (0, 5, 10, 20):
    up_n = sum(res_n["last_prices"].get(t, 0) * pct/100 * pos["qty"]
               for t, pos in res_n["positions"].items() if pos["qty"] > 0)
    up_u = sum(res_u["last_prices"].get(t, 0) * pct/100 * pos["qty"]
               for t, pos in res_u["positions"].items() if pos["qty"] > 0)
    tn = res_n["total"] + up_n
    tu = res_u["total"] + up_u
    rn = tn/total_capital*100
    ru = tu/total_capital*100
    print(f"  +{pct:2d}%: Normal {fmt(tn)} ({rn:+.1f}%) | Uptrend {fmt(tu)} ({ru:+.1f}%) | 차이 {fmt(tu-tn)}")

# 최저점(3월 19일) 시점 Uptrend 평가
print("\n" + "=" * 80)
print("코로나 저점 (2020-03-19) 시점 평가")
print("-" * 80)
march19 = "2020-03-19"
mar_n = sum((ohlcv[t][ohlcv[t].index.strftime("%Y-%m-%d") == march19]["Close"].iloc[0]
             if not ohlcv[t][ohlcv[t].index.strftime("%Y-%m-%d") == march19].empty else 0)
            * pos["qty"]
            for t, pos in res_n["positions"].items() if pos["qty"] > 0)
mar_u = sum((ohlcv[t][ohlcv[t].index.strftime("%Y-%m-%d") == march19]["Close"].iloc[0]
             if not ohlcv[t][ohlcv[t].index.strftime("%Y-%m-%d") == march19].empty else 0)
            * pos["qty"]
            for t, pos in res_u["positions"].items() if pos["qty"] > 0)
print(f"  보유주식 평가액:    Normal {fmt(mar_n)}  |  Uptrend {fmt(mar_u)}")

# 액션 집계
print("\n[Uptrend 액션 집계]")
from collections import Counter
reasons = Counter(a[5] for a in res_u["actions"])
for r, c in sorted(reasons.items(), key=lambda x: -x[1]):
    print(f"  {r}: {c}건")
