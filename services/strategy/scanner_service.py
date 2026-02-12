import pandas as pd
import time
from services.market.data_service import DataService
from services.kis.kis_service import KisService
from services.kis.fetch.kis_fetcher import KisFetcher
from services.analysis.indicator_service import IndicatorService
from utils.logger import get_logger

logger = get_logger("scanner_service")

class ScannerService:
    @classmethod
    def scan_market(cls, limit: int = 20) -> dict:
        """
        주요 종목 중에서 기회 포착 (미국 주식 중심, KIS API 사용)
        """
        # KIS를 통해 가져온 상위 미국 종목 리스트 사용
        tickers = DataService.get_top_us_tickers(limit=limit)
        print(f"🔍 Scanning {len(tickers)} stocks from US market via KIS...")
        
        opportunities = {
            "oversold_bluechip": [], # 과매도 우량주
            "trend_breakout": [],    # 추세 돌파
            "analyst_strong_buy": [] # 기관 강력 매수
        }
        
        token = KisService.get_access_token()
        
        for ticker in tickers:
            try:
                # 1. 상세 시세 및 지표 (PER, PBR 등 포함)
                price_info = KisFetcher.fetch_overseas_price(token, ticker)
                if not price_info: continue
                
                price = price_info.get('price', 0)
                if not price: continue
                
                # 2. 기술적 지표 (과거 1년치 시세)
                hist = DataService.get_price_history(ticker, days=365)
                if hist.empty: continue
                
                indicators = IndicatorService.get_latest_indicators(hist['Close'])
                rsi = indicators.get('rsi', 50)
                ema200 = indicators.get('ema200', 0)
                
                prev_close = hist['Close'].iloc[-2] if len(hist) > 1 else price
                
                # 3. 기본적 분석 지표 (KisFetcher에서 파싱한 데이터)
                pbr = price_info.get('pbr', 0)
                market_cap = price_info.get('market_cap', 0) # 파싱된 시총
                target = price_info.get('raw', {}).get('target_mean_price') # API 지원 시
                
                # [조건 A] 과매도 우량주 (RSI < 30)
                if rsi < 35: # 약간 완화
                    if market_cap > 50_000_000_000 and pbr and pbr < 8: # 시총 500억 달러 이상 우량주
                        opportunities["oversold_bluechip"].append({
                            "ticker": ticker,
                            "price": price,
                            "rsi": round(rsi, 1),
                            "pbr": round(pbr, 2),
                            "name": price_info.get('name', ticker)
                        })

                # [조건 B] 추세 돌파 (EMA 200 골든크로스)
                if ema200 > 0 and prev_close < ema200 and price > ema200:
                    opportunities["trend_breakout"].append({
                        "ticker": ticker,
                        "price": price,
                        "ema200": round(ema200, 2),
                        "change": round(((price - prev_close)/prev_close)*100, 1)
                    })
                    
                # [조건 C] 기관 강력 매수 (목표가 괴리율 > 30%)
                if target and target > price * 1.3:
                    upside = ((target - price) / price) * 100
                    opportunities["analyst_strong_buy"].append({
                        "ticker": ticker,
                        "price": price,
                        "target": target,
                        "upside": round(upside, 1),
                        "name": price_info.get('name', ticker)
                    })
                
                print(".", end="", flush=True)
                # KIS API 속도 제한 고려 (VTS의 경우 초당 2건)
                time.sleep(0.5)
                
            except Exception as e:
                # logger.error(f"Error scanning {ticker}: {e}")
                continue
                
        print("\n✅ Scan complete.")
        return opportunities
