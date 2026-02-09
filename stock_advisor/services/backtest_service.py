import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta

class BacktestService:
    @classmethod
    def run_rsi_backtest(cls, ticker: str, years: int = 3):
        """RSI 전략 백테스팅 (고정비중 vs 켈리베팅 비교)"""
        print(f"🚀 Running backtest for {ticker} (Past {years} years)...")
        
        # 1. 데이터 가져오기
        end_date = datetime.now()
        start_date = end_date - timedelta(days=years*365)
        df = yf.download(ticker, start=start_date, end=end_date)
        
        if df.empty:
            return "데이터 오류", []

        # 2. 지표 계산
        delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))

        # 3. 전략 A: All-in (100% 몰빵)
        res_A = cls._simulate(df, strategy="all_in")
        
        # 4. 전략 B: Risk Managed (30% 고정 비중)
        # (켈리는 승률을 미리 알아야 하므로, 현실적으로 '분산투자'를 가정)
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
            close_series = df['Close'].iloc[i]
            rsi_series = df['RSI'].iloc[i]
            
            # Series -> scalar 안전 변환
            price = float(close_series.iloc[0]) if isinstance(close_series, pd.Series) else float(close_series)
            rsi = float(rsi_series.iloc[0]) if isinstance(rsi_series, pd.Series) else float(rsi_series)
            
            date = df.index[i]
            
            # 매수 (RSI < 30)
            if rsi < 30 and cash > 0:
                invest_amount = 0
                if strategy == "all_in":
                    invest_amount = cash
                elif strategy == "fixed_30":
                    # 전체 자산(현금+주식)의 30%까지만 매수
                    total_equity = cash + (shares * price)
                    target_exposure = total_equity * 0.3
                    current_exposure = shares * price
                    if target_exposure > current_exposure:
                        invest_amount = target_exposure - current_exposure
                        invest_amount = min(invest_amount, cash) # 현금 한도 내에서
                
                if invest_amount > 10: # 최소 주문금액
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
            
            # 일별 자산 추적
            total_val = cash + (shares * price)
            equity_curve.append(total_val)

        final_val = cash + (shares * float(df['Close'].iloc[-1].iloc[0] if isinstance(df['Close'].iloc[-1], pd.Series) else df['Close'].iloc[-1]))
        total_ret = (final_val - initial_balance) / initial_balance * 100
        
        # MDD 계산
        equity_series = pd.Series(equity_curve)
        roll_max = equity_series.cummax()
        drawdown = equity_series / roll_max - 1.0
        mdd = drawdown.min() * 100

        return {
            "initial": initial_balance,
            "final": round(final_val, 2),
            "return_pct": round(total_ret, 2),
            "mdd": round(mdd, 2),
            "trade_count": len(trades)
        }
