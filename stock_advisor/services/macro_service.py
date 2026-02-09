import yfinance as yf
import pandas as pd
import time

class MacroService:
    """
    거시경제 지표 및 시장 국면 분석 서비스
    """
    _cache = {}
    _cache_expiry = 3600  # 1시간 캐싱

    @classmethod
    def get_macro_data(cls) -> dict:
        """
        국채금리, 시장국면, VIX 지수, 코인, 원자재를 종합적으로 반환합니다.
        """
        now = time.time()
        if 'macro' in cls._cache:
            data, timestamp = cls._cache['macro']
            if now - timestamp < cls._cache_expiry:
                return data

        print("🌐 Fetching Macro Data...")
        data = {
            "us_10y_yield": cls._get_us_10y_yield(),
            "market_regime": cls._get_market_regime(),
            "vix": cls._get_vix(),
            "crypto": cls._get_crypto_data(),
            "commodities": cls._get_commodity_data(),
            "timestamp": now
        }
        
        cls._cache['macro'] = (data, now)
        return data

    @classmethod
    def _get_crypto_data(cls) -> dict:
        """비트코인 및 이더리움 시세"""
        crypto = {}
        for ticker, name in [("BTC-USD", "BTC"), ("ETH-USD", "ETH")]:
            try:
                t = yf.Ticker(ticker)
                price = t.fast_info.last_price
                prev = t.fast_info.previous_close
                change = ((price - prev) / prev) * 100
                crypto[name] = {"price": price, "change": change}
            except:
                crypto[name] = {"price": 0, "change": 0}
        return crypto

    @classmethod
    def _get_commodity_data(cls) -> dict:
        """금, 은, 유가 시세"""
        commodities = {}
        # 금(GC=F), 은(SI=F), 유가(CL=F)
        mapping = [("GC=F", "Gold"), ("SI=F", "Silver"), ("CL=F", "Oil")]
        for ticker, name in mapping:
            try:
                t = yf.Ticker(ticker)
                price = t.fast_info.last_price
                prev = t.fast_info.previous_close
                change = ((price - prev) / prev) * 100
                commodities[name] = {"price": price, "change": change}
            except:
                commodities[name] = {"price": 0, "change": 0}
        return commodities

    @classmethod
    def _get_us_10y_yield(cls) -> float:
        """미국 10년물 국채 금리 (^TNX)"""
        try:
            ticker = yf.Ticker("^TNX")
            # Yahoo Finance ^TNX price is yield (e.g., 4.25)
            yield_val = ticker.fast_info.last_price
            return round(yield_val, 3) if yield_val else 4.500
        except Exception as e:
            print(f"Macro yield error: {e}")
            return 4.500  # Fallback

    @classmethod
    def _get_market_regime(cls) -> dict:
        """S&P 500 기준 시장 국면 (Bull/Bear) 판단"""
        try:
            ticker = yf.Ticker("^GSPC")
            # 최근 1년(약 252 거래일) 데이터 가져오기
            hist = ticker.history(period="1y")
            
            if len(hist) < 200:
                return {"status": "Unknown", "ma200": None}
            
            current_price = hist['Close'].iloc[-1]
            ma200 = hist['Close'].rolling(window=200).mean().iloc[-1]
            
            status = "Bull" if current_price > ma200 else "Bear"
            diff_pct = ((current_price - ma200) / ma200) * 100
            
            return {
                "status": status,
                "current": round(current_price, 2),
                "ma200": round(ma200, 2),
                "diff_pct": round(diff_pct, 2)
            }
        except Exception as e:
            print(f"Macro regime error: {e}")
            return {"status": "Unknown", "ma200": None}

    @classmethod
    def _get_vix(cls) -> float:
        """VIX 공포 지수 (^VIX)"""
        try:
            ticker = yf.Ticker("^VIX")
            return round(ticker.fast_info.last_price, 2)
        except:
            return 20.00 # Neutral fallback
