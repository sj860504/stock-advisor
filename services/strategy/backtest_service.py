import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from services.market.data_service import DataService
from services.analysis.indicator_service import IndicatorService

class BacktestService:
    @classmethod
    def run_rsi_backtest(cls, ticker: str, years: int = 3):
        """RSI 전략 백테스팅 (DataService 사용)"""
        print(f"📊 Running backtest for {ticker} (Past {years} years)...")
        
        # 1. 데이터 가져오기 (DataService 사용)
        df = DataService.get_price_history(ticker, days=years*365)
        
        if df.empty:
            return "데이터 오류", []

        # 2. 지표 계산
        indicators = IndicatorService.get_latest_indicators(df['Close'])
        # IndicatorService는 최신 값만 주므로, 전체 히스토리 RSI가 필요함
        # 직접 계산 로직 포함 (또는 IndicatorService 확장 가능)
        delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))

        # 3. 전략 A: All-in (100% 몰빵)
        res_A = cls._simulate(df, strategy="all_in")
        
        # 4. 전략 B: Risk Managed (30% 고정 비중)
        res_B = cls._simulate(df, strategy="fixed_30")

        return {"A": res_A, "B": res_B}

    @staticmethod
    def _simulate(df, strategy="all_in"):
        initial_balance = 10000.0
        cash = initial_balance
        shares = 0
        trades = []
        equity_curve = []
        
        for i in range(1, len(df)):
            price = float(df['Close'].iloc[i])
            rsi = float(df['RSI'].iloc[i])
            if np.isnan(rsi): continue
            
            date = df.index[i]
            
            # 매수 (RSI < 30)
            if rsi < 30 and cash > 0:
                invest_amount = 0
                if strategy == "all_in":
                    invest_amount = cash
                elif strategy == "fixed_30":
                    total_equity = cash + (shares * price)
                    target_exposure = total_equity * 0.3
                    current_exposure = shares * price
                    if target_exposure > current_exposure:
                        invest_amount = target_exposure - current_exposure
                        invest_amount = min(invest_amount, cash)
                
                if invest_amount > 10:
                    buy_shares = invest_amount / price
                    shares += buy_shares
                    cash -= invest_amount
                    trades.append({"type": "BUY", "date": date, "price": price, "rsi": rsi})

            # 매도 (RSI > 60)
            elif rsi > 60 and shares > 0:
                sell_amount = shares * price
                cash += sell_amount
                shares = 0
                trades.append({"type": "SELL", "date": date, "price": price, "rsi": rsi})
            
            # 자산 추적
            total_val = cash + (shares * price)
            equity_curve.append(total_val)

        final_val = cash + (shares * float(df['Close'].iloc[-1]))
        total_ret = (final_val - initial_balance) / initial_balance * 100
        
        # MDD 계산
        equity_series = pd.Series(equity_curve)
        if not equity_series.empty:
            roll_max = equity_series.cummax()
            drawdown = equity_series / roll_max - 1.0
            mdd = drawdown.min() * 100
        else:
            mdd = 0

        return {
            "initial": initial_balance,
            "final": round(final_val, 2),
            "return_pct": round(total_ret, 2),
            "mdd": round(mdd, 2),
            "trade_count": len(trades)
        }
