"""최근 N일 보유 종목 백테스트 — 레거시 vs Uptrend(수정 전) vs Uptrend(수정 후).

운영 서버에서 실행 (yfinance 네트워크 필요):
    source venv/bin/activate
    python scripts/backtest_last_week.py                 # 최근 7일, data/stock_advisor.db
    python scripts/backtest_last_week.py --days 14       # 기간 변경
    python scripts/backtest_last_week.py --db /root/stock-advisor/data/stock_advisor.db
    python scripts/backtest_last_week.py --csv-dir ./ohlcv   # 미리 받아둔 CSV 사용 (네트워크 불필요)
    python scripts/backtest_last_week.py --synthetic     # 네트워크 없이 합성 데이터로 스크립트 자체 검증

비교 대상 (일봉 종가/고가/저가 기반, 보유 종목 한정 — 신규 종목 score 매수는 시뮬레이션하지 않음):
  1) Legacy        : 익절 7% 분할매도, 트레일링 -5%, 손절 -7%×3일(KR)/-5% 즉시(US), 추매 -5% 이하
  2) Uptrend(before): 2026-09-09 수정 전 동작
        - DCA 1단계 -3% 가 레거시 추매 게이트(-5%) 에 막혀 실효 임계 -5%
        - US 종목 DCA = USD 현금 전액 매수 (통화 혼용 버그)
        - 안전망 손절 없음
  3) Uptrend(after) : 수정 후 동작
        - DCA -3/-8/-15% × 3/4/5% (KR=KOSPI, US=SPX 5d mult), 현금 부족은 단계 소진 X
        - 안전망 손절 -25% × 7거래일 (Crash 중 보류)
        - 예비현금 10% (DCA 는 0% 까지 사용)

CSV 포맷 (--csv-dir): <symbol>.csv, 컬럼 Date,Open,High,Low,Close (yfinance history().to_csv() 그대로).
  symbol 예: 005930.KS, AAPL, ^KS11, ^GSPC, ^VIX
"""
import argparse
import math
import os
import sqlite3
import sys
from datetime import datetime, timedelta

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(ROOT)

# ── 파라미터 (settings_service DEFAULT_SETTINGS 와 동일) ────────────────────────
LEGACY = dict(take_profit=7.0, trailing=-5.0, tight_trigger=3.0, tight_stop=-3.0,
              stop_loss_kr=-7.0, stop_loss_days_kr=3, stop_loss_us=-5.0, stop_loss_days_us=0,
              add_below=-5.0, per_trade_ratio=0.05, sell_split=5)
UPTREND = dict(partial_take=10.0, partial_ratio=0.5, trailing_remaining=-10.0,
               stop_loss=-25.0, stop_loss_days=7,
               stages=[(-15.0, 0.05), (-8.0, 0.04), (-3.0, 0.03)],
               max_position=0.15, min_cash=0.0, reserve=0.10,
               crash_1d=-5.0, crash_5d=-10.0, crash_vix=35.0, frozen_vix=50.0,
               tiers=[(-18.0, 2.5), (-12.0, 2.0), (-7.0, 1.5)])


def is_kr(ticker: str) -> bool:
    return ticker.isdigit() and len(ticker) == 6


def ysym(ticker: str) -> str:
    return f"{ticker}.KS" if is_kr(ticker) else ticker


# ── 데이터 로드 ─────────────────────────────────────────────────────────────────
def load_holdings(db_path: str, user_id: str = "sean"):
    conn = sqlite3.connect(db_path)
    holdings = pd.read_sql(
        "SELECT ticker, name, quantity, buy_price FROM portfolio_holdings "
        "WHERE portfolio_id = (SELECT id FROM portfolios WHERE user_id=?) AND quantity > 0",
        conn, params=(user_id,))
    cash = pd.read_sql("SELECT cash_balance FROM portfolios WHERE user_id=?", conn, params=(user_id,))
    conn.close()
    cash_krw = float(cash.iloc[0, 0]) if len(cash) else 0.0
    return holdings, cash_krw


def fetch_yf(symbols, start, end):
    import yfinance as yf
    out = {}
    for s in symbols:
        try:
            df = yf.Ticker(s).history(start=start, end=end, interval="1d", auto_adjust=True)
            if df is not None and not df.empty:
                df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
                out[s] = df[["Open", "High", "Low", "Close"]]
        except Exception as e:  # noqa: BLE001
            print(f"  ⚠️ {s} fetch failed: {e}")
    return out


def load_csv_dir(symbols, csv_dir):
    out = {}
    for s in symbols:
        p = os.path.join(csv_dir, f"{s}.csv")
        if os.path.exists(p):
            df = pd.read_csv(p, parse_dates=["Date"], index_col="Date")
            df.index = pd.to_datetime(df.index, utc=True).tz_localize(None).normalize()
            out[s] = df[["Open", "High", "Low", "Close"]]
    return out


def synthetic_data(holdings, start, end, seed=7):
    """네트워크 없이 스크립트 검증용. 급락→반등 시나리오 (DCA/Crash/부분익절 경로가 모두 발동하도록)."""
    import numpy as np
    rng = np.random.default_rng(seed)
    days = pd.bdate_range(start=start - timedelta(days=12), end=end - timedelta(days=1))
    n = len(days)
    # 지수: 앞 절반 완만, 중반 -12% 급락(하루 -6%), 후반 반등
    idx_ret = np.zeros(n)
    mid = n // 2
    idx_ret[mid] = -0.06; idx_ret[mid + 1] = -0.04; idx_ret[mid + 2] = -0.03
    idx_ret[mid + 3:] = 0.015
    out = {}
    for sym, base in (("^KS11", 3000.0), ("^GSPC", 6000.0)):
        px = base * np.cumprod(1 + idx_ret + rng.normal(0, 0.003, n))
        out[sym] = pd.DataFrame({"Open": px, "High": px * 1.005, "Low": px * 0.995, "Close": px}, index=days)
    vix = np.where(np.arange(n) >= mid, 38.0, 18.0)
    vix[mid + 4:] = 24.0
    out["^VIX"] = pd.DataFrame({"Open": vix, "High": vix, "Low": vix, "Close": vix}, index=days)
    for _, h in holdings.iterrows():
        beta = rng.uniform(0.8, 1.6)
        drift = rng.normal(0, 0.004, n)
        # 종목별로 매수가 근처에서 시작 → 일부는 상승(부분익절), 대부분 급락 후 반등
        start_px = float(h["buy_price"]) * rng.uniform(0.97, 1.08)
        px = start_px * np.cumprod(1 + beta * idx_ret + drift)
        out[ysym(h["ticker"])] = pd.DataFrame(
            {"Open": px, "High": px * (1 + rng.uniform(0.005, 0.02, n)),
             "Low": px * (1 - rng.uniform(0.005, 0.03, n)), "Close": px}, index=days)
    return out


def index_changes(df: pd.DataFrame) -> dict:
    """{date: (chg_1d, chg_5d)}"""
    closes = df["Close"].tolist()
    dates = list(df.index)
    res = {}
    for i, d in enumerate(dates):
        c1 = (closes[i] - closes[i - 1]) / closes[i - 1] * 100 if i >= 1 else 0.0
        c5 = (closes[i] - closes[i - 5]) / closes[i - 5] * 100 if i >= 5 else 0.0
        res[d] = (c1, c5)
    return res


# ── 시뮬레이터 ──────────────────────────────────────────────────────────────────
class Sim:
    def __init__(self, name, mode, holdings, cash_krw, usd_cash, fx, data, kospi, spx, vix, days, before_fix=False):
        self.name, self.mode, self.before_fix = name, mode, before_fix
        self.fx = fx
        self.cash_krw, self.usd_cash = cash_krw, usd_cash
        self.data, self.kospi, self.spx, self.vix, self.days = data, kospi, spx, vix, days
        self.pos = {}
        for _, h in holdings.iterrows():
            t = h["ticker"]
            if ysym(t) not in data:
                continue
            self.pos[t] = dict(name=h["name"], qty=int(h["quantity"]), init_qty=int(h["quantity"]),
                               buy=float(h["buy_price"]), high=float(h["buy_price"]),
                               partial=False, rem_high=0.0, dca={-15.0: False, -8.0: False, -3.0: False},
                               streak=0, sold=False)
        self.actions = []
        self.last = {}

    # helpers
    def _to_krw(self, t, v):
        return v if is_kr(t) else v * self.fx

    def _cash_krw_for(self, t):
        return self.cash_krw if is_kr(t) else self.usd_cash * self.fx

    def _spend(self, t, amt_local):
        if is_kr(t):
            self.cash_krw -= amt_local
        else:
            self.usd_cash -= amt_local

    def _market_total_krw(self, t, d):
        tot = 0.0
        for k, p in self.pos.items():
            if p["qty"] > 0 and is_kr(k) == is_kr(t):
                px = self._px(k, d)
                if px:
                    tot += self._to_krw(k, px * p["qty"])
        return tot + self._cash_krw_for(t)

    def _px(self, t, d):
        df = self.data.get(ysym(t))
        if df is None or d not in df.index:
            return None
        return float(df.loc[d, "Close"])

    def _row(self, t, d):
        df = self.data.get(ysym(t))
        if df is None or d not in df.index:
            return None
        return df.loc[d]

    def _sell(self, d, t, qty, px, label):
        p = self.pos[t]
        qty = min(qty, p["qty"])
        if qty <= 0:
            return
        pnl = (px - p["buy"]) * qty
        self._spend(t, -px * qty)
        p["qty"] -= qty
        self.actions.append((d.strftime("%Y-%m-%d"), t, "SELL", qty, px, label, self._to_krw(t, pnl)))
        if p["qty"] == 0:
            p["sold"] = True; p["partial"] = False; p["dca"] = {k: False for k in p["dca"]}

    def _buy(self, d, t, qty, px, label):
        p = self.pos[t]
        if qty <= 0:
            return
        cost = px * qty
        p["buy"] = (p["buy"] * p["qty"] + cost) / (p["qty"] + qty)
        p["qty"] += qty
        p["sold"] = False
        self._spend(t, cost)
        self.actions.append((d.strftime("%Y-%m-%d"), t, "BUY+", qty, px, label, 0.0))

    # market state
    def _crash(self, d):
        k = self.kospi.get(d, (0, 0)); s = self.spx.get(d, (0, 0))
        v = self.vix.get(d, 20.0)
        return min(k[0], s[0]) < UPTREND["crash_1d"] or min(k[1], s[1]) < UPTREND["crash_5d"] or v > UPTREND["crash_vix"]

    def _frozen(self, d):
        return self.vix.get(d, 20.0) > UPTREND["frozen_vix"]

    def _mult(self, t, d):
        # before_fix: 항상 KOSPI. after: KR=KOSPI, US=SPX
        src = self.kospi if (is_kr(t) or self.before_fix) else self.spx
        c5 = src.get(d, (0, 0))[1]
        for th, m in UPTREND["tiers"]:
            if c5 < th:
                return m
        return 1.0

    # strategies
    def run(self):
        for d in self.days:
            for t in list(self.pos):
                if self.pos[t]["qty"] <= 0:
                    continue
                row = self._row(t, d)
                if row is None:
                    continue
                if self.mode == "legacy":
                    self._step_legacy(d, t, row)
                else:
                    self._step_uptrend(d, t, row)
        for t in self.pos:
            px = None
            for d in reversed(self.days):
                px = self._px(t, d)
                if px:
                    break
            self.last[t] = px
        return self

    def _step_legacy(self, d, t, row):
        p = self.pos[t]; L = LEGACY
        hi, lo, cl = float(row["High"]), float(row["Low"]), float(row["Close"])
        p["high"] = max(p["high"], hi)
        pnl = (cl - p["buy"]) / p["buy"] * 100
        max_pnl = (p["high"] - p["buy"]) / p["buy"] * 100
        # trailing / tight stop (전량)
        th = L["tight_stop"] if max_pnl >= L["tight_trigger"] else L["trailing"]
        trail_px = p["high"] * (1 + th / 100)
        if lo <= trail_px and p["high"] > p["buy"] * 1.0001:
            self._sell(d, t, p["qty"], trail_px, "trailing_stop"); return
        # stop loss
        sl = L["stop_loss_kr"] if is_kr(t) else L["stop_loss_us"]
        sl_days = L["stop_loss_days_kr"] if is_kr(t) else L["stop_loss_days_us"]
        if pnl <= sl:
            p["streak"] += 1
            if p["streak"] >= max(1, sl_days):
                self._sell(d, t, p["qty"], cl, f"stop_loss_{p['streak']}d"); return
        else:
            p["streak"] = 0
        # take profit (분할 1/5)
        if pnl >= L["take_profit"]:
            q = max(1, math.ceil(p["qty"] / L["sell_split"]))
            self._sell(d, t, q, cl, "take_profit_split"); return
        # add buy (-5% 이하, 1회/일, per_trade 5%)
        if pnl <= L["add_below"]:
            budget = self._market_total_krw(t, d) * L["per_trade_ratio"]
            budget = min(budget, self._cash_krw_for(t))
            q = int(budget // self._to_krw(t, cl))
            if q > 0:
                self._buy(d, t, q, cl, "add_position")

    def _step_uptrend(self, d, t, row):
        p = self.pos[t]; U = UPTREND
        hi, lo, cl = float(row["High"]), float(row["Low"]), float(row["Close"])
        pnl = (cl - p["buy"]) / p["buy"] * 100
        crash = self._crash(d); frozen = self._frozen(d)
        # 1) 안전망 손절 (after 만)
        if not self.before_fix and pnl <= U["stop_loss"]:
            p["streak"] += 1
            if p["streak"] >= U["stop_loss_days"] and not crash:
                self._sell(d, t, p["qty"], cl, f"uptrend_stop_loss_{p['streak']}d"); return
        else:
            p["streak"] = 0
        # 2) 부분익절
        if not p["partial"] and not crash:
            tp_px = p["buy"] * (1 + U["partial_take"] / 100)
            if hi >= tp_px:
                q = max(1, int(p["qty"] * U["partial_ratio"]))
                if q >= p["qty"]:
                    q = max(1, p["qty"] - 1) if p["qty"] > 1 else p["qty"]
                self._sell(d, t, q, tp_px, "partial_take")
                if p["qty"] > 0:
                    p["partial"] = True; p["rem_high"] = max(tp_px, hi)
                return
        # 3) 잔여 trailing
        if p["partial"] and p["qty"] > 0:
            p["rem_high"] = max(p["rem_high"], hi)
            tr_px = p["rem_high"] * (1 + U["trailing_remaining"] / 100)
            if lo <= tr_px and not crash:
                self._sell(d, t, p["qty"], tr_px, "trailing_remaining"); return
        # 4) DCA
        if frozen:
            return
        for th, ratio in U["stages"]:
            if p["dca"][th]:
                continue
            eff_th = th
            if self.before_fix and th == -3.0:
                eff_th = LEGACY["add_below"]   # 레거시 추매 게이트에 막혀 실효 -5%
            if pnl > eff_th:
                continue
            total = self._market_total_krw(t, d)
            price_krw = self._to_krw(t, cl)
            if self.before_fix and not is_kr(t):
                # 통화 혼용 버그: (us_total_krw + KRW현금)×ratio / USD가격 → 과대 수량 → USD 현금 전액으로 축소
                q = int(self.usd_cash // cl)
                if q > 0:
                    self._buy(d, t, q, cl, f"dca_{int(th)}%_ALLCASH_BUG")
                p["dca"][th] = True
                break
            budget = total * ratio * self._mult(t, d)
            cur_val = p["qty"] * price_krw
            max_allowed = total * U["max_position"]
            if cur_val >= max_allowed:
                p["dca"][th] = True; break
            budget = min(budget, max_allowed - cur_val)
            avail = self._cash_krw_for(t)
            budget = min(budget, max(0.0, avail - total * U["min_cash"]))
            if budget < price_krw:
                if self.before_fix:
                    p["dca"][th] = True   # 옛 동작: 현금 부족도 단계 소진
                break
            q = int(budget // price_krw)
            self._buy(d, t, q, cl, f"dca_{int(th)}%x{self._mult(t, d):.1f}")
            p["dca"][th] = True
            break

    # summary
    def summary(self):
        realized = sum(a[6] for a in self.actions if a[2] == "SELL")
        unreal = 0.0; mkt = 0.0
        for t, p in self.pos.items():
            px = self.last.get(t)
            if p["qty"] > 0 and px:
                unreal += self._to_krw(t, (px - p["buy"]) * p["qty"])
                mkt += self._to_krw(t, px * p["qty"])
        cash = self.cash_krw + self.usd_cash * self.fx
        return dict(name=self.name, realized=realized, unrealized=unreal, total=realized + unreal,
                    cash=cash, mkt=mkt, equity=cash + mkt,
                    sells=sum(1 for a in self.actions if a[2] == "SELL"),
                    buys=sum(1 for a in self.actions if a[2] == "BUY+"))


# ── main ────────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(ROOT, "data", "stock_advisor.db"))
    ap.add_argument("--user", default="sean")
    ap.add_argument("--days", type=int, default=7, help="백테스트 기간 (캘린더 일)")
    ap.add_argument("--end", default=None, help="종료일 YYYY-MM-DD (기본 오늘)")
    ap.add_argument("--fx", type=float, default=1350.0, help="USD/KRW (US 종목 있을 때만 사용)")
    ap.add_argument("--usd-cash", type=float, default=0.0, help="초기 USD 현금 (DB 에 없음)")
    ap.add_argument("--cash", type=float, default=None, help="초기 KRW 현금 오버라이드 (기본 DB portfolios.cash_balance)")
    ap.add_argument("--csv-dir", default=None)
    ap.add_argument("--synthetic", action="store_true")
    args = ap.parse_args()

    end = datetime.strptime(args.end, "%Y-%m-%d") if args.end else datetime.now()
    end = end.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    start = end - timedelta(days=args.days + 1)
    lookback = start - timedelta(days=14)   # 5d 변화율용

    holdings, cash_krw = load_holdings(args.db, args.user)
    if args.cash is not None:
        cash_krw = args.cash
    print(f"보유 {len(holdings)}종목, KRW 현금 {cash_krw:,.0f}원, USD 현금 ${args.usd_cash:,.2f}  "
          f"기간 {start.date()} ~ {(end - timedelta(days=1)).date()}")
    symbols = [ysym(t) for t in holdings["ticker"]] + ["^KS11", "^GSPC", "^VIX"]

    if args.synthetic:
        data = synthetic_data(holdings, start, end)
        print("⚠️ 합성 데이터 모드 — 결과는 실제 시세가 아님 (스크립트 검증용)")
    elif args.csv_dir:
        data = load_csv_dir(symbols, args.csv_dir)
    else:
        data = fetch_yf(symbols, lookback.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
    missing = [s for s in symbols if s not in data]
    if missing:
        print(f"⚠️ 데이터 없음: {missing}")
    if "^KS11" not in data:
        print("❌ KOSPI 데이터 없음 — 중단"); sys.exit(1)

    kospi = index_changes(data["^KS11"])
    spx = index_changes(data["^GSPC"]) if "^GSPC" in data else {}
    vix = {d: float(v) for d, v in data["^VIX"]["Close"].items()} if "^VIX" in data else {}
    all_days = sorted({d for s, df in data.items() if not s.startswith("^") for d in df.index})
    days = [d for d in all_days if start <= d < end]
    if not days:
        print("❌ 기간 내 거래일 없음"); sys.exit(1)
    print(f"거래일 {len(days)}일: {days[0].date()} ~ {days[-1].date()}")
    crash_days = [d.strftime('%m-%d') for d in days if Sim('', 'u', holdings.iloc[0:0], 0, 0, 1, data, kospi, spx, vix, days)._crash(d)]
    print(f"Crash 판정일: {crash_days or '없음'}   VIX 범위: "
          f"{min((vix.get(d, 20.0) for d in days), default=0):.1f}~{max((vix.get(d, 20.0) for d in days), default=0):.1f}")

    common = dict(holdings=holdings, cash_krw=cash_krw, usd_cash=args.usd_cash, fx=args.fx,
                  data=data, kospi=kospi, spx=spx, vix=vix, days=days)
    sims = [
        Sim("Legacy", "legacy", **common).run(),
        Sim("Uptrend(before)", "uptrend", before_fix=True, **common).run(),
        Sim("Uptrend(after)", "uptrend", before_fix=False, **common).run(),
    ]
    rows = [s.summary() for s in sims]

    def f(v): return f"{v:>+16,.0f}"
    print("\n" + "=" * 92)
    print(f"{'항목':<22}" + "".join(f"{r['name']:>23}" for r in rows))
    print("-" * 92)
    print(f"{'매도 건수':<22}" + "".join(f"{r['sells']:>23}" for r in rows))
    print(f"{'추매 건수':<22}" + "".join(f"{r['buys']:>23}" for r in rows))
    print(f"{'실현 P&L':<22}" + "".join(f"{f(r['realized']):>23}" for r in rows))
    print(f"{'미실현 P&L':<22}" + "".join(f"{f(r['unrealized']):>23}" for r in rows))
    print(f"{'총 P&L':<22}" + "".join(f"{f(r['total']):>23}" for r in rows))
    print(f"{'기말 현금(KRW환산)':<22}" + "".join(f"{f(r['cash']):>23}" for r in rows))
    print(f"{'기말 평가자산':<22}" + "".join(f"{f(r['equity']):>23}" for r in rows))
    base = rows[0]["total"]
    print(f"\n[차이 vs Legacy]  before {rows[1]['total'] - base:+,.0f}원   after {rows[2]['total'] - base:+,.0f}원"
          f"   (after − before {rows[2]['total'] - rows[1]['total']:+,.0f}원)")

    for s in sims:
        print(f"\n[{s.name} — 매매 액션 {len(s.actions)}건]")
        for a in s.actions[:60]:
            pnl = f" pnl {a[6]:+,.0f}" if a[2] == "SELL" else ""
            print(f"  {a[0]} {a[1]:<8} {a[2]:<5} {a[3]:>5}주 @ {a[4]:>12,.2f}  {a[5]}{pnl}")
        if len(s.actions) > 60:
            print(f"  ... 외 {len(s.actions) - 60}건")


if __name__ == "__main__":
    main()
