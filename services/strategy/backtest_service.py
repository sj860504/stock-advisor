import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from services.market.data_service import DataService
from services.analysis.indicator_service import IndicatorService

# 백테스트 상수
RSI_PERIOD = 14
BACKTEST_INITIAL_BALANCE = 10000.0
BACKTEST_MIN_TRADE_AMOUNT = 10.0
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 60
FIXED_RATIO_STRATEGY = 0.3


class BacktestService:
    """RSI 기반 백테스트 서비스"""

    @classmethod
    def run_rsi_backtest(cls, ticker: str, years: int = 3):
        """RSI 전략 백테스팅 (DataService 사용)"""
        print(f"📊 Running backtest for {ticker} (Past {years} years)...")
        df = DataService.get_price_history(ticker, days=years * 365)
        if df.empty:
            return "데이터 오류", []
        close_series = df["Close"]
        delta = close_series.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=RSI_PERIOD).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=RSI_PERIOD).mean()
        rs = gain / loss
        df["RSI"] = 100 - (100 / (1 + rs))
        result_all_in = cls._simulate(df, strategy="all_in")
        result_fixed_30 = cls._simulate(df, strategy="fixed_30")
        return {"A": result_all_in, "B": result_fixed_30}

    @staticmethod
    def _calc_invest_amount(strategy: str, cash: float, shares: float, price: float) -> float:
        """매수 전략별 투자금 계산 (all_in / fixed_30)."""
        if strategy == "all_in":
            return cash
        if strategy == "fixed_30":
            total_equity     = cash + shares * price
            target_exposure  = total_equity * FIXED_RATIO_STRATEGY
            current_exposure = shares * price
            if target_exposure > current_exposure:
                return min(target_exposure - current_exposure, cash)
        return 0.0

    @staticmethod
    def _calc_mdd_from_equity(equity_curve: list) -> float:
        """equity curve 리스트에서 최대낙폭(MDD %)을 계산합니다."""
        equity_series = pd.Series(equity_curve)
        if equity_series.empty:
            return 0.0
        roll_max = equity_series.cummax()
        return float((equity_series / roll_max - 1.0).min() * 100)

    @staticmethod
    def _simulate(df, strategy="all_in"):
        """RSI 기반 매매 시뮬레이션 (all_in 또는 fixed_30)"""
        initial_balance = BACKTEST_INITIAL_BALANCE
        cash   = initial_balance
        shares = 0.0
        trades = []
        equity_curve = []

        for i in range(1, len(df)):
            price = float(df["Close"].iloc[i])
            rsi   = float(df["RSI"].iloc[i])
            if np.isnan(rsi):
                continue
            date = df.index[i]

            if rsi < RSI_OVERSOLD and cash > 0:
                invest_amount = BacktestService._calc_invest_amount(strategy, cash, shares, price)
                if invest_amount > BACKTEST_MIN_TRADE_AMOUNT:
                    shares += invest_amount / price
                    cash   -= invest_amount
                    trades.append({"type": "BUY", "date": date, "price": price, "rsi": rsi})
            elif rsi > RSI_OVERBOUGHT and shares > 0:
                cash  += shares * price
                shares = 0.0
                trades.append({"type": "SELL", "date": date, "price": price, "rsi": rsi})

            equity_curve.append(cash + shares * price)

        final_val = cash + shares * float(df["Close"].iloc[-1])
        total_ret = (final_val - initial_balance) / initial_balance * 100
        return {
            "initial":     initial_balance,
            "final":       round(final_val, 2),
            "return_pct":  round(total_ret, 2),
            "mdd":         round(BacktestService._calc_mdd_from_equity(equity_curve), 2),
            "trade_count": len(trades),
        }
