import pandas as pd
import time
import requests
from datetime import datetime, timedelta
from config import Config
from services.kis.kis_service import KisService
from services.kis.fetch.kis_fetcher import KisFetcher

MACRO_CACHE_EXPIRY_SEC = 3600


class MacroService:
    """거시경제 지표 및 시장 국면 분석 (KIS/FRED 기반)."""
    _cache: dict = {}
    _cache_expiry = MACRO_CACHE_EXPIRY_SEC
    _fred_base_url = "https://api.stlouisfed.org/fred/series/observations"

    FRED_SERIES = {
        "avg_hourly_earnings": "CES0500000003",
        "cpi": "CPIAUCSL",
        "ppi": "PPIACO",
        "nonfarm_payrolls": "PAYEMS",
        "unemployment_rate": "UNRATE",
        "consumer_confidence": "UMCSENT",
        "pmi": "IPMAN",
        "retail_sales": "RSXFS",
        "industrial_production": "INDPRO",
        "capacity_utilization": "TCU",
        "housing_starts": "HOUST",
        "building_permits": "PERMIT",
        "durable_goods_orders": "DGORDER",
        "initial_jobless_claims": "ICSA"
    }

    MACRO_RULES = {
        "avg_hourly_earnings": {"higher_is_good": False, "name": "시간당 평균 임금"},
        "cpi": {"higher_is_good": False, "name": "소비자 물가 지수"},
        "ppi": {"higher_is_good": False, "name": "생산자 물가 지수"},
        "nonfarm_payrolls": {"higher_is_good": True, "name": "비농업 고용 지수"},
        "unemployment_rate": {"higher_is_good": False, "name": "실업률"},
        "consumer_confidence": {"higher_is_good": True, "name": "소비자 신뢰 지수"},
        "pmi": {"higher_is_good": True, "name": "구매 관리자 지수"},
        "retail_sales": {"higher_is_good": True, "name": "소매 판매"},
        "industrial_production": {"higher_is_good": True, "name": "산업 생산"},
        "capacity_utilization": {"higher_is_good": True, "name": "설비 가동률"},
        "housing_starts": {"higher_is_good": True, "name": "주택 착공"},
        "building_permits": {"higher_is_good": True, "name": "건축 허가"},
        "durable_goods_orders": {"higher_is_good": True, "name": "내구재 주문"},
        "initial_jobless_claims": {"higher_is_good": False, "name": "실업 수당 청구"}
    }

    @classmethod
    def get_macro_data(cls) -> dict:
        now = time.time()
        if 'macro' in cls._cache:
            data, timestamp = cls._cache['macro']
            if now - timestamp < cls._cache_expiry:
                return data

        print("🌐 Fetching Comprehensive Macro Data via KIS/FRED...")
        vix = cls._get_vix()
        fear_greed = cls._get_fear_greed_index()
        economic_indicators = cls._get_economic_indicators()
        market_regime = cls._get_market_regime(
            vix=vix,
            fear_greed=fear_greed,
            economic_indicators=economic_indicators,
        )

        data = {
            "indices": cls._get_major_indices(),
            "us_10y_yield": cls._get_us_10y_yield(),
            "market_regime": market_regime,
            "vix": vix,
            "fear_greed": fear_greed,
            "sector_performance": cls._get_sector_performance(),
            "crypto": cls._get_crypto_data(),
            "commodities": cls._get_commodity_data(),
            "economic_indicators": economic_indicators,
            "timestamp": now
        }
        
        cls._cache['macro'] = (data, now)
        return data

    @classmethod
    def get_exchange_rate(cls) -> float:
        """KIS 등을 활용한 환율 정보 (임시 고정 또는 API 호출)"""
        # KIS에서도 환율 정보를 제공하지만, 여기서는 단순화하여 1400 유지 또는 추후 확장
        return 1400.0

    @classmethod
    def _get_major_indices(cls) -> dict:
        """KIS API를 통한 주요 지수 시세"""
        token = KisService.get_access_token()
        indices = {}
        # KIS 지수 심볼 (IDX 거래소 기준)
        mapping = [
            ("SPX", "S&P500", "IDX"),
            ("DJI", "Dow", "IDX"),
            ("NAS", "Nasdaq100", "IDX"),
            ("0001", "KOSPI", "KRX") # 국내는 종목코드로 조회 가능
        ]
        for symb, name, excd in mapping:
            try:
                if excd == "KRX":
                    res = KisFetcher.fetch_domestic_price(token, symb)
                else:
                    res = KisFetcher.fetch_overseas_price(token, symb, meta={"api_market_code": excd})
                
                indices[name] = {
                    "price": res.get("price", 0),
                    "change": res.get("change_rate", 0)
                }
            except:
                indices[name] = {"price": 0, "change": 0}
        return indices

    @classmethod
    def _get_crypto_data(cls) -> dict:
        """가상자산 시세 (외부 API 사용 권장, 여기서는 생략 또는 Mock)"""
        return {"Bitcoin": {"price": 0, "change": 0}}

    @classmethod
    def _get_commodity_data(cls) -> dict:
        """원자재 시세 (KIS 해외 선물/상품 API 활용 가능)"""
        return {"Gold": {"price": 0, "change": 0}, "Silver": {"price": 0, "change": 0}}

    @classmethod
    def _get_us_10y_yield(cls) -> float:
        """미국 10년물 국채 금리"""
        # KIS에서 ^TNX와 매칭되는 심볼 확인 필요, 일단 4.5로 유지
        return 4.5

    @classmethod
    def _get_vix(cls) -> float:
        """VIX 공포 지수"""
        token = KisService.get_access_token()
        try:
            res = KisFetcher.fetch_overseas_price(token, "VIX", meta={"api_market_code": "IDX"})
            return res.get("price", 20.0)
        except:
            return 20.0

    @classmethod
    def _get_market_regime(
        cls,
        vix: float | None = None,
        fear_greed: int | None = None,
        economic_indicators: dict | None = None
    ) -> dict:
        """시장 국면 판단 (Bull/Bear/Neutral) 및 점수 계산"""
        # S&P 500 (SPX)의 다중 EMA 기반 합성 점수
        from services.market.data_service import DataService
        hist = DataService.get_price_history("SPX", days=400) # 지수는 별도 처리가 필요할 수 있음
        if hist.empty:
            return {"status": "Neutral", "current": 0, "ma200": 0, "diff_pct": 0, "regime_score": 0}

        if "Close" not in hist.columns:
            return {"status": "Neutral", "current": 0, "ma200": 0, "diff_pct": 0, "regime_score": 0}

        close = pd.to_numeric(hist["Close"], errors="coerce").dropna()
        if close.empty:
            return {"status": "Neutral", "current": 0, "ma200": 0, "diff_pct": 0, "regime_score": 0}

        current_price = float(close.iloc[-1])
        ema_periods = [5, 20, 60, 120, 200]
        ema_map = {
            p: float(close.ewm(span=p, adjust=False).mean().iloc[-1])
            for p in ema_periods
        }

        # 호재(+) / 악재(-) 원칙으로 점수화
        # 단기<중기<장기 순 하락 정렬이면 악재, 반대면 호재
        raw_score = 0
        price_weights = {5: 8, 20: 12, 60: 16, 120: 22, 200: 30}
        for p, w in price_weights.items():
            raw_score += w if current_price >= ema_map[p] else -w

        ema5 = ema_map[5]
        ema20 = ema_map[20]
        ema60 = ema_map[60]
        ema120 = ema_map[120]
        ema200 = ema_map[200]

        if ema5 > ema20 > ema60 > ema120 > ema200:
            raw_score += 12
        elif ema5 < ema20 < ema60 < ema120 < ema200:
            raw_score -= 12

        for p in [20, 60, 120]:
            ema_series = close.ewm(span=p, adjust=False).mean()
            slope_up = len(ema_series) >= 2 and float(ema_series.iloc[-1]) > float(ema_series.iloc[-2])
            raw_score += 4 if slope_up else -4

        max_abs_raw = sum(price_weights.values()) + 12 + (4 * 3)  # 100
        technical_score = int(round((raw_score / max_abs_raw) * 30))
        technical_score = max(-30, min(30, technical_score))

        # 변동성(VIX) 점수
        if vix is None:
            vix = cls._get_vix()
        vix_score = 0
        if vix >= 30:
            vix_score = -8
        elif vix >= 25:
            vix_score = -5
        elif vix <= 15:
            vix_score = +6
        elif vix <= 20:
            vix_score = +3

        # 공포탐욕(Fear&Greed) 점수
        if fear_greed is None:
            fear_greed = cls._get_fear_greed_index()
        fng_score = 0
        if fear_greed <= 25:
            fng_score = -6
        elif fear_greed <= 40:
            fng_score = -3
        elif fear_greed >= 75:
            fng_score = +6
        elif fear_greed >= 60:
            fng_score = +3

        # 주요 경제지표(최소 3개 이상) 점수
        if economic_indicators is None:
            economic_indicators = cls._get_economic_indicators()
        econ_summary = (economic_indicators or {}).get("summary", {})
        econ_total = int(econ_summary.get("total_score", 0) or 0)
        econ_max = int(econ_summary.get("max_score", 0) or 0)
        econ_score = int(round((econ_total / econ_max) * 10)) if econ_max > 0 else 0
        econ_score = max(-10, min(10, econ_score))

        regime_score = technical_score + vix_score + fng_score + econ_score
        regime_score = max(-30, min(30, regime_score))

        if regime_score >= 10:
            status = "Bull"
        elif regime_score <= -10:
            status = "Bear"
        else:
            status = "Neutral"

        ma200 = float(close.rolling(window=200).mean().iloc[-1]) if len(close) >= 200 else ema200
        diff_pct = (current_price - ma200) / ma200 * 100 if ma200 else 0

        return {
            "status": status,
            "current": round(current_price, 2),
            "ma200": round(float(ma200 or 0), 2),
            "diff_pct": round(float(diff_pct), 2),
            "regime_score": regime_score,
            "ema": {f"ema{p}": round(v, 2) for p, v in ema_map.items()},
            "components": {
                "technical_score": technical_score,
                "vix_score": vix_score,
                "fear_greed_score": fng_score,
                "economic_score": econ_score
            }
        }

    @classmethod
    def _get_fear_greed_index(cls) -> int:
        # CNN Fear & Greed 공개 엔드포인트 사용
        try:
            url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
            res = requests.get(url, timeout=5)
            res.raise_for_status()
            data = res.json() or {}
            block = data.get("fear_and_greed", {})
            val = block.get("score")
            if val is None and isinstance(block, dict):
                val = block.get("value")
            if val is None:
                val = data.get("fear_and_greed_score")
            score = int(round(float(val)))
            return max(0, min(100, score))
        except Exception:
            return 50

    @classmethod
    def _get_sector_performance(cls) -> dict:
        """섹터별 성과 (XLK, XLF 등)"""
        return {} # 필요 시 KIS로 개별 ETF 조회하도록 확장 가능

    @classmethod
    def _get_economic_indicators(cls) -> dict:
        # 주요 지표 4개(요청: 최소 3개 이상) 기반 점수
        selected = [
            "unemployment_rate",   # UNRATE (낮을수록 호재)
            "consumer_confidence", # UMCSENT (높을수록 호재)
            "cpi",                 # CPIAUCSL (낮을수록 호재)
            "initial_jobless_claims"  # ICSA (낮을수록 호재)
        ]

        indicators = {}
        total_score = 0
        max_score = 0

        for key in selected:
            series_id = cls.FRED_SERIES.get(key)
            rule = cls.MACRO_RULES.get(key, {})
            higher_is_good = bool(rule.get("higher_is_good", True))
            name = rule.get("name", key)

            latest, prev = cls._get_fred_latest_pair(series_id)
            if latest is None or prev is None:
                indicators[key] = {
                    "name": name,
                    "series_id": series_id,
                    "latest": None,
                    "previous": None,
                    "delta": None,
                    "score": 0,
                    "status": "no_data"
                }
                continue

            delta = latest - prev
            score = 0
            if delta > 0:
                score = 1 if higher_is_good else -1
            elif delta < 0:
                score = -1 if higher_is_good else 1

            status = "positive" if score > 0 else "negative" if score < 0 else "neutral"
            indicators[key] = {
                "name": name,
                "series_id": series_id,
                "latest": round(float(latest), 4),
                "previous": round(float(prev), 4),
                "delta": round(float(delta), 4),
                "score": score,
                "status": status
            }
            total_score += score
            max_score += 1

        sentiment_ratio = round((total_score / max_score), 4) if max_score > 0 else 0
        return {
            "indicators": indicators,
            "summary": {
                "total_score": total_score,
                "max_score": max_score,
                "sentiment_ratio": sentiment_ratio
            }
        }

    @classmethod
    def _get_fred_latest_pair(cls, series_id: str | None) -> tuple[float | None, float | None]:
        """FRED 시계열의 최신값과 직전값 반환"""
        if not series_id:
            return None, None
        api_key = (Config.FRED_API_KEY or "").strip()
        if not api_key:
            return None, None

        try:
            params = {
                "series_id": series_id,
                "api_key": api_key,
                "file_type": "json",
                "sort_order": "desc",
                "limit": 12
            }
            res = requests.get(cls._fred_base_url, params=params, timeout=8)
            res.raise_for_status()
            observations = (res.json() or {}).get("observations", [])
            values = []
            for obs in observations:
                raw = str(obs.get("value", ".")).strip()
                if raw in ("", "."):
                    continue
                values.append(float(raw))
                if len(values) >= 2:
                    break
            if len(values) < 2:
                return None, None
            return values[0], values[1]
        except Exception:
            return None, None
