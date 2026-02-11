import yfinance as yf
import pandas as pd
from .data_service import DataService
import time

class ScannerService:
    @classmethod
    def scan_market(cls, limit: int = 50) -> dict:
        """
        S&P 500 醫낅ぉ 以?湲고쉶 ?ъ갑 (Limit?쇰줈 ?ㅼ틪 媛쒖닔 ?쒗븳 媛??
        """
        tickers = DataService.get_sp500_tickers()
        print(f"?뵇 Scanning {min(limit, len(tickers))} stocks from S&P 500...")
        
        opportunities = {
            "oversold_bluechip": [], # 怨쇰ℓ???곕웾二?
            "trend_breakout": [],    # 異붿꽭 ?뚰뙆
            "analyst_strong_buy": [] # 湲곌? 媛뺣젰 留ㅼ닔
        }
        
        count = 0
        for ticker in tickers:
            if count >= limit: break
            
            try:
                stock = yf.Ticker(ticker)
                
                # 1. 湲곕낯 ?뺣낫 (Fast Info)
                price = stock.fast_info.last_price
                if not price: continue
                
                # 2. 湲곗닠??吏??(History)
                hist = stock.history(period="1y")
                if hist.empty: continue
                
                # RSI 怨꾩궛
                delta = hist['Close'].diff()
                gain = (delta.where(delta > 0, 0)).rolling(14).mean()
                loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
                rs = gain / loss
                rsi = (100 - (100 / (1 + rs))).iloc[-1]
                
                # EMA 怨꾩궛
                ema20 = hist['Close'].ewm(span=20, adjust=False).mean().iloc[-1]
                ema200 = hist['Close'].ewm(span=200, adjust=False).mean().iloc[-1]
                
                prev_close = hist['Close'].iloc[-2]
                
                # 3. ??붾찘??& 湲곌? ?섍껄 (Info - ?먮┝, ?꾩슂???몄텧)
                # (?띾룄瑜??꾪빐 議곌굔 留뚯” ?쒖뿉留??몄텧)
                
                # [議곌굔 A] 怨쇰ℓ???곕웾二?(RSI < 30)
                if rsi < 30:
                    info = stock.info
                    pbr = info.get('priceToBook')
                    market_cap = info.get('marketCap', 0)
                    
                    # ?쒖킑 100議??댁긽 & PBR 5 ?댄븯 (嫄고뭹 ?녿뒗 ?곕웾二?
                    if market_cap > 100_000_000_000 and pbr and pbr < 5:
                        opportunities["oversold_bluechip"].append({
                            "ticker": ticker,
                            "price": price,
                            "rsi": round(rsi, 1),
                            "pbr": round(pbr, 2),
                            "name": info.get('shortName')
                        })

                # [議곌굔 B] 異붿꽭 ?뚰뙆 (EMA 200 怨⑤뱺?щ줈??
                # ?댁젣??EMA200 ?꾨옒??붾뜲 ?ㅻ뒛? ?レ뿀??
                if prev_close < ema200 and price > ema200:
                    vol_ratio = 1.0 # 嫄곕옒??遺꾩꽍 異붽? 媛??
                    opportunities["trend_breakout"].append({
                        "ticker": ticker,
                        "price": price,
                        "ema200": round(ema200, 2),
                        "change": round(((price - prev_close)/prev_close)*100, 1)
                    })
                    
                # [議곌굔 C] 湲곌? 媛뺣젰 留ㅼ닔 (紐⑺몴媛 愿대━??> 30%)
                # RSI媛 ?덈Т ?믪? ?딆? ?곹깭?먯꽌(70 誘몃쭔)
                if rsi < 70:
                    # info???꾩뿉???몄텧 ?덊뻽?쇰㈃ ?ш린???몄텧
                    if 'info' not in locals(): info = stock.info
                    
                    target = info.get('targetMeanPrice')
                    if target and target > price * 1.3: # 30% ?댁긽 ?곸듅 ?щ젰
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
                
        print("\n??Scan complete.")
        return opportunities
