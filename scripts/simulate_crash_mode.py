"""6/1~6/8 Crash Mode vs Normal Mode 시뮬레이션.

가정:
- 매수가 = 보유 종목의 portfolio_holdings.buy_price (5/28 시점)
- 6/2~6/8 일별 OHLCV 로 매도 시뮬
- Normal: 현재 알고리즘 (trailing_stop -5%, tight_stop -3% @ +3%, stop_loss -7%, take_profit +7%)
- Crash (사용자 선택 A/C/A/A/A):
    Auto on (조건 충족 시 자동), Strong=per-trade 15%, Frozen=VIX>50 시 보류,
    종목 max 10%, panic_lock 해제
    Strong 진입 시: stop_loss -15%, trailing -12%, tight 비활성, take_profit +15%
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
START = "2026-06-01"; END = "2026-06-09"

# 1) 보유 종목 + 매수가 (5/28 진입가)
conn = sqlite3.connect(DB)
holdings = pd.read_sql("""
    SELECT ticker, name, quantity, buy_price
    FROM portfolio_holdings
    WHERE portfolio_id = (SELECT id FROM portfolios WHERE user_id='sean')
""", conn)
cash = pd.read_sql("SELECT cash_balance FROM portfolios WHERE user_id='sean'", conn).iloc[0,0]
conn.close()
print(f"보유 {len(holdings)}종목, KRW 현금 {cash:,.0f}원")

# 2) yfinance 6/1~6/8 일별 OHLCV
ohlcv = {}
for _, row in holdings.iterrows():
    sym = row["ticker"] + ".KS"
    try:
        t = yf.Ticker(sym, session=session)
        df = t.history(start=START, end=END, interval="1d", auto_adjust=True)
        if df.empty:
            print(f"  ✗ {sym}: 데이터 없음")
            continue
        ohlcv[row["ticker"]] = df
    except Exception as e:
        print(f"  ✗ {sym}: {e}")

# KOSPI 지수 (1d/5d 변화율 — crash detection용)
try:
    kospi = yf.Ticker("^KS11", session=session).history(start="2026-05-22", end=END, interval="1d", auto_adjust=True)
    print(f"\nKOSPI 6/1~6/8 일별 변화율:")
    for d, r in kospi.iterrows():
        d_str = d.strftime("%Y-%m-%d")
        if d_str < START:
            continue
        prev = kospi[kospi.index < d]["Close"].iloc[-1] if len(kospi[kospi.index < d]) else r["Close"]
        chg_1d = (r["Close"] - prev) / prev * 100
        prev5 = kospi[kospi.index < d]["Close"].iloc[-5] if len(kospi[kospi.index < d]) >= 5 else None
        chg_5d = ((r["Close"] - prev5) / prev5 * 100) if prev5 else None
        print(f"  {d_str}: close={r['Close']:.0f}  1d={chg_1d:+.2f}%  5d={chg_5d:+.2f}%" if chg_5d else f"  {d_str}: 1d={chg_1d:+.2f}%")
except Exception as e:
    print(f"KOSPI: {e}")

# 3) Mode 별 파라미터
PARAMS = {
    "normal": {  # 현재 알고리즘 (Neutral regime)
        "stop_loss": -7, "trailing": -5, "tight_trig": 3, "tight_stop": -3,
        "take_profit": 7, "consec_days": 3, "buy_threshold": 30, "per_trade": 0.05,
        "min_cash_ratio": 0.0,
    },
    "crash_strong": {  # Strong crash 모드
        "stop_loss": -15, "trailing": -12, "tight_trig": 999, "tight_stop": -999,  # tight 비활성
        "take_profit": 15, "consec_days": 7, "buy_threshold": 50, "per_trade": 0.15,
        "min_cash_ratio": 0.05,
    },
}


def simulate(mode, holdings, ohlcv, cash):
    """모드별 매매 시뮬레이션. 일별 OHLCV 순회하며 매도/추매 결정."""
    p = PARAMS[mode]
    positions = {}
    for _, h in holdings.iterrows():
        positions[h["ticker"]] = {
            "name": h["name"], "qty": h["quantity"], "buy_price": h["buy_price"],
            "high_since_buy": h["buy_price"], "consec_loss_days": 0, "sold": False,
        }
    cash_left = cash
    daily_pnl = []
    actions = []  # log of (date, ticker, action, qty, price, reason, pnl)

    # 거래일 집합 (KR 거래일)
    all_dates = set()
    for df in ohlcv.values():
        for d in df.index:
            all_dates.add(d.strftime("%Y-%m-%d"))
    trading_days = sorted([d for d in all_dates if START <= d < END])

    for date_str in trading_days:
        for ticker, pos in positions.items():
            if pos["sold"] or pos["qty"] == 0:
                continue
            if ticker not in ohlcv:
                continue
            df = ohlcv[ticker]
            day = df[df.index.strftime("%Y-%m-%d") == date_str]
            if day.empty:
                continue
            row = day.iloc[0]
            day_high = float(row["High"]); day_low = float(row["Low"]); day_close = float(row["Close"])
            buy = pos["buy_price"]

            pos["high_since_buy"] = max(pos["high_since_buy"], day_high)
            max_profit_pct = (pos["high_since_buy"] - buy) / buy * 100
            close_pnl = (day_close - buy) / buy * 100

            # 1) Take profit
            tp = buy * (1 + p["take_profit"] / 100)
            if day_high >= tp:
                sold_price = tp
                pnl = (sold_price - buy) * pos["qty"]
                cash_left += sold_price * pos["qty"]
                actions.append((date_str, ticker, "SELL", pos["qty"], sold_price, "take_profit", pnl))
                pos["sold"] = True; pos["qty"] = 0
                continue

            # 2) Tight stop (mode가 활성화한 경우만)
            if max_profit_pct >= p["tight_trig"]:
                tight_th = pos["high_since_buy"] * (1 + p["tight_stop"] / 100)
                if day_low <= tight_th:
                    pnl = (tight_th - buy) * pos["qty"]
                    cash_left += tight_th * pos["qty"]
                    actions.append((date_str, ticker, "SELL", pos["qty"], tight_th, "tight_stop", pnl))
                    pos["sold"] = True; pos["qty"] = 0
                    continue

            # 3) Trailing stop
            trail_th = pos["high_since_buy"] * (1 + p["trailing"] / 100)
            if day_low <= trail_th and max_profit_pct < p["tight_trig"]:
                pnl = (trail_th - buy) * pos["qty"]
                cash_left += trail_th * pos["qty"]
                actions.append((date_str, ticker, "SELL", pos["qty"], trail_th, "trailing_stop", pnl))
                pos["sold"] = True; pos["qty"] = 0
                continue

            # 4) Stop loss (3일 연속 룰)
            sl_th = buy * (1 + p["stop_loss"] / 100)
            if close_pnl <= p["stop_loss"]:
                pos["consec_loss_days"] += 1
                if pos["consec_loss_days"] >= p["consec_days"]:
                    pnl = (day_close - buy) * pos["qty"]
                    cash_left += day_close * pos["qty"]
                    actions.append((date_str, ticker, "SELL", pos["qty"], day_close,
                                    f"stop_loss_{pos['consec_loss_days']}d", pnl))
                    pos["sold"] = True; pos["qty"] = 0
                    continue
            else:
                pos["consec_loss_days"] = 0

            # 5) Crash mode 추매 (mode가 crash이면, profit_pct가 add 임계 이하면)
            # Crash Strong: profit_pct < -2% 시 add (per_trade=15%)
            if mode == "crash_strong" and close_pnl <= -2.0:
                # cash 최소 5% 유지하면서 per_trade 15% 의 자본 사용
                total_assets = cash_left + sum(p2["qty"] * day_close for t2, p2 in positions.items() if not p2["sold"])
                add_budget = total_assets * p["per_trade"]
                min_cash = total_assets * p["min_cash_ratio"]
                if cash_left - add_budget < min_cash:
                    continue
                # 종목 max 10% 가드
                cur_val = pos["qty"] * day_close
                max_per_ticker = total_assets * 0.10
                if cur_val >= max_per_ticker:
                    continue
                add_qty = max(1, int(add_budget / day_close))
                if add_qty * day_close > cash_left:
                    continue
                # 평단가 갱신
                new_qty = pos["qty"] + add_qty
                pos["buy_price"] = (pos["qty"] * pos["buy_price"] + add_qty * day_close) / new_qty
                pos["qty"] = new_qty
                pos["high_since_buy"] = max(pos["high_since_buy"], day_high)
                pos["consec_loss_days"] = 0  # 평단 낮춰서 카운터 리셋
                cash_left -= add_qty * day_close
                actions.append((date_str, ticker, "BUY+", add_qty, day_close, "crash_add", 0))

    # 최종 잔액 + 미실현 손익 (마지막 종가 기준)
    realized = sum(a[6] for a in actions if a[2] == "SELL")
    unrealized = 0
    last_prices = {t: ohlcv[t]["Close"].iloc[-1] if t in ohlcv and not ohlcv[t].empty else 0
                   for t in positions}
    for ticker, pos in positions.items():
        if pos["qty"] > 0 and last_prices.get(ticker):
            unrealized += (last_prices[ticker] - pos["buy_price"]) * pos["qty"]
    return {
        "mode": mode,
        "realized_pnl": realized,
        "unrealized_pnl": unrealized,
        "total_pnl": realized + unrealized,
        "cash_end": cash_left,
        "actions": actions,
        "positions": positions,
    }


print("\n" + "=" * 70)
res_n = simulate("normal", holdings, ohlcv, cash)
res_c = simulate("crash_strong", holdings, ohlcv, cash)

def fmt_money(v):
    return f"{v:>+15,.0f}원"

print("\n【Normal Mode — 현재 알고리즘】")
print(f"  매도 횟수      : {sum(1 for a in res_n['actions'] if a[2]=='SELL')}건")
print(f"  실현 P&L       : {fmt_money(res_n['realized_pnl'])}")
print(f"  미실현 P&L     : {fmt_money(res_n['unrealized_pnl'])}")
print(f"  총 P&L         : {fmt_money(res_n['total_pnl'])}")
print(f"  기말 현금       : {fmt_money(res_n['cash_end'])}")

print("\n【Crash Strong Mode — 사용자 선택 A/C/A/A/A】")
print(f"  매도 횟수      : {sum(1 for a in res_c['actions'] if a[2]=='SELL')}건")
print(f"  추매 횟수      : {sum(1 for a in res_c['actions'] if a[2]=='BUY+')}건")
print(f"  실현 P&L       : {fmt_money(res_c['realized_pnl'])}")
print(f"  미실현 P&L     : {fmt_money(res_c['unrealized_pnl'])}")
print(f"  총 P&L         : {fmt_money(res_c['total_pnl'])}")
print(f"  기말 현금       : {fmt_money(res_c['cash_end'])}")

diff = res_c["total_pnl"] - res_n["total_pnl"]
print(f"\n【차이】 Crash − Normal = {fmt_money(diff)}  ({'+' if diff>0 else ''}{diff/abs(res_n['total_pnl']+1)*100:.1f}%)")

print("\n[Normal 매도 액션]")
for a in res_n["actions"]:
    if a[2] == "SELL":
        print(f"  {a[0]} {a[1]} {a[3]}주 @ {a[4]:,.0f} ({a[5]}) → pnl {a[6]:+,.0f}")

print("\n[Crash 액션]")
for a in res_c["actions"][:30]:
    print(f"  {a[0]} {a[1]} {a[2]} {a[3]}주 @ {a[4]:,.0f} ({a[5]}) {f'pnl {a[6]:+,.0f}' if a[6] else ''}")
if len(res_c["actions"]) > 30:
    print(f"  ... +{len(res_c['actions'])-30} more")
