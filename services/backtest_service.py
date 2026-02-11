import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta

class BacktestService:
    @classmethod
    def run_rsi_backtest(cls, ticker: str, years: int = 3):
        """RSI ?꾨왂 諛깊뀒?ㅽ똿 (怨좎젙鍮꾩쨷 vs 耳덈━踰좏똿 鍮꾧탳)"""
        print(f"?? Running backtest for {ticker} (Past {years} years)...")
        
        # 1. ?곗씠??媛?몄삤湲?
        end_date = datetime.now()
        start_date = end_date - timedelta(days=years*365)
        df = yf.download(ticker, start=start_date, end=end_date)
        
        if df.empty:
            return "?곗씠???ㅻ쪟", []

        # 2. 吏??怨꾩궛
        delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))

        # 3. ?꾨왂 A: All-in (100% 紐곕뭇)
        res_A = cls._simulate(df, strategy="all_in")
        
        # 4. ?꾨왂 B: Risk Managed (30% 怨좎젙 鍮꾩쨷)
        # (耳덈━???밸쪧??誘몃━ ?뚯븘???섎?濡? ?꾩떎?곸쑝濡?'遺꾩궛?ъ옄'瑜?媛??
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
            
            # Series -> scalar ?덉쟾 蹂??
            price = float(close_series.iloc[0]) if isinstance(close_series, pd.Series) else float(close_series)
            rsi = float(rsi_series.iloc[0]) if isinstance(rsi_series, pd.Series) else float(rsi_series)
            
            date = df.index[i]
            
            # 留ㅼ닔 (RSI < 30)
            if rsi < 30 and cash > 0:
                invest_amount = 0
                if strategy == "all_in":
                    invest_amount = cash
                elif strategy == "fixed_30":
                    # ?꾩껜 ?먯궛(?꾧툑+二쇱떇)??30%源뚯?留?留ㅼ닔
                    total_equity = cash + (shares * price)
                    target_exposure = total_equity * 0.3
                    current_exposure = shares * price
                    if target_exposure > current_exposure:
                        invest_amount = target_exposure - current_exposure
                        invest_amount = min(invest_amount, cash) # ?꾧툑 ?쒕룄 ?댁뿉??
                
                if invest_amount > 10: # 理쒖냼 二쇰Ц湲덉븸
                    buy_shares = invest_amount / price
                    shares += buy_shares
                    cash -= invest_amount
                    trades.append({"type": "BUY", "date": date, "price": price, "rsi": rsi})

            # 留ㅻ룄 (RSI > 60)
            elif rsi > 60 and shares > 0:
                sell_amount = shares * price
                cash += sell_amount
                shares = 0
                trades.append({"type": "SELL", "date": date, "price": price, "rsi": rsi})
            
            # ?쇰퀎 ?먯궛 異붿쟻
            total_val = cash + (shares * price)
            equity_curve.append(total_val)

        final_val = cash + (shares * float(df['Close'].iloc[-1].iloc[0] if isinstance(df['Close'].iloc[-1], pd.Series) else df['Close'].iloc[-1]))
        total_ret = (final_val - initial_balance) / initial_balance * 100
        
        # MDD 怨꾩궛
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
