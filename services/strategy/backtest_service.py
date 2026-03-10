import pandas as pd
import numpy as np
import yfinance as yf
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from services.market.data_service import DataService, KR_FALLBACK_TICKERS
from services.analysis.indicator_service import IndicatorService

# Backtest constants
RSI_PERIOD = 14
BACKTEST_INITIAL_BALANCE = 10000.0
BACKTEST_MIN_TRADE_AMOUNT = 10.0
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 60
FIXED_RATIO_STRATEGY = 0.3


@dataclass
class BtHolding:
    """Single holding in backtest portfolio."""
    ticker: str
    shares: int
    avg_price: float


@dataclass
class BtPortfolio:
    """Portfolio state during backtest simulation."""
    cash: float
    holdings: Dict[str, BtHolding] = field(default_factory=dict)

    def stock_value(self, prices: Dict[str, float]) -> float:
        return sum(
            h.shares * prices.get(h.ticker, 0.0)
            for h in self.holdings.values()
        )

    def total_assets(self, prices: Dict[str, float]) -> float:
        return self.cash + self.stock_value(prices)

    def cash_ratio(self, prices: Dict[str, float]) -> float:
        total = self.total_assets(prices)
        return self.cash / total if total > 0 else 1.0


class BacktestService:
    """RSI-based backtest service."""

    @classmethod
    def run_rsi_backtest(cls, ticker: str, years: int = 3):
        """RSI strategy backtesting (using DataService)."""
        print(f"📊 Running backtest for {ticker} (Past {years} years)...")
        df = DataService.get_price_history(ticker, days=years * 365)
        if df.empty:
            return "data_error", []
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
        """Calculate investment amount per buy strategy (all_in / fixed_30)."""
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
        """Calculate maximum drawdown (MDD %) from equity curve list."""
        equity_series = pd.Series(equity_curve)
        if equity_series.empty:
            return 0.0
        roll_max = equity_series.cummax()
        return float((equity_series / roll_max - 1.0).min() * 100)

    @classmethod
    def _load_backtest_data(cls, tickers: List[str], years: int) -> Dict[str, pd.DataFrame]:
        """Load historical price data via yfinance for multiple tickers."""
        end = datetime.now()
        start = end - timedelta(days=years * 365)
        result: Dict[str, pd.DataFrame] = {}
        yf_tickers = []
        ticker_map = {}
        for t in tickers:
            if t.isdigit():
                yf_t = f"{t}.KS"
            else:
                yf_t = t
            yf_tickers.append(yf_t)
            ticker_map[yf_t] = t

        data = yf.download(yf_tickers, start=start, end=end, progress=False, group_by="ticker")
        if len(yf_tickers) == 1:
            yf_t = yf_tickers[0]
            orig = ticker_map[yf_t]
            df = data[["Close"]].dropna()
            if not df.empty:
                result[orig] = df
        else:
            for yf_t, orig in ticker_map.items():
                if yf_t in data.columns.get_level_values(0):
                    df = data[yf_t][["Close"]].dropna()
                    if not df.empty:
                        result[orig] = df
        return result

    @classmethod
    def run_portfolio_backtest(
        cls,
        tickers: Optional[List[str]] = None,
        years: int = 2,
        initial_capital: float = 10_000_000,
        position_pct: float = 0.05,
        target_cash_ratio: float = 0.40,
        take_profit_pct: float = 0.03,
        stop_loss_pct: float = -0.08,
        rsi_oversold: int = 30,
        rsi_overbought: int = 70,
    ) -> dict:
        """Run portfolio-level backtest with multi-ticker RSI strategy."""
        if tickers is None:
            tickers = list(KR_FALLBACK_TICKERS)

        # Load data and compute RSI
        price_data = cls._load_backtest_data(tickers, years)
        if not price_data:
            return {"error": "No price data available for given tickers"}

        rsi_data: Dict[str, pd.Series] = {}
        for t, df in price_data.items():
            rsi_data[t] = IndicatorService.compute_rsi_series(df["Close"])

        # Build common date index
        all_dates = sorted(
            set().union(*(df.index.tolist() for df in price_data.values()))
        )

        portfolio = BtPortfolio(cash=initial_capital)
        equity_curve = []
        cash_ratio_curve = []
        trades = []
        wins = 0
        losses = 0

        for date in all_dates:
            # Get today's prices for all tickers
            prices: Dict[str, float] = {}
            rsis: Dict[str, float] = {}
            for t, df in price_data.items():
                if date in df.index:
                    prices[t] = float(df.loc[date, "Close"])
                    if date in rsi_data[t].index:
                        rsi_val = rsi_data[t].loc[date]
                        if not np.isnan(rsi_val):
                            rsis[t] = float(rsi_val)

            if not prices:
                continue

            # Phase 1 - Sell (check existing holdings)
            for t in list(portfolio.holdings.keys()):
                if t not in prices:
                    continue
                h = portfolio.holdings[t]
                price = prices[t]
                profit_pct = (price - h.avg_price) / h.avg_price

                sell_reason = None
                if profit_pct <= stop_loss_pct:
                    sell_reason = "stop_loss"
                elif profit_pct >= take_profit_pct:
                    sell_reason = "take_profit"
                elif t in rsis and rsis[t] > rsi_overbought:
                    sell_reason = "rsi_overbought"

                if sell_reason:
                    proceeds = h.shares * price
                    portfolio.cash += proceeds
                    if profit_pct > 0:
                        wins += 1
                    else:
                        losses += 1
                    trades.append({
                        "date": str(date.date()) if hasattr(date, 'date') else str(date),
                        "ticker": t,
                        "action": "SELL",
                        "reason": sell_reason,
                        "shares": h.shares,
                        "price": round(price, 2),
                        "profit_pct": round(profit_pct * 100, 2),
                    })
                    del portfolio.holdings[t]

            # Phase 2 - Buy (after sells complete)
            for t in tickers:
                if t not in prices or t not in rsis:
                    continue
                if t in portfolio.holdings:
                    continue
                if rsis[t] >= rsi_oversold:
                    continue

                total = portfolio.total_assets(prices)
                current_cash_ratio = portfolio.cash_ratio(prices)
                if current_cash_ratio <= target_cash_ratio:
                    continue

                buy_budget = min(
                    total * position_pct,
                    portfolio.cash - total * target_cash_ratio,
                )
                if buy_budget <= 0 or buy_budget > portfolio.cash:
                    continue

                price = prices[t]
                shares = int(buy_budget / price)
                if shares <= 0:
                    continue

                cost = shares * price
                portfolio.cash -= cost
                portfolio.holdings[t] = BtHolding(ticker=t, shares=shares, avg_price=price)
                trades.append({
                    "date": str(date.date()) if hasattr(date, 'date') else str(date),
                    "ticker": t,
                    "action": "BUY",
                    "reason": "rsi_oversold",
                    "shares": shares,
                    "price": round(price, 2),
                    "cost": round(cost, 2),
                })

            total_val = portfolio.total_assets(prices)
            equity_curve.append(round(total_val, 0))
            cash_ratio_curve.append(round(portfolio.cash_ratio(prices) * 100, 2))

        # Final metrics
        final_value = equity_curve[-1] if equity_curve else initial_capital
        total_return_pct = (final_value - initial_capital) / initial_capital * 100
        mdd_pct = cls._calc_mdd_from_equity(equity_curve)
        total_trades = len(trades)
        total_closed = wins + losses
        win_rate = (wins / total_closed * 100) if total_closed > 0 else 0.0

        # Sharpe ratio (annualized, daily returns)
        sharpe = 0.0
        if len(equity_curve) > 1:
            eq = pd.Series(equity_curve, dtype=float)
            daily_returns = eq.pct_change().dropna()
            if daily_returns.std() > 0:
                sharpe = float(daily_returns.mean() / daily_returns.std() * np.sqrt(252))

        # Max simultaneous holdings
        max_holdings = 0
        current_count = 0
        for tr in trades:
            if tr["action"] == "BUY":
                current_count += 1
            else:
                current_count -= 1
            max_holdings = max(max_holdings, current_count)

        cash_never_negative = all(
            cr >= 0 for cr in cash_ratio_curve
        ) and portfolio.cash >= 0

        return {
            "config": {
                "tickers": tickers,
                "years": years,
                "initial_capital": initial_capital,
                "position_pct": position_pct,
                "target_cash_ratio": target_cash_ratio,
                "take_profit_pct": take_profit_pct,
                "stop_loss_pct": stop_loss_pct,
                "rsi_oversold": rsi_oversold,
                "rsi_overbought": rsi_overbought,
            },
            "results": {
                "final_value": round(final_value, 0),
                "total_return_pct": round(total_return_pct, 2),
                "mdd_pct": round(mdd_pct, 2),
                "sharpe_ratio": round(sharpe, 2),
                "total_trades": total_trades,
                "win_rate_pct": round(win_rate, 1),
                "avg_cash_ratio_pct": round(np.mean(cash_ratio_curve), 1) if cash_ratio_curve else 0.0,
                "min_cash_ratio_pct": round(min(cash_ratio_curve), 1) if cash_ratio_curve else 0.0,
                "max_simultaneous_holdings": max_holdings,
                "cash_never_negative": cash_never_negative,
            },
            "equity_curve": equity_curve,
            "cash_ratio_curve": cash_ratio_curve,
            "trades": trades,
        }

    @staticmethod
    def _simulate(df, strategy="all_in"):
        """RSI-based trading simulation (all_in or fixed_30)."""
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
