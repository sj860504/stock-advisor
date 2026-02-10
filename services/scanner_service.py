import yfinance as yf
import pandas as pd
from .data_service import DataService
import time

class ScannerService:
    @classmethod
    def scan_market(cls, limit: int = 50) -> dict:
        """
        S&P 500 종목 중 기회 포착 (Limit으로 스캔 개수 제한 가능)
        """
        tickers = DataService.get_sp500_tickers()
        print(f"🔍 Scanning {min(limit, len(tickers))} stocks from S&P 500...")
        
        opportunities = {
            "oversold_bluechip": [], # 과매도 우량주
            "trend_breakout": [],    # 추세 돌파
            "analyst_strong_buy": [] # 기관 강력 매수
        }
        
        count = 0
        for ticker in tickers:
            if count >= limit: break
            
            try:
                stock = yf.Ticker(ticker)
                
                # 1. 기본 정보 (Fast Info)
                price = stock.fast_info.last_price
                if not price: continue
                
                # 2. 기술적 지표 (History)
                hist = stock.history(period="1y")
                if hist.empty: continue
                
                # RSI 계산
                delta = hist['Close'].diff()
                gain = (delta.where(delta > 0, 0)).rolling(14).mean()
                loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
                rs = gain / loss
                rsi = (100 - (100 / (1 + rs))).iloc[-1]
                
                # EMA 계산
                ema20 = hist['Close'].ewm(span=20, adjust=False).mean().iloc[-1]
                ema200 = hist['Close'].ewm(span=200, adjust=False).mean().iloc[-1]
                
                prev_close = hist['Close'].iloc[-2]
                
                # 3. 펀더멘털 & 기관 의견 (Info - 느림, 필요시 호출)
                # (속도를 위해 조건 만족 시에만 호출)
                
                # [조건 A] 과매도 우량주 (RSI < 30)
                if rsi < 30:
                    info = stock.info
                    pbr = info.get('priceToBook')
                    market_cap = info.get('marketCap', 0)
                    
                    # 시총 100조 이상 & PBR 5 이하 (거품 없는 우량주)
                    if market_cap > 100_000_000_000 and pbr and pbr < 5:
                        opportunities["oversold_bluechip"].append({
                            "ticker": ticker,
                            "price": price,
                            "rsi": round(rsi, 1),
                            "pbr": round(pbr, 2),
                            "name": info.get('shortName')
                        })

                # [조건 B] 추세 돌파 (EMA 200 골든크로스)
                # 어제는 EMA200 아래였는데 오늘은 뚫었음
                if prev_close < ema200 and price > ema200:
                    vol_ratio = 1.0 # 거래량 분석 추가 가능
                    opportunities["trend_breakout"].append({
                        "ticker": ticker,
                        "price": price,
                        "ema200": round(ema200, 2),
                        "change": round(((price - prev_close)/prev_close)*100, 1)
                    })
                    
                # [조건 C] 기관 강력 매수 (목표가 괴리율 > 30%)
                # RSI가 너무 높지 않은 상태에서(70 미만)
                if rsi < 70:
                    # info는 위에서 호출 안했으면 여기서 호출
                    if 'info' not in locals(): info = stock.info
                    
                    target = info.get('targetMeanPrice')
                    if target and target > price * 1.3: # 30% 이상 상승 여력
                        upside = ((target - price) / price) * 100
                        opportunities["analyst_strong_buy"].append({
                            "ticker": ticker,
                            "price": price,
                            "target": target,
                            "upside": round(upside, 1),
                            "name": info.get('shortName')
                        })
                
                print(".", end="", flush=True)
                count += 1
                
            except Exception as e:
                # print(f"x ({ticker})", end="", flush=True)
                continue
                
        print("\n✅ Scan complete.")
        return opportunities
