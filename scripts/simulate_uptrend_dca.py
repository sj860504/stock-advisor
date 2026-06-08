"""6/1~6/8 우상향 가정 DCA 알고리즘 시뮬레이션.

사용자 결정 B/A/A/A/A:
- 손절: -20% 극단 + 5일 연속만 (안전망)
- 부분 익절: +10% 도달 → 50% 매도, 50% trailing -8%
- DCA 3단계: -3% / -8% / -15% → 3% / 4% / 5% 자본
- 종목 max position 15%
- (2020 백테스트는 별도)

매도 보류 조건 (Crash):
- KOSPI 1d <-3%
- KOSPI 5d <-8%
- VIX > 30
- Frozen: VIX > 50 → 매수도 보류

KOSPI 5d 보너스 — per_trade mult:
  <-3%: 1.0×   <-7%: 1.5×   <-12%: 2.0×   <-18%: 2.5×
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

# 1) 보유 종목 + 매수가
conn = sqlite3.connect(DB)
holdings = pd.read_sql("""
    SELECT ticker, name, quantity, buy_price
    FROM portfolio_holdings
    WHERE portfolio_id = (SELECT id FROM portfolios WHERE user_id='sean')
""", conn)
cash_init = pd.read_sql("SELECT cash_balance FROM portfolios WHERE user_id='sean'", conn).iloc[0,0]
conn.close()
print(f"보유 {len(holdings)}종목, 초기 KRW 현금 {cash_init:,.0f}원")

# 2) OHLCV
ohlcv = {}
for _, row in holdings.iterrows():
    sym = row["ticker"] + ".KS"
    try:
        df = yf.Ticker(sym, session=session).history(start=START, end=END, interval="1d", auto_adjust=True)
        if not df.empty:
            ohlcv[row["ticker"]] = df
    except Exception:
        pass

# 3) KOSPI 인덱스 + 1d/5d 변화율 (Crash 감지용)
kospi = yf.Ticker("^KS11", session=session).history(start="2026-05-20", end=END, interval="1d", auto_adjust=True)
kospi_daily = {}
closes = list(kospi["Close"])
dates = [d.strftime("%Y-%m-%d") for d in kospi.index]
for i, d in enumerate(dates):
    if d < START:
        continue
    chg_1d = (closes[i] - closes[i-1])/closes[i-1]*100 if i > 0 else 0
    chg_5d = (closes[i] - closes[i-5])/closes[i-5]*100 if i >= 5 else 0
    kospi_daily[d] = (chg_1d, chg_5d)

vix = 21.5  # 6/8 기준 (간단화)


def kospi_per_trade_mult(d):
    """KOSPI 5d 변화율에 따른 per_trade multiplier."""
    if d not in kospi_daily:
        return 1.0
    _, chg_5d = kospi_daily[d]
    if chg_5d < -18:
        return 2.5
    if chg_5d < -12:
        return 2.0
    if chg_5d < -7:
        return 1.5
    return 1.0


def is_crash_day(d):
    """매도/손절 보류 — 강화 임계 (B 추천)."""
    if d not in kospi_daily:
        return False
    chg_1d, chg_5d = kospi_daily[d]
    if chg_1d < -5 or chg_5d < -10 or vix > 35:
        return True
    return False


def is_frozen_day(d):
    """매수도 보류 (panic)."""
    return vix > 50


# 4) 알고리즘 시뮬레이션
def simulate(name, params):
    p = params
    cash = cash_init
    positions = {}
    for _, h in holdings.iterrows():
        positions[h["ticker"]] = {
            "name": h["name"],
            "qty": h["quantity"],
            "init_qty": h["quantity"],
            "buy_price": h["buy_price"],
            "high_since_buy": h["buy_price"],
            "partial_taken": False,
            "remaining_high": h["buy_price"],  # trailing on remaining qty after partial
            "dca_done": {-3: False, -8: False, -15: False},
            "consec_loss_days": 0,
            "sold": False,
        }
    actions = []

    all_dates = set()
    for df in ohlcv.values():
        for d in df.index:
            all_dates.add(d.strftime("%Y-%m-%d"))
    trading_days = sorted([d for d in all_dates if START <= d < END])

    for d in trading_days:
        crash = is_crash_day(d) if p.get("crash_guard") else False
        frozen = is_frozen_day(d) if p.get("frozen_guard") else False
        mult = kospi_per_trade_mult(d) if p.get("kospi_bonus") else 1.0

        for ticker, pos in positions.items():
            if pos["qty"] == 0:
                continue
            if ticker not in ohlcv:
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

            # 1) 부분 익절 (+10%, 50%) — 한 번만
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

            # 2) 잔여 trailing (-8% from remaining_high)
            if p.get("partial_take") and pos["partial_taken"] and pos["qty"] > 0:
                pos["remaining_high"] = max(pos["remaining_high"], day_high)
                trail_th = pos["remaining_high"] * (1 + p["trailing_remaining"] / 100)
                if day_low <= trail_th and not crash:
                    pnl = (trail_th - buy) * pos["qty"]
                    cash += trail_th * pos["qty"]
                    actions.append((d, ticker, "SELL", pos["qty"], trail_th, "trailing_remaining", pnl))
                    pos["qty"] = 0
                    pos["sold"] = True
                    continue

            # 3) 일반 trailing — 비활성 (B 선택: trailing for losers는 없음)
            #    Normal Mode 시뮬을 위해 옵션화
            if p.get("normal_trailing") and not pos["partial_taken"]:
                trail_th = pos["high_since_buy"] * (1 + p.get("trailing", -5) / 100)
                if day_low <= trail_th and not crash:
                    pnl = (trail_th - buy) * pos["qty"]
                    cash += trail_th * pos["qty"]
                    actions.append((d, ticker, "SELL", pos["qty"], trail_th, "trailing_loss", pnl))
                    pos["qty"] = 0; pos["sold"] = True
                    continue

            # 4) 손절 — Crash 중에는 보류
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

            # 5) DCA 추매 (frozen 아니고 손실 단계 도달)
            if p.get("dca") and not frozen:
                total_assets = cash + sum(p2["qty"] * day_close for t2, p2 in positions.items()
                                          if not p2["sold"])
                min_cash = total_assets * p.get("min_cash_ratio", 0.05)
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

    # 마무리: 미실현 P&L (6/8 종가 기준)
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
            "actions": actions, "positions": positions, "last_prices": last_prices}


PARAMS_NORMAL = {
    "partial_take": False,
    "normal_trailing": True, "trailing": -5,
    "take_profit": 7,
    "stop_loss": None,  # signal_service forced_sell 별도 — 여기선 trailing/stop_loss만
    "dca": False, "crash_guard": False, "frozen_guard": False, "kospi_bonus": False,
}

PARAMS_UPTREND = {
    # 부분익절 +15%, 잔여 trailing -10% (튜닝)
    "partial_take": True, "take_profit": 15, "trailing_remaining": -10,
    "normal_trailing": False,
    # 손절 -25%/7일 + Crash 중엔 보류
    "stop_loss": -25, "stop_loss_days": 7,
    "dca": True,
    "dca_ratios": {-3: 3, -8: 4, -15: 5},
    # Crash 강화: 5d<-10% / 1d<-5% / VIX>35
    "crash_guard": True, "frozen_guard": True, "kospi_bonus": True,
    "min_cash_ratio": 0.0,
    "max_position_pct": 0.15,
}

res_n = simulate("Normal (현재)", PARAMS_NORMAL)
res_u = simulate("Uptrend DCA (B/A/A/A/A)", PARAMS_UPTREND)


def fmt(v): return f"{v:>+15,.0f}원"

print("\n" + "=" * 78)
print(f"{'항목':<30} {'Normal (현재)':>20} {'Uptrend DCA':>20}")
print("-" * 78)
sells_n = sum(1 for a in res_n["actions"] if a[2] in ("SELL", "SELL50"))
sells_u = sum(1 for a in res_u["actions"] if a[2] in ("SELL", "SELL50"))
buys_u = sum(1 for a in res_u["actions"] if a[2] == "BUY+")
print(f"{'매도(부분익절 포함)':<30} {sells_n:>20}건 {sells_u:>18}건")
print(f"{'DCA 추매':<30} {0:>20}건 {buys_u:>18}건")
print(f"{'실현 P&L':<30} {fmt(res_n['realized']):>20} {fmt(res_u['realized']):>20}")
print(f"{'미실현 P&L (6/8 종가)':<30} {fmt(res_n['unrealized']):>20} {fmt(res_u['unrealized']):>20}")
print(f"{'총 P&L':<30} {fmt(res_n['total']):>20} {fmt(res_u['total']):>20}")
print(f"{'기말 현금':<30} {fmt(res_n['cash_end']):>20} {fmt(res_u['cash_end']):>20}")
diff = res_u["total"] - res_n["total"]
print(f"\n[차이] Uptrend − Normal = {fmt(diff)}")

# 회복 시나리오: 6/8 종가 → 1주/2주/1개월 +5/+10/+15%
print("\n" + "=" * 78)
print("회복 시나리오 (6/8 종가 → 가격 +N% 가정한 추가 P&L)")
print("-" * 78)
for pct in (3, 5, 10, 15):
    upside_n = 0; upside_u = 0
    for ticker, pos in res_n["positions"].items():
        if pos["qty"] > 0 and res_n["last_prices"].get(ticker):
            upside_n += res_n["last_prices"][ticker] * pct/100 * pos["qty"]
    for ticker, pos in res_u["positions"].items():
        if pos["qty"] > 0 and res_u["last_prices"].get(ticker):
            upside_u += res_u["last_prices"][ticker] * pct/100 * pos["qty"]
    total_n = res_n["total"] + upside_n
    total_u = res_u["total"] + upside_u
    diff = total_u - total_n
    print(f"  +{pct:2d}%: Normal {fmt(total_n)}  |  Uptrend {fmt(total_u)}  |  차이 {fmt(diff)}")

# DCA 액션 상세
print("\n[Uptrend DCA — 매매 액션]")
for a in res_u["actions"]:
    label = a[2] if a[2] != "BUY+" else "추매"
    pnl_str = f" pnl {a[6]:+,.0f}" if a[6] else ""
    print(f"  {a[0]} {a[1]} {label} {a[3]:>4}주 @ {a[4]:>10,.0f} ({a[5]}){pnl_str}")
