"""리플레이 백테스트 — 운영 코드(SignalService.calculate_score / PositionService._execute_collected_signals)를
그대로 호출해 일봉 시계열 위에서 재생한다. 시뮬 스크립트가 규칙을 따로 구현해 운영 코드와 어긋나는 문제를 없앤다.

    python scripts/backtest_replay.py --days 30                 # 최근 30일, DB 보유종목, yfinance
    python scripts/backtest_replay.py --days 30 --mode both     # uptrend vs legacy 비교
    python scripts/backtest_replay.py --synthetic --cash 5000000
    python scripts/backtest_replay.py --csv-dir ./ohlcv --lookback 260

동작:
  · 각 거래일마다 TickerState(종가/고/저/전일종가/등락률/RSI14/EMA/ATR/평균거래량) 와
    MacroDataSnapshot(VIX, 지수 1d/5d/20d/90d, VIX 1d 변화) 을 구성
  · SignalService.calculate_score → SignalSchema → PositionService._execute_collected_signals
  · 주문(TradeExecutorService._execute_trade_v2)만 시뮬레이터로 대체(즉시 체결, 수수료 0)
  · DB 의존(SettingsService/StockMeta/TradeHistory/MarketHour)은 스텁으로 대체
제한:
  · 유니버스 = 보유 종목 (+ --extra 로 추가 티커). 자산관리(budget buy)·미체결·그룹한도·Frozen 실행 게이트는
    execution 레벨이라 재생하지 않음 (Frozen 만 시뮬 주문기에서 재현). 하루 1회 루프(운영은 1분 루프).
  · DCF 값은 --dcf-from-db 시 DB 최신 financials 를 상수로 사용, 아니면 없음(+10 패널티).
"""
import argparse
import math
import os
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import backtest_last_week as blw  # noqa: E402  (데이터 로더 재사용)

from models.schemas import ExecutionConfig, HoldingSchema, MacroDataSnapshot, MarketRegimeSchema, SignalSchema, TradeResult, UserState  # noqa: E402
from models.ticker_state import TickerState  # noqa: E402
from services.analysis.indicator_service import IndicatorService  # noqa: E402
from services.config.settings_service import SettingsService  # noqa: E402
from services.strategy.crash_guard_service import CrashGuardService  # noqa: E402
from services.strategy.execution_service_v2 import TradeExecutorService  # noqa: E402
from services.strategy.position_service import PositionService  # noqa: E402
from services.strategy.signal_service import SignalService  # noqa: E402


class SettingsStub:
    def __init__(self, **overrides):
        self.overrides = {k: str(v) for k, v in overrides.items()}

    def __call__(self, key, default=None):
        if key in self.overrides:
            return self.overrides[key]
        return SettingsService.DEFAULT_SETTINGS.get(key, (default,))[0]


class Portfolio:
    """시뮬 포트폴리오 (KRW/USD 현금 + 평균단가 포지션)."""

    def __init__(self, holdings_df: pd.DataFrame, cash_krw: float, usd_cash: float, fx: float):
        self.cash_krw, self.usd_cash, self.fx = cash_krw, usd_cash, fx
        self.pos = {r.ticker: {"qty": int(r.quantity), "buy": float(r.buy_price), "name": r.name}
                    for r in holdings_df.itertuples()}
        self.actions = []

    def holdings(self, prices: dict) -> list:
        out = []
        for t, p in self.pos.items():
            if p["qty"] <= 0:
                continue
            out.append(HoldingSchema(ticker=t, name=p["name"], quantity=p["qty"], buy_price=p["buy"],
                                     current_price=prices.get(t, p["buy"])))
        return out

    def totals(self, prices: dict):
        kr = sum(p["qty"] * prices.get(t, p["buy"]) for t, p in self.pos.items() if blw.is_kr(t) and p["qty"] > 0)
        us = sum(p["qty"] * prices.get(t, p["buy"]) for t, p in self.pos.items() if not blw.is_kr(t) and p["qty"] > 0)
        return kr + self.cash_krw, us * self.fx + self.usd_cash * self.fx

    def buy(self, d, t, qty, px, reason):
        p = self.pos.setdefault(t, {"qty": 0, "buy": 0.0, "name": t})
        cost = qty * px
        if blw.is_kr(t):
            if cost > self.cash_krw + 1e-6:
                qty = int(self.cash_krw // px); cost = qty * px
        else:
            if cost > self.usd_cash + 1e-6:
                qty = int(self.usd_cash // px); cost = qty * px
        if qty <= 0:
            return 0, 0.0
        p["buy"] = (p["buy"] * p["qty"] + cost) / (p["qty"] + qty)
        p["qty"] += qty
        if blw.is_kr(t):
            self.cash_krw -= cost
        else:
            self.usd_cash -= cost
        self.actions.append((d, t, "BUY", qty, px, reason, 0.0))
        return qty, cost

    def sell(self, d, t, qty, px, reason):
        p = self.pos.get(t)
        if not p or p["qty"] <= 0:
            return 0, 0.0
        qty = min(qty, p["qty"])
        proceeds = qty * px
        pnl = (px - p["buy"]) * qty
        p["qty"] -= qty
        if blw.is_kr(t):
            self.cash_krw += proceeds
        else:
            self.usd_cash += proceeds
        self.actions.append((d, t, "SELL", qty, px, reason, pnl if blw.is_kr(t) else pnl * self.fx))
        return qty, proceeds


def build_state(ticker, df_upto: pd.DataFrame, dcf: float, conf: float = 1.0) -> TickerState:
    close = pd.to_numeric(df_upto["Close"], errors="coerce").dropna()
    if len(close) < 15:
        return None
    last = df_upto.iloc[-1]
    prev = float(close.iloc[-2]) if len(close) >= 2 else float(close.iloc[-1])
    st = TickerState(ticker=ticker, current_price=float(last["Close"]), open_price=float(last.get("Open", last["Close"])),
                     high_price=float(last.get("High", last["Close"])), low_price=float(last.get("Low", last["Close"])),
                     prev_close=prev, change_rate=(float(last["Close"]) - prev) / prev * 100 if prev > 0 else 0.0)
    snap = IndicatorService.compute_latest_indicators_snapshot(close)
    rsi = snap.rsi if snap else 50.0
    emas = snap.ema if snap else {}
    emas = {k: v for k, v in emas.items() if v is not None}
    if not any(emas.get(k) for k in (200, 120, 60)):
        emas[60] = float(close.tail(60).mean())
    st.update_indicators(emas=emas, dcf=dcf, rsi=rsi,
                         atr_pct=IndicatorService.compute_atr_pct(df_upto),
                         avg_volume_20d=IndicatorService.compute_avg_volume(df_upto), dcf_confidence=conf)
    st.volume = int(last.get("Volume", 0) or 0)
    st.last_updated = None
    return st


def load_dcf_from_db(db_path: str, tickers: list) -> dict:
    import sqlite3
    out = {}
    try:
        conn = sqlite3.connect(db_path)
        q = ("SELECT sm.ticker, f.dcf_value FROM financials f JOIN stock_meta sm ON sm.id = f.stock_id "
             "WHERE f.id IN (SELECT MAX(id) FROM financials GROUP BY stock_id)")
        for t, v in conn.execute(q):
            if t in tickers and v:
                out[t] = float(v)
        conn.close()
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ DCF from DB failed: {e}")
    return out


def run(mode: str, holdings_df, cash_krw, usd_cash, fx, data, days, args, dcf_map):
    uptrend = 1 if mode == "uptrend" else 0
    stub = SettingsStub(STRATEGY_UPTREND_DCA_ENABLED=uptrend, STRATEGY_SHADOW=0)
    pf = Portfolio(holdings_df, cash_krw, usd_cash, fx)
    user_state = UserState(user_id="sim")
    kospi = data.get("^KS11"); spx = data.get("^GSPC"); vixdf = data.get("^VIX")
    first_buy_date = (days[0] - timedelta(days=args.assume_held_days)) if args.assume_held_days else None

    def idx_changes(df, upto):
        if df is None:
            return {}
        c = [float(x) for x in df.loc[:upto, "Close"].dropna()]
        out = {}
        for h in (1, 5, 20, 90):
            out[h] = (c[-1] - c[-1 - h]) / c[-1 - h] * 100 if len(c) > h and c[-1 - h] > 0 else None
        return out

    current_macro = {}
    tickers = list(holdings_df.ticker) + [t for t in args.extra if t not in set(holdings_df.ticker)]

    def fake_trade(ticker, side, reason, profit_pct, is_holding, score, current_price, market_total, cash_balance,
                   exchange_rate, holdings=None, user_id="sim", holding=None, macro=None, target_cash_ratio_kr=None,
                   target_cash_ratio_us=None, forced_qty=None, gap_pct=0.0, trigger_reason=None):
        d = current_macro["day"]
        px = float(current_price)
        if side == "buy":
            if uptrend and CrashGuardService.is_frozen(macro):
                return TradeResult.no_op()
            if forced_qty is None:
                is_kr_flag = blw.is_kr(ticker)
                st = current_macro["states"].get(ticker)
                usd_cash_krw = pf.usd_cash * fx
                qty, _, _ = TradeExecutorService._calculate_buy_quantity(
                    score, pf.cash_krw, px, fx, is_kr_flag, market_total_krw=market_total,
                    usd_cash_krw=usd_cash_krw, gap_pct=gap_pct, atr_pct=getattr(st, "atr_pct", 0.0) if st else 0.0)
            else:
                qty = int(forced_qty)
            qty, cost = pf.buy(d, ticker, qty, px, trigger_reason or reason)
            if qty <= 0:
                return TradeResult.no_op()
            return TradeResult(executed=True, spent_krw=cost if blw.is_kr(ticker) else 0.0,
                               spent_usd=0.0 if blw.is_kr(ticker) else cost)
        else:
            p = pf.pos.get(ticker)
            if not p or p["qty"] <= 0:
                return TradeResult.no_op()
            qty = int(forced_qty) if forced_qty else max(1, math.ceil(p["qty"] / 5))
            qty, proceeds = pf.sell(d, ticker, qty, px, trigger_reason or reason)
            if qty <= 0:
                return TradeResult.no_op()
            return TradeResult(executed=True, spent_krw=proceeds if blw.is_kr(ticker) else 0.0,
                               spent_usd=0.0 if blw.is_kr(ticker) else proceeds)

    def fake_cfg(macro=None):
        return ExecutionConfig(
            buy_max=SettingsService.get_int("STRATEGY_BUY_THRESHOLD", 30),
            sell_min=SettingsService.get_int("STRATEGY_SELL_THRESHOLD", 70),
            take_profit_pct=PositionService._get_take_profit_pct_by_regime(macro),
            stop_loss_pct=PositionService._get_stop_loss_pct_by_regime(macro),
            add_rsi_limit=60.0, add_score_limit=55, exchange_rate=fx,
            today=current_macro["day"].strftime("%Y-%m-%d"),
        )

    patches = [
        patch.object(SettingsService, "get_setting", side_effect=stub),
        patch.object(PositionService, "_get_mode_for_market", return_value="top100"),
        patch.object(PositionService, "_load_execution_config", side_effect=fake_cfg),
        patch.object(TradeExecutorService, "_execute_trade_v2", side_effect=fake_trade),
        patch("services.market.market_hour_service.MarketHourService.minutes_to_close", return_value=1e9),
        patch("repositories.trade_history_repo.TradeHistoryRepo.get_first_buy_date", return_value=first_buy_date),
        patch("services.market.market_data_service.MarketDataService.get_state", side_effect=lambda t: current_macro["states"].get(t)),
    ]
    for p_ in patches:
        p_.start()
    try:
        equity_curve = []
        for d in days:
            CrashGuardService._status_cache = None
            states, prices = {}, {}
            for t in tickers:
                df = data.get(blw.ysym(t))
                if df is None or d not in df.index:
                    continue
                st = build_state(t, df.loc[:d], dcf_map.get(t, 0.0))
                if st is None:
                    continue
                states[t] = st; prices[t] = st.current_price
            k = idx_changes(kospi, d); s_ = idx_changes(spx, d); v = idx_changes(vixdf, d)
            vix_now = float(vixdf.loc[:d, "Close"].dropna().iloc[-1]) if vixdf is not None and len(vixdf.loc[:d]) else 20.0
            macro = MacroDataSnapshot(
                vix=vix_now, fear_greed=args.fng, exchange_rate=fx,
                market_regime=MarketRegimeSchema(status=args.regime, regime_score=args.regime_score),
                kospi_change_1d=k.get(1), kospi_change_5d=k.get(5), kospi_change_20d=k.get(20), kospi_change_90d=k.get(90),
                spx_change_1d=s_.get(1), spx_change_5d=s_.get(5), spx_change_20d=s_.get(20), spx_change_90d=s_.get(90),
                vix_change_1d=v.get(1),
            )
            current_macro.update(day=d, states=states, macro=macro)
            if not states:
                print(f"  ⚠️ {d.date()}: 지표 계산 가능한 종목 없음 (≥15봉 필요) — lookback 을 늘리세요")
            holdings = pf.holdings(prices)
            hmap = {h.ticker: h for h in holdings}
            kr_total, us_total_krw = pf.totals(prices)
            tgt_kr = TradeExecutorService._get_target_cash_ratio("KR", args.regime.upper(), macro)
            tgt_us = TradeExecutorService._get_target_cash_ratio("US", args.regime.upper(), macro)
            signals = []
            for t, st in states.items():
                h = hmap.get(t)
                mt = kr_total if blw.is_kr(t) else us_total_krw
                if h is None:
                    # 신규 후보: 신선도/현금 하드게이트 대신 단순 현금비율 게이트만 재현
                    avail = pf.cash_krw if blw.is_kr(t) else pf.usd_cash * fx
                    if mt > 0 and avail / mt < (tgt_kr if blw.is_kr(t) else tgt_us):
                        continue
                score, reasons, bd = SignalService.calculate_score(
                    t, st, h, macro, user_state, pf.cash_krw, market_cash_ratio=(tgt_kr if blw.is_kr(t) else tgt_us),
                    market_total_krw=mt)
                signals.append(SignalSchema(ticker=t, state=st, holding=h, score=score, reasons=reasons,
                                            stock_score=bd.get("stock_score"), market_adj=int(bd.get("market_adj", 0) or 0)))
            PositionService._execute_collected_signals(
                "sim", signals, holdings, kr_total, us_total_krw, pf.cash_krw, tgt_kr, tgt_us, macro,
                user_state=user_state, usd_cash=pf.usd_cash)
            kr_total, us_total_krw = pf.totals(prices)
            equity_curve.append((d, kr_total + us_total_krw))
    finally:
        for p_ in patches:
            p_.stop()

    last_prices = {}
    for t in pf.pos:
        df = data.get(blw.ysym(t))
        if df is not None and len(df):
            last_prices[t] = float(df["Close"].dropna().iloc[-1])
    realized = sum(a[6] for a in pf.actions if a[2] == "SELL")
    unreal = sum(((last_prices.get(t, p["buy"]) - p["buy"]) * p["qty"]) * (1 if blw.is_kr(t) else fx)
                 for t, p in pf.pos.items() if p["qty"] > 0)
    return dict(mode=mode, realized=realized, unrealized=unreal, total=realized + unreal, actions=pf.actions,
                cash=pf.cash_krw + pf.usd_cash * fx, equity=equity_curve, user_state=user_state)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(ROOT, "data", "stock_advisor.db"))
    ap.add_argument("--user", default="sean")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--lookback", type=int, default=260, help="지표 계산용 과거 캘린더일")
    ap.add_argument("--end", default=None)
    ap.add_argument("--fx", type=float, default=1350.0)
    ap.add_argument("--cash", type=float, default=None)
    ap.add_argument("--usd-cash", type=float, default=0.0)
    ap.add_argument("--mode", choices=["uptrend", "legacy", "both"], default="both")
    ap.add_argument("--extra", nargs="*", default=[], help="유니버스 추가 티커 (신규 매수 후보)")
    ap.add_argument("--fng", type=float, default=50.0)
    ap.add_argument("--regime", default="Neutral")
    ap.add_argument("--regime-score", type=int, default=50)
    ap.add_argument("--dcf-from-db", action="store_true")
    ap.add_argument("--assume-held-days", type=int, default=0, help="상대약세 판정용 가상 보유일 (0=판정 안 함)")
    ap.add_argument("--csv-dir", default=None)
    ap.add_argument("--synthetic", action="store_true")
    args = ap.parse_args()

    end = datetime.strptime(args.end, "%Y-%m-%d") if args.end else datetime.now()
    end = end.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    start = end - timedelta(days=args.days + 1)
    lookback = start - timedelta(days=args.lookback)

    holdings, cash_krw = blw.load_holdings(args.db, args.user)
    if args.cash is not None:
        cash_krw = args.cash
    tickers = list(holdings.ticker) + list(args.extra)
    symbols = [blw.ysym(t) for t in tickers] + ["^KS11", "^GSPC", "^VIX"]
    print(f"보유 {len(holdings)}종목 (+extra {len(args.extra)}), KRW {cash_krw:,.0f}원, USD ${args.usd_cash:,.2f}  "
          f"기간 {start.date()} ~ {(end - timedelta(days=1)).date()}")
    if args.synthetic:
        # 지표(RSI14/EMA/ATR) 계산에 ≥15봉 필요 → lookback 만큼 앞당겨 생성 (급락 구간은 생성 창의 중간)
        data = blw.synthetic_data(holdings, start - timedelta(days=max(60, args.lookback // 3)), end)
        print("⚠️ 합성 데이터 모드 — 스크립트 검증용")
    elif args.csv_dir:
        data = blw.load_csv_dir(symbols, args.csv_dir)
    else:
        data = blw.fetch_yf(symbols, lookback.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
    missing = [s for s in symbols if s not in data]
    if missing:
        print(f"⚠️ 데이터 없음: {missing}")
    all_days = sorted({d for s, df in data.items() if not s.startswith("^") for d in df.index})
    days = [d for d in all_days if start <= d < end]
    if not days:
        print("❌ 기간 내 거래일 없음"); sys.exit(1)
    dcf_map = load_dcf_from_db(args.db, tickers) if args.dcf_from_db else {}
    print(f"거래일 {len(days)}일, DCF 제공 {len(dcf_map)}종목")

    modes = ["uptrend", "legacy"] if args.mode == "both" else [args.mode]
    results = [run(m, holdings, cash_krw, args.usd_cash, args.fx, data, days, args, dcf_map) for m in modes]

    def f(v): return f"{v:>+16,.0f}"
    print("\n" + "=" * 70)
    print(f"{'항목':<22}" + "".join(f"{r['mode']:>22}" for r in results))
    print("-" * 70)
    for label, key in (("실현 P&L", "realized"), ("미실현 P&L", "unrealized"), ("총 P&L", "total"), ("기말 현금(KRW환산)", "cash")):
        print(f"{label:<22}" + "".join(f"{f(r[key]):>22}" for r in results))
    print(f"{'매수/매도 건수':<22}" + "".join(
        f"{str(sum(1 for a in r['actions'] if a[2] == 'BUY')) + '/' + str(sum(1 for a in r['actions'] if a[2] == 'SELL')):>22}" for r in results))
    for r in results:
        from collections import Counter
        c = Counter(a[5] for a in r["actions"])
        print(f"\n[{r['mode']}] trigger 별 건수: {dict(c)}")
        for a in r["actions"][:40]:
            pnl = f" pnl {a[6]:+,.0f}" if a[2] == "SELL" else ""
            print(f"  {a[0].strftime('%Y-%m-%d')} {a[1]:<8} {a[2]:<4} {a[3]:>5}주 @ {a[4]:>12,.2f}  {a[5]}{pnl}")
        if len(r["actions"]) > 40:
            print(f"  ... 외 {len(r['actions']) - 40}건")
        if r["equity"]:
            eq = [e for _, e in r["equity"]]
            peak, mdd = eq[0], 0.0
            for e in eq:
                peak = max(peak, e); mdd = min(mdd, (e - peak) / peak * 100 if peak else 0)
            print(f"  평가자산 {eq[0]:,.0f} → {eq[-1]:,.0f} ({(eq[-1] / eq[0] - 1) * 100:+.2f}%), MDD {mdd:.2f}%")


if __name__ == "__main__":
    main()
