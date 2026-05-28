import pandas as pd
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from config import Config
from services.kis.kis_service import KisService
from services.kis.fetch.kis_fetcher import KisFetcher
from typing import Dict, Optional
from utils.logger import get_logger
from models.schemas import (
    MarketRegimeSchema, MarketRegimeComponents, OtherDetailScores,
    EconomicPhaseDetail, EconomicIndicatorEntry, EconomicIndicatorsSummary,
    EconomicIndicatorsSnapshot, IndexQuote, CryptoQuote, CommodityQuote,
    RegimeComponents,
)

logger = get_logger("macro_service")

MACRO_CACHE_EXPIRY_SEC = 3600


class MacroService:
    """Macroeconomic indicators and market regime analysis (KIS/FRED based)."""
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

    # weight: importance per indicator (normalized by sum — absolute values fixed)
    # Core inflation/employment (3): CPI, unemployment, NFP
    # Major leading/sentiment (2): PMI, consumer confidence, PPI, retail sales, durable goods
    # Auxiliary (1): rest
    MACRO_RULES = {
        "cpi":                   {"higher_is_good": False, "name": "CPI",                    "weight": 3},
        "unemployment_rate":     {"higher_is_good": False, "name": "Unemployment Rate",      "weight": 3},
        "nonfarm_payrolls":      {"higher_is_good": True,  "name": "Nonfarm Payrolls (NFP)", "weight": 3},
        "pmi":                   {"higher_is_good": True,  "name": "ISM Manufacturing (IPMAN)", "weight": 2},
        "consumer_confidence":   {"higher_is_good": True,  "name": "Consumer Confidence",    "weight": 2},
        "ppi":                   {"higher_is_good": False, "name": "PPI",                    "weight": 2},
        "retail_sales":          {"higher_is_good": True,  "name": "Retail Sales",           "weight": 2},
        "durable_goods_orders":  {"higher_is_good": True,  "name": "Durable Goods Orders",   "weight": 2},
        "initial_jobless_claims":{"higher_is_good": False, "name": "Initial Jobless Claims",  "weight": 2},
        "industrial_production": {"higher_is_good": True,  "name": "Industrial Production",  "weight": 1},
        "capacity_utilization":  {"higher_is_good": True,  "name": "Capacity Utilization",   "weight": 1},
        "avg_hourly_earnings":   {"higher_is_good": False, "name": "Avg Hourly Earnings",    "weight": 1},
        "housing_starts":        {"higher_is_good": True,  "name": "Housing Starts",         "weight": 1},
        "building_permits":      {"higher_is_good": True,  "name": "Building Permits",       "weight": 1},
    }

    @classmethod
    def _fetch_raw_indicators(cls) -> tuple:
        """모든 외부 API 데이터 조회. Returns (vix, fear_greed, economic_indicators, us_10y_yield, crypto, commodities)."""
        return (
            cls._get_vix(),
            cls._get_fear_greed_index(),
            cls._get_economic_indicators(),
            cls._get_us_10y_yield(),
            cls._get_crypto_data(),
            cls._get_commodity_data(),
        )

    @classmethod
    def get_macro_data(cls) -> dict:
        now = time.time()
        if 'macro' in cls._cache:
            data, timestamp = cls._cache['macro']
            if now - timestamp < cls._cache_expiry:
                return data

        logger.info("🌐 Fetching Comprehensive Macro Data via KIS/FRED...")
        vix, fear_greed, economic_indicators, us_10y_yield, crypto, commodities = cls._fetch_raw_indicators()
        from repositories.stock_meta_repo import StockMetaRepo
        historical_avg_score = StockMetaRepo.get_30d_avg_regime_score()
        market_regime = cls._get_market_regime(
            vix=vix,
            fear_greed=fear_greed,
            economic_indicators=economic_indicators,
            us_10y_yield=us_10y_yield,
            historical_avg_score=historical_avg_score,
        )

        data = {
            "indices": cls._get_major_indices(),
            "us_10y_yield": us_10y_yield,
            "market_regime": market_regime,
            "vix": vix,
            "fear_greed": fear_greed,
            "sector_performance": cls._get_sector_performance(),
            "crypto": crypto,
            "commodities": commodities,
            "economic_indicators": economic_indicators,
            "timestamp": now
        }

        cls._cache['macro'] = (data, now)
        cls._save_regime_snapshot(market_regime, vix, fear_greed)
        return data

    @classmethod
    def get_macro_data_snapshot(cls) -> "MacroDataSnapshot":
        """get_macro_data() dict → MacroDataSnapshot 변환 래퍼."""
        from models.schemas import MacroDataSnapshot
        data = cls.get_macro_data()
        snapshot = MacroDataSnapshot(**{k: v for k, v in data.items() if k != "timestamp"})
        snapshot.exchange_rate = cls.get_exchange_rate()
        return snapshot

    @staticmethod
    def _save_regime_snapshot(market_regime, vix, fear_greed):
        """Save daily regime snapshot to DB for history tracking."""
        if (getattr(market_regime, 'model_extra', None) or {}).get('_fetch_failed'):
            logger.warning("SPX fetch failed — skipping regime DB save")
            return
        try:
            from services.market.stock_meta_service import StockMetaService
            today_str = datetime.now().strftime("%Y-%m-%d")
            regime_data = market_regime.model_dump() if hasattr(market_regime, 'model_dump') else market_regime
            StockMetaService.save_market_regime(today_str, regime_data, vix or 0, fear_greed or 50)
        except Exception as _e:
            logger.warning(f"Regime DB save failed: {_e}")

    @classmethod
    def invalidate_cache(cls):
        """Force-clear macro cache after economic release (full recalculation on next call)."""
        cls._cache.pop('macro', None)

    @classmethod
    def refresh_on_release(cls, release_name: str, series_ids: list) -> dict:
        """Economic release trigger: clear cache + recalculate + save to DB + Slack alert."""
        logger.info(f"Economic release detected: {release_name} — recalculating regime...")
        cls.invalidate_cache()
        data = cls.get_macro_data()   # No cache, full recalculation & DB auto-save
        regime = data.get("market_regime")
        score  = regime.regime_score if regime else "?"
        status = regime.status if regime else "?"
        vix    = data.get("vix", "?")
        fng    = data.get("fear_greed", "?")
        try:
            from services.notification.alert_service import AlertService
            AlertService.send_slack_alert(
                f"*Economic Release* — {release_name}\n"
                f"Regime update: *{status}* ({score}/100)\n"
                f"VIX: {vix}  |  Fear&Greed: {fng}"
            )
        except Exception:
            pass
        logger.info(f"Regime update complete: {status} ({score}/100)")
        return data

    _last_exchange_rate: Optional[float] = None  # 직전 성공 환율 캐시
    _exchange_rate_cached_at: Optional[float] = None  # epoch sec — TTL 캐시
    _EXCHANGE_RATE_TTL_SEC: int = 300  # 5분

    @classmethod
    def get_exchange_rate(cls) -> float:
        """USD/KRW exchange rate via yfinance — 5분 메모리 캐시.
        Fallback chain: 5분 캐시 → yfinance → 직전 성공값 → 1400."""
        import time
        now = time.time()
        # 5분 캐시 hit
        if (cls._last_exchange_rate and cls._last_exchange_rate > 0 and
            cls._exchange_rate_cached_at and (now - cls._exchange_rate_cached_at) < cls._EXCHANGE_RATE_TTL_SEC):
            return cls._last_exchange_rate
        # 만료 → yfinance 재조회
        try:
            import yfinance as yf
            data = yf.Ticker("USDKRW=X").history(period="5d")
            if data is not None and not data.empty and "Close" in data.columns:
                rate = float(data["Close"].dropna().iloc[-1])
                if rate > 0:
                    cls._last_exchange_rate = rate
                    cls._exchange_rate_cached_at = now
                    logger.info(f"💱 Exchange rate (yfinance): {rate:.2f}")
                    return rate
        except Exception as e:
            logger.warning(f"⚠️ Failed to fetch exchange rate from yfinance: {e}")
        if cls._last_exchange_rate and cls._last_exchange_rate > 0:
            logger.debug(f"💱 Using stale cached exchange rate: {cls._last_exchange_rate:.2f}")
            return cls._last_exchange_rate
        logger.warning("⚠️ Using fallback exchange rate: 1400.0 (no cache available)")
        return 1400.0

    # yfinance fallback symbols (used when KIS IDX returns 0)
    _YFINANCE_INDEX_MAP = {"S&P500": "^GSPC", "Dow": "^DJI", "Nasdaq100": "^NDX"}

    @classmethod
    def _get_major_indices(cls) -> Dict[str, IndexQuote]:
        """Major index quotes via KIS API (yfinance fallback when market closed or no response)."""
        token = KisService.get_access_token()
        indices = {}
        mapping = [
            ("SPX", "S&P500", "IDX"),
            ("DJI", "Dow", "IDX"),
            ("NAS", "Nasdaq100", "IDX"),
            ("0001", "KOSPI", "KRX"),
        ]
        for symb, name, excd in mapping:
            try:
                if excd == "KRX":
                    res = KisFetcher.fetch_domestic_price(token, symb)
                else:
                    res = KisFetcher.fetch_overseas_price(token, symb, meta={"api_market_code": excd})
                indices[name] = IndexQuote(
                    price=res.get("price", 0),
                    change=res.get("change_rate", 0),
                )
            except Exception:
                indices[name] = IndexQuote()

        # yfinance fallback: supplement US indices that returned 0 from KIS
        try:
            import yfinance as yf
            for name, sym in cls._YFINANCE_INDEX_MAP.items():
                existing = indices.get(name)
                if existing is None or existing.price == 0:
                    hist = yf.Ticker(sym).history(period="2d")
                    if len(hist) >= 2:
                        price = float(hist["Close"].iloc[-1])
                        prev  = float(hist["Close"].iloc[-2])
                        indices[name] = IndexQuote(
                            price=round(price, 2),
                            change=round((price / prev - 1) * 100, 2),
                            source="yfinance",
                        )
        except Exception:
            pass

        return indices

    @classmethod
    def _get_crypto_data(cls) -> Dict[str, CryptoQuote]:
        """Crypto prices (yfinance BTC-USD)."""
        result: Dict[str, CryptoQuote] = {"BTC": CryptoQuote()}
        try:
            import yfinance as yf
            hist = yf.Ticker("BTC-USD").history(period="5d")
            if len(hist) >= 2:
                price = float(hist["Close"].iloc[-1])
                prev = float(hist["Close"].iloc[-2])
                result["BTC"] = CryptoQuote(price=round(price, 0), change=round((price / prev - 1) * 100, 2))
        except Exception:
            pass
        return result

    @classmethod
    def _get_commodity_data(cls) -> Dict[str, CommodityQuote]:
        """Commodity prices (yfinance GC=F, CL=F)."""
        result: Dict[str, CommodityQuote] = {"Gold": CommodityQuote(), "Oil": CommodityQuote()}
        try:
            import yfinance as yf
            for name, symbol in [("Gold", "GC=F"), ("Oil", "CL=F")]:
                hist = yf.Ticker(symbol).history(period="5d")
                if len(hist) >= 2:
                    price = float(hist["Close"].iloc[-1])
                    prev = float(hist["Close"].iloc[-2])
                    result[name] = CommodityQuote(price=round(price, 2), change=round((price / prev - 1) * 100, 2))
        except Exception:
            pass
        return result

    @classmethod
    def _get_us_10y_yield(cls) -> float:
        """US 10-year Treasury yield (yfinance ^TNX)."""
        try:
            import yfinance as yf
            data = yf.Ticker("^TNX").history(period="1d")
            if not data.empty and "Close" in data.columns:
                return round(float(data["Close"].iloc[-1]), 3)
        except Exception:
            pass
        return 4.5

    @classmethod
    def _get_vix(cls) -> float:
        """VIX fear index (KIS with yfinance ^VIX fallback)."""
        token = KisService.get_access_token()
        try:
            res = KisFetcher.fetch_overseas_price(token, "VIX", meta={"api_market_code": "IDX"})
            val = res.get("price")
            if val and float(val) > 0:
                return float(val)
        except Exception:
            pass
        # yfinance fallback
        try:
            import yfinance as yf
            data = yf.Ticker("^VIX").history(period="1d")
            if not data.empty and "Close" in data.columns:
                return round(float(data["Close"].iloc[-1]), 2)
        except Exception:
            pass
        return 20.0

    # ── Score calculation helpers (shared for current/historical) ──────────────

    @staticmethod
    def _get_forward_pe() -> float | None:
        """SPY forward P/E 조회 (yfinance). 실패 시 None 반환."""
        try:
            import yfinance as yf
            val = yf.Ticker("SPY").info.get("forwardPE")
            return float(val) if val and float(val) > 0 else None
        except Exception:
            return None

    @classmethod
    def _get_avg_5y_forward_pe(cls) -> float:
        """DB 5년 평균 forward_pe. 데이터 30개 미만 시 18.5(역사적 평균) 반환."""
        from repositories.stock_meta_repo import StockMetaRepo
        avg = StockMetaRepo.get_avg_forward_pe_5y()
        return avg if avg is not None else 18.5

    @staticmethod
    def _calc_forward_pe_raw(
        forward_pe: float | None,
        avg_5y_pe: float | None,
    ) -> int:
        """S&P 500 Forward P/E vs 5Y average → raw score (-6 ~ +6). other_raw에 합산됨."""
        if forward_pe is None or avg_5y_pe is None or avg_5y_pe <= 0:
            return 0
        deviation = (forward_pe - avg_5y_pe) / avg_5y_pe
        if   deviation <= -0.20: return +6
        elif deviation <= -0.10: return +4
        elif deviation <= -0.05: return +2
        elif deviation <  +0.05: return  0
        elif deviation <  +0.10: return -2
        elif deviation <  +0.20: return -4
        else:                    return -6

    @staticmethod
    def _to_20(raw: int | float, max_val: int | float) -> int:
        """Normalize raw score in ±max_val range to 0~20 (neutral=10)."""
        return max(0, min(20, round((raw + max_val) / (2 * max_val) * 20)))

    @classmethod
    def _calc_ema_raw(cls, close: pd.Series, current_price: float, ema_map: dict) -> int:
        """EMA alignment + slope score summation."""
        price_weights = {5: 12, 20: 16, 60: 16, 120: 20, 200: 24}
        raw = sum(w if current_price >= ema_map[p] else -w for p, w in price_weights.items())
        ema5, ema20, ema60, ema120, ema200 = (ema_map[p] for p in [5, 20, 60, 120, 200])
        if ema5 > ema20 > ema60 > ema120 > ema200:
            raw += 12
        elif ema5 < ema20 < ema60 < ema120 < ema200:
            raw -= 12
        for p in [20, 60, 120]:
            ema_series = close.ewm(span=p, adjust=False).mean()
            slope_up = len(ema_series) >= 2 and float(ema_series.iloc[-1]) > float(ema_series.iloc[-2])
            raw += 4 if slope_up else -4
        return raw

    @classmethod
    def _calc_momentum_raw(cls, close: pd.Series, ndx_1m_hist) -> tuple:
        """SPX/NDX 1M/2W momentum + 52-week ATH drawdown score summation."""
        raw = 0
        spx_1m_ret = ndx_1m_ret = spx_2w_ret = spx_from_ath = None
        if len(close) >= 21:
            spx_1m_ret = round((float(close.iloc[-1]) / float(close.iloc[-21]) - 1) * 100, 2)
            if spx_1m_ret > 3:    raw += 10
            elif spx_1m_ret > 1:  raw += 5
            elif spx_1m_ret < -3: raw -= 10
            elif spx_1m_ret < -1: raw -= 5
        if ndx_1m_hist is not None and len(ndx_1m_hist) >= 5:
            ndx_1m_ret = round(
                (float(ndx_1m_hist["Close"].iloc[-1]) / float(ndx_1m_hist["Close"].iloc[0]) - 1) * 100, 2
            )
            if ndx_1m_ret > 3:    raw += 5
            elif ndx_1m_ret > 1:  raw += 2
            elif ndx_1m_ret < -3: raw -= 5
            elif ndx_1m_ret < -1: raw -= 2
        if len(close) >= 11:
            spx_2w_ret = round((float(close.iloc[-1]) / float(close.iloc[-11]) - 1) * 100, 2)
            if spx_2w_ret > 2:      raw += 6
            elif spx_2w_ret > 0.5:  raw += 3
            elif spx_2w_ret < -2:   raw -= 6
            elif spx_2w_ret < -0.5: raw -= 3
        if len(close) >= 252:
            ath_52w = float(close.iloc[-252:].max())
            if ath_52w > 0:
                spx_from_ath = round((float(close.iloc[-1]) / ath_52w - 1) * 100, 2)
                if spx_from_ath < -20:   raw -= 8
                elif spx_from_ath < -10: raw -= 4
                elif spx_from_ath < -5:  raw -= 2
        return raw, {
            "spx_1m_ret": spx_1m_ret, "ndx_1m_ret": ndx_1m_ret,
            "spx_2w_ret": spx_2w_ret, "spx_from_ath_pct": spx_from_ath,
        }

    @classmethod
    def _calc_technical_20(cls, close: pd.Series, ndx_1m_hist=None) -> tuple:
        """EMA alignment + SPX/NDX/2W momentum + ATH drawdown -> (technical_20: 0~20, detail, ema_map)."""
        current_price = float(close.tail(20).mean()) if len(close) >= 20 else float(close.iloc[-1])
        ema_map = {p: float(close.ewm(span=p, adjust=False).mean().iloc[-1]) for p in [5, 20, 60, 120, 200]}
        ema_raw = cls._calc_ema_raw(close, current_price, ema_map)
        momentum_raw, tech_detail = cls._calc_momentum_raw(close, ndx_1m_hist)
        tech_raw = ema_raw + momentum_raw
        max_abs_tech = sum({5: 12, 20: 16, 60: 16, 120: 20, 200: 24}.values()) + 12 + (4 * 3) + 10 + 5 + 6 + 8
        technical_score = max(-30, min(30, int(round((tech_raw / max_abs_tech) * 30))))
        return cls._to_20(technical_score, 30), tech_detail, ema_map

    @classmethod
    def _calc_vix_20(cls, vix: float, vix_1m_chg: float | None) -> int:
        """VIX level (±8) + velocity (±4) -> 0~20."""
        vix_score = 0
        if vix >= 35:    vix_score = -8
        elif vix >= 30:  vix_score = -5
        elif vix >= 25:  vix_score = -3
        elif vix >= 22:  vix_score = -1
        elif vix <= 13:  vix_score = +8
        elif vix <= 18:  vix_score = +4

        vix_speed = 0
        if vix_1m_chg is not None:
            if vix_1m_chg > 40:    vix_speed = -4
            elif vix_1m_chg > 20:  vix_speed = -2
            elif vix_1m_chg < -30: vix_speed = +2
            elif vix_1m_chg < -15: vix_speed = +1
        return cls._to_20(max(-12, min(12, vix_score + vix_speed)), 12)

    @staticmethod
    def _calc_fng_20(fear_greed: int) -> tuple[int, bool]:
        """Fear&Greed 6-tier -> (score: 0~20, extreme_fear: bool)."""
        fng_score = 0
        if fear_greed <= 15:    fng_score = -10
        elif fear_greed <= 25:  fng_score = -6
        elif fear_greed <= 40:  fng_score = -3
        elif fear_greed >= 85:  fng_score = +10
        elif fear_greed >= 70:  fng_score = +6
        elif fear_greed >= 55:  fng_score = +3
        extreme_fear = fear_greed <= 20
        return MacroService._to_20(fng_score, 10), extreme_fear

    @staticmethod
    def _calc_econ_20(economic_indicators) -> int:
        """FRED economic indicator weighted score -> 0~20."""
        if economic_indicators is None:
            econ_summary = EconomicIndicatorsSummary()
        elif isinstance(economic_indicators, EconomicIndicatorsSnapshot):
            econ_summary = economic_indicators.summary
        else:
            econ_summary = (economic_indicators or {}).get("summary", {})
        econ_total   = float(getattr(econ_summary, 'total_score', 0) if isinstance(econ_summary, EconomicIndicatorsSummary) else econ_summary.get("total_score", 0) or 0)
        econ_max_val = float(getattr(econ_summary, 'max_score', 0) if isinstance(econ_summary, EconomicIndicatorsSummary) else econ_summary.get("max_score", 0) or 0)
        econ_score   = int(round((econ_total / econ_max_val) * 10)) if econ_max_val > 0 else 0
        return MacroService._to_20(max(-10, min(10, econ_score)), 10)

    # ── 5-Phase economic regime classification ────────────────────────────

    @staticmethod
    def _calc_inflation_pressure(oil_1m_ret: float | None, economic_indicators: dict | None) -> tuple:
        """Inflation pressure score based on oil/CPI/PPI MoM (-10 ~ +10)."""
        pressure = 0
        detail = {}

        # Oil 1M return
        if oil_1m_ret is not None:
            detail["oil_1m_ret"] = oil_1m_ret
            if oil_1m_ret > 20:     pressure += 2
            elif oil_1m_ret > 10:   pressure += 1
            elif oil_1m_ret > 5:    pressure += 1
            elif oil_1m_ret < -15:  pressure -= 2
            elif oil_1m_ret < -5:   pressure -= 1

        # CPI / PPI MoM (extracted from FRED indicators)
        if isinstance(economic_indicators, EconomicIndicatorsSnapshot):
            indicators = economic_indicators.indicators
        else:
            indicators = (economic_indicators or {}).get("indicators", {})
        for key, weight in [("cpi", 3), ("ppi", 2)]:
            ind = indicators.get(key)
            if ind is None:
                continue
            latest = getattr(ind, 'latest', None) if isinstance(ind, EconomicIndicatorEntry) else ind.get("latest")
            prev = getattr(ind, 'previous', None) if isinstance(ind, EconomicIndicatorEntry) else ind.get("previous")
            if latest and prev and prev > 0:
                mom = round((latest - prev) / prev * 100, 3)
                detail[f"{key}_mom"] = mom
                if mom > 0.4:    pressure += weight
                elif mom > 0.2:  pressure += (weight - 1)
                elif mom > 0.1:  pressure += 1
                elif mom < 0:    pressure -= (weight - 1)

        return max(-10, min(10, pressure)), detail

    @staticmethod
    def _calc_growth_signal(tech_detail: dict, vix: float, fear_greed: int, econ_20: int) -> tuple:
        """Growth signal based on SPX momentum/VIX/F&G/economic indicators (-10 ~ +10)."""
        signal = 0
        detail = {}

        # SPX 1M momentum
        spx_1m = tech_detail.get("spx_1m_ret")
        if spx_1m is not None:
            detail["spx_1m_ret"] = spx_1m
            if spx_1m > 3:      signal += 3
            elif spx_1m > 1:    signal += 1
            elif spx_1m < -5:   signal -= 3
            elif spx_1m < -3:   signal -= 2
            elif spx_1m < -1:   signal -= 1

        # VIX level (halved — already scored in vix_20)
        if vix >= 30:           signal -= 2
        elif vix >= 25:         signal -= 1
        elif vix >= 20:         signal -= 1
        elif vix <= 13:         signal += 2
        elif vix <= 18:         signal += 1

        # Fear & Greed (halved — already scored in fng_20)
        if fear_greed <= 20:    signal -= 1
        elif fear_greed <= 35:  signal -= 1
        elif fear_greed >= 80:  signal += 1
        elif fear_greed >= 65:  signal += 1

        # FRED econ (10 = neutral)
        signal += (econ_20 - 10)

        return max(-10, min(10, signal)), detail

    ECONOMIC_PHASES = {
        "Stagflation":  {"modifier": -12, "label": "Stagflation"},
        "Deflation":    {"modifier":  -8, "label": "Deflation"},
        "Inflation":    {"modifier":  -5, "label": "Inflation"},
        "Reflation":    {"modifier":  +3, "label": "Reflation"},
        "Goldilocks":   {"modifier":  +8, "label": "Goldilocks"},
    }

    COMPONENT_WEIGHTS = {
        "technical": 20,
        "vix":       25,
        "fng":       20,
        "econ":      20,
        "other":     15,
    }

    @classmethod
    def _determine_economic_phase(cls, inflation_pressure: int, growth_signal: int) -> tuple:
        """Inflation pressure x growth signal -> (phase_name, modifier)."""
        if inflation_pressure >= 5 and growth_signal <= -4:
            phase = "Stagflation"
        elif inflation_pressure >= 3:
            phase = "Inflation"
        elif inflation_pressure <= -3 and growth_signal <= -3:
            phase = "Deflation"
        elif inflation_pressure <= 0 and growth_signal >= 3:
            phase = "Goldilocks"
        elif inflation_pressure > 0 and growth_signal >= 0:
            phase = "Reflation"
        else:
            return "Neutral", 0
        return phase, cls.ECONOMIC_PHASES[phase]["modifier"]

    @staticmethod
    def _score_yield(us_10y_yield: float) -> int:
        if us_10y_yield <= 3.5: return 8
        if us_10y_yield <= 4.0: return 4
        if us_10y_yield <= 4.5: return 0
        if us_10y_yield <= 5.0: return -4
        return -8

    @staticmethod
    def _score_curve(yield_spread: float | None) -> int:
        if yield_spread is None: return 0
        if yield_spread > 1.0: return 6
        if yield_spread > 0.3: return 3
        if yield_spread < -1.0: return -6
        if yield_spread < -0.3: return -3
        return 0

    @staticmethod
    def _score_btc(btc_ret: float | None) -> int:
        if btc_ret is None: return 0
        if btc_ret > 20: return 3
        if btc_ret > 10: return 1
        if btc_ret < -25: return -3
        if btc_ret < -12: return -1
        return 0

    @staticmethod
    def _score_dxy(dxy_ret: float | None) -> int:
        if dxy_ret is None: return 0
        if dxy_ret > 3: return -4
        if dxy_ret > 1: return -2
        if dxy_ret < -3: return 4
        if dxy_ret < -1: return 2
        return 0

    @staticmethod
    def _score_gold(gold_ret: float | None) -> int:
        if gold_ret is None: return 0
        if gold_ret > 5: return -4
        if gold_ret > 2: return -2
        if gold_ret < -5: return 4
        if gold_ret < -2: return 2
        return 0

    @staticmethod
    def _score_oil(oil_ret: float | None) -> int:
        if oil_ret is None: return 0
        if oil_ret > 20: return -3
        if oil_ret > 10: return -2
        if oil_ret > 5: return -1
        if oil_ret < -15: return 2
        if oil_ret < -5: return 1
        return 0

    @classmethod
    def _calc_composite_20(
        cls,
        us_10y_yield: float,
        yield_spread: float | None,
        btc_ret: float | None,
        dxy_ret: float | None,
        gold_ret: float | None,
        oil_ret: float | None = None,
        forward_pe: float | None = None,
        avg_5y_pe: float | None = None,
    ) -> tuple:
        """Yield(±8)+curve(±6)+DXY(±4)+BTC(±3)+Gold(±4)+Oil(±5)+ForwardPE(±6) -> (other_20: 0~20, score_detail).
        max_val: 28(기존) + 6(forward_pe) = 34
        """
        yield_score = MacroService._score_yield(us_10y_yield)
        curve_score = MacroService._score_curve(yield_spread)
        btc_score = MacroService._score_btc(btc_ret)
        dxy_score = MacroService._score_dxy(dxy_ret)
        gold_score = MacroService._score_gold(gold_ret)
        oil_score = MacroService._score_oil(oil_ret)
        forward_pe_raw = cls._calc_forward_pe_raw(forward_pe, avg_5y_pe)
        other_raw = yield_score + curve_score + dxy_score + btc_score + gold_score + oil_score + forward_pe_raw
        other_20 = MacroService._to_20(other_raw, 34)
        return other_20, {
            "yield_score": yield_score, "curve_score": curve_score,
            "dxy_score": dxy_score, "btc_score": btc_score,
            "gold_score": gold_score, "oil_score": oil_score,
            "forward_pe_raw": forward_pe_raw,
        }

    @classmethod
    def _fetch_composite_assets(cls, us_10y_yield: float) -> tuple:
        """Fetch yield curve spread + BTC/DXY/Gold/Oil 1M returns."""
        yield_spread = None
        try:
            y2_val, _ = cls._get_fred_latest_pair("DGS2")
            if y2_val is not None:
                yield_spread = round(us_10y_yield - y2_val, 3)
        except Exception:
            pass

        btc_ret = dxy_ret = gold_ret = oil_ret = None
        try:
            import yfinance as yf
            for sym, key in [("BTC-USD", "btc"), ("DX-Y.NYB", "dxy"), ("GC=F", "gold"), ("CL=F", "oil")]:
                try:
                    h = yf.Ticker(sym).history(period="1mo")
                    if len(h) >= 5:
                        ret = round((float(h["Close"].iloc[-1]) / float(h["Close"].iloc[0]) - 1) * 100, 2)
                        if key == "oil":
                            ret = max(-25, min(25, ret))
                        if key == "btc":    btc_ret  = ret
                        elif key == "dxy":  dxy_ret  = ret
                        elif key == "gold": gold_ret = ret
                        elif key == "oil":  oil_ret  = ret
                except Exception:
                    pass
        except Exception:
            pass
        return yield_spread, btc_ret, dxy_ret, gold_ret, oil_ret

    @classmethod
    def _get_bear_threshold(cls) -> int:
        """Dynamic Bear threshold based on persistence (default 40, 1-month Bear->44, 2-month Bear->48)."""
        bear_threshold = 40
        try:
            from services.market.stock_meta_service import StockMetaService
            recent = StockMetaService.get_market_regime_history(days=70)
            if recent and len(recent) >= 1:
                recent_statuses = [r.get("status") for r in recent[:2]]
                bear_months = sum(1 for s in recent_statuses if s == "Bear")
                if bear_months >= 2:
                    bear_threshold = 48
                elif bear_months >= 1:
                    bear_threshold = 44
        except Exception:
            pass
        return bear_threshold

    @classmethod
    def _fetch_spx_and_ndx_history(cls) -> tuple:
        """Fetch real-time SPX 2-year close + NDX 1-month history."""
        import yfinance as yf
        close = None
        try:
            raw = yf.Ticker("^GSPC").history(period="2y")
            if not raw.empty and "Close" in raw.columns:
                close = pd.to_numeric(raw["Close"], errors="coerce").dropna()
            if close is None or (hasattr(close, 'empty') and close.empty):
                logger.warning("SPX 2y fetch failed — yfinance returned empty data")
        except Exception as e:
            logger.error(f"SPX 2y fetch exception: {e}")
        ndx_1m_hist = None
        try:
            ndx_1m_hist = yf.Ticker("^NDX").history(period="1mo")
        except Exception:
            pass
        return close, ndx_1m_hist

    @classmethod
    def _fetch_vix_1m_change(cls, vix: float) -> float | None:
        """Fetch VIX 1-month change rate."""
        try:
            import yfinance as yf
            vix_h = yf.Ticker("^VIX").history(period="1mo")
            if len(vix_h) >= 5:
                vix_prev = float(vix_h["Close"].iloc[0])
                if vix_prev > 0:
                    return round((vix - vix_prev) / vix_prev * 100, 1)
        except Exception:
            pass
        return None

    @staticmethod
    def _compute_weighted_score(
        technical_20: int, vix_20: int, fng_20: int,
        econ_20: int, other_20: int, phase_modifier: int,
    ) -> int:
        """5개 컴포넌트 합산 후 phase_modifier 적용, 0~100 클리핑."""
        base_score = technical_20 + vix_20 + fng_20 + econ_20 + other_20
        return max(0, min(100, base_score + phase_modifier))

    @staticmethod
    def _blend_regime_score(regime_score: int, historical_avg_score: float | None) -> int:
        """현재 점수 60% + 30일 평균 40% blending. historical_avg_score 없으면 현재 점수 그대로."""
        if historical_avg_score is not None:
            return round(regime_score * 0.6 + historical_avg_score * 0.4)
        return regime_score

    @staticmethod
    def _build_regime_schema(
        blended_score: int, regime_score: int, bear_threshold: int, extreme_fear: bool,
        close: pd.Series, ema_map: dict,
        technical_20: int, tech_detail: dict,
        vix_20: int, fng_20: int, econ_20: int,
        other_20: int, other_scores: dict,
        vix: float, vix_1m_chg, us_10y_yield: float,
        yield_spread, btc_ret, dxy_ret, gold_ret, oil_ret,
        economic_phase: str, phase_modifier: int,
        inflation_pressure: int, growth_signal: int,
        inflation_detail: dict | None,
        forward_pe: float | None = None,
        avg_5y_pe: float | None = None,
    ) -> MarketRegimeSchema:
        """blended_score 기준 레짐 판정 후 MarketRegimeSchema 구성."""
        if extreme_fear:
            status = "Bear"
        elif blended_score >= 65:
            status = "Bull"
        elif blended_score <= bear_threshold:
            status = "Bear"
        else:
            status = "Neutral"
        current_price = float(close.iloc[-1])
        ema200 = ema_map[200]
        ma200 = float(close.rolling(window=200).mean().iloc[-1]) if len(close) >= 200 else ema200
        diff_pct = round((current_price - ma200) / ma200 * 100, 2) if ma200 else 0
        return MarketRegimeSchema(
            status=status,
            current=round(current_price, 2),
            ma200=round(float(ma200 or 0), 2),
            diff_pct=float(diff_pct),
            regime_score=regime_score,
            bear_threshold=bear_threshold,
            economic_phase=economic_phase,
            phase_modifier=phase_modifier,
            ema={f"ema{p}": round(v, 2) for p, v in ema_map.items()},
            components=MarketRegimeComponents(
                technical=technical_20, technical_detail=tech_detail,
                vix=vix_20, fear_greed=fng_20, economic=econ_20, other=other_20,
                other_detail=OtherDetailScores(
                    us_10y_yield=round(us_10y_yield, 3),
                    yield_spread_10y2y=yield_spread,
                    vix_1m_chg=vix_1m_chg,
                    btc_1m_ret=btc_ret, dxy_1m_ret=dxy_ret,
                    gold_1m_ret=gold_ret, oil_1m_ret=oil_ret,
                    forward_pe=forward_pe,
                    avg_5y_pe=avg_5y_pe,
                    forward_pe_deviation=(
                        round((forward_pe - avg_5y_pe) / avg_5y_pe, 4)
                        if forward_pe and avg_5y_pe and avg_5y_pe > 0 else None
                    ),
                    **other_scores,
                ),
                economic_phase_detail=EconomicPhaseDetail(
                    phase=economic_phase,
                    modifier=phase_modifier,
                    inflation_pressure=inflation_pressure,
                    growth_signal=growth_signal,
                    **(inflation_detail or {}),
                ),
            ),
        )

    @staticmethod
    def _assemble_regime_result(
        close: pd.Series, ema_map: dict,
        technical_20: int, tech_detail: dict,
        vix_20: int, fng_20: int, econ_20: int,
        other_20: int, other_scores: dict,
        vix: float, vix_1m_chg, us_10y_yield: float,
        yield_spread, btc_ret, dxy_ret, gold_ret,
        bear_threshold: int = 40,
        economic_phase: str = "Neutral",
        phase_modifier: int = 0,
        inflation_pressure: int = 0,
        growth_signal: int = 0,
        inflation_detail: dict | None = None,
        oil_ret: float | None = None,
        extreme_fear: bool = False,
        historical_avg_score: float | None = None,
        forward_pe: float | None = None,
        avg_5y_pe: float | None = None,
    ) -> MarketRegimeSchema:
        """Aggregate component scores and return market regime result."""
        regime_score = MacroService._compute_weighted_score(
            technical_20, vix_20, fng_20, econ_20, other_20, phase_modifier
        )
        blended_score = MacroService._blend_regime_score(regime_score, historical_avg_score)
        return MacroService._build_regime_schema(
            blended_score, regime_score, bear_threshold, extreme_fear,
            close, ema_map,
            technical_20, tech_detail,
            vix_20, fng_20, econ_20, other_20, other_scores,
            vix, vix_1m_chg, us_10y_yield,
            yield_spread, btc_ret, dxy_ret, gold_ret, oil_ret,
            economic_phase, phase_modifier,
            inflation_pressure, growth_signal, inflation_detail,
            forward_pe=forward_pe, avg_5y_pe=avg_5y_pe,
        )

    @classmethod
    def _calculate_all_regime_components(
        cls,
        close,
        vix: float,
        vix_1m_chg: float | None,
        fear_greed: int,
        economic_indicators,
        us_10y_yield: float,
        yield_spread: float | None,
        btc_ret: float | None,
        dxy_ret: float | None,
        gold_ret: float | None,
        oil_ret: float | None,
        ndx_1m_hist=None,
        forward_pe: float | None = None,
        avg_5y_pe: float | None = None,
    ) -> RegimeComponents:
        """5개 점수 컴포넌트 + 경제 국면 계산. 순수 계산 함수, I/O 없음."""
        technical_20, tech_detail, ema_map = cls._calc_technical_20(close, ndx_1m_hist)
        vix_20 = cls._calc_vix_20(vix, vix_1m_chg)
        fng_20, extreme_fear = cls._calc_fng_20(fear_greed)
        econ_20 = cls._calc_econ_20(economic_indicators)
        other_20, other_scores = cls._calc_composite_20(
            us_10y_yield, yield_spread, btc_ret, dxy_ret, gold_ret, oil_ret,
            forward_pe=forward_pe, avg_5y_pe=avg_5y_pe,
        )
        inflation_pressure, inflation_detail = cls._calc_inflation_pressure(oil_ret, economic_indicators)
        growth_signal, _ = cls._calc_growth_signal(tech_detail, vix, fear_greed, econ_20)
        economic_phase, phase_modifier = cls._determine_economic_phase(inflation_pressure, growth_signal)
        return RegimeComponents(
            technical_20=technical_20, vix_20=vix_20, fng_20=fng_20,
            econ_20=econ_20, other_20=other_20, extreme_fear=extreme_fear,
            ema_map=ema_map, tech_detail=tech_detail, other_scores=other_scores,
            inflation_pressure=inflation_pressure, inflation_detail=inflation_detail,
            growth_signal=growth_signal, economic_phase=economic_phase, phase_modifier=phase_modifier,
            forward_pe=forward_pe, avg_5y_pe=avg_5y_pe,
        )

    @classmethod
    def _get_market_regime(
        cls,
        vix: float | None = None,
        fear_greed: int | None = None,
        economic_indicators: dict | None = None,
        us_10y_yield: float | None = None,
        historical_avg_score: float | None = None,
    ) -> MarketRegimeSchema:
        """Market regime classification (Bull/Bear/Neutral) with 100-point scoring (5-phase economic regime)."""
        close, ndx_1m_hist = cls._fetch_spx_and_ndx_history()
        if close is None or close.empty:
            return MarketRegimeSchema(status="Unknown", regime_score=-1, _fetch_failed=True)

        if vix is None:
            vix = cls._get_vix()
        vix_1m_chg = cls._fetch_vix_1m_change(vix)
        if fear_greed is None:
            fear_greed = cls._get_fear_greed_index()
        if economic_indicators is None:
            economic_indicators = cls._get_economic_indicators()
        if us_10y_yield is None:
            us_10y_yield = cls._get_us_10y_yield()
        yield_spread, btc_ret, dxy_ret, gold_ret, oil_ret = cls._fetch_composite_assets(us_10y_yield)
        forward_pe = cls._get_forward_pe()
        avg_5y_pe  = cls._get_avg_5y_forward_pe()

        c = cls._calculate_all_regime_components(
            close, vix, vix_1m_chg, fear_greed, economic_indicators,
            us_10y_yield, yield_spread, btc_ret, dxy_ret, gold_ret, oil_ret, ndx_1m_hist,
            forward_pe=forward_pe, avg_5y_pe=avg_5y_pe,
        )
        bear_threshold = cls._get_bear_threshold()
        return cls._assemble_regime_result(
            close, c.ema_map, c.technical_20, c.tech_detail, c.vix_20, c.fng_20, c.econ_20,
            c.other_20, c.other_scores, vix, vix_1m_chg, us_10y_yield,
            yield_spread, btc_ret, dxy_ret, gold_ret, bear_threshold,
            economic_phase=c.economic_phase, phase_modifier=c.phase_modifier,
            inflation_pressure=c.inflation_pressure, growth_signal=c.growth_signal,
            inflation_detail=c.inflation_detail, oil_ret=oil_ret,
            extreme_fear=c.extreme_fear,
            historical_avg_score=historical_avg_score,
            forward_pe=c.forward_pe, avg_5y_pe=c.avg_5y_pe,
        )

    _CNN_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/121.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.cnn.com/markets/fear-and-greed",
        "Origin": "https://www.cnn.com",
    }

    @classmethod
    def _get_fear_greed_index(cls) -> int:
        # CNN Fear & Greed public endpoint (browser headers required)
        try:
            url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
            res = requests.get(url, timeout=8, headers=cls._CNN_HEADERS)
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
        """Sector performance (XLK, XLF, etc.)."""
        return {}  # Extensible via individual ETF queries through KIS

    @classmethod
    def _score_single_indicator(cls, key: str, series_id: str, latest, prev) -> EconomicIndicatorEntry:
        """Return score and status for a single indicator."""
        rule = cls.MACRO_RULES.get(key, {})
        name, weight = rule.get("name", key), float(rule.get("weight", 1))
        higher_is_good = bool(rule.get("higher_is_good", True))
        if latest is None or prev is None:
            return EconomicIndicatorEntry(name=name, series_id=series_id, weight=weight)
        delta = latest - prev
        raw = (1 if higher_is_good else -1) if delta > 0 else (-1 if higher_is_good else 1) if delta < 0 else 0
        return EconomicIndicatorEntry(
            name=name, series_id=series_id, weight=weight,
            latest=round(float(latest), 4), previous=round(float(prev), 4), delta=round(float(delta), 4),
            score=raw, weighted_score=raw * weight,
            status="positive" if raw > 0 else "negative" if raw < 0 else "neutral",
        )

    @classmethod
    def _get_economic_indicators(cls) -> EconomicIndicatorsSnapshot:
        """Fetch all 14 FRED indicators in parallel and compute weighted scores."""
        all_keys = list(cls.FRED_SERIES.keys())

        def _fetch(key):
            series_id = cls.FRED_SERIES[key]
            latest, prev = cls._get_fred_latest_pair(series_id)
            return key, series_id, latest, prev

        fetch_results = {}
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {executor.submit(_fetch, k): k for k in all_keys}
            for future in as_completed(futures):
                key, series_id, latest, prev = future.result()
                fetch_results[key] = (series_id, latest, prev)

        indicators = {}
        total_weighted_score = 0.0
        max_weighted_score = 0.0
        for key in all_keys:
            series_id, latest, prev = fetch_results[key]
            ind = cls._score_single_indicator(key, series_id, latest, prev)
            indicators[key] = ind
            if ind.status != "no_data":
                total_weighted_score += ind.weighted_score
                max_weighted_score += ind.weight

        sentiment_ratio = round(total_weighted_score / max_weighted_score, 4) if max_weighted_score > 0 else 0
        available = sum(1 for v in indicators.values() if v.status != "no_data")
        return EconomicIndicatorsSnapshot(
            indicators=indicators,
            summary=EconomicIndicatorsSummary(
                total_weighted_score=round(total_weighted_score, 2),
                max_weighted_score=round(max_weighted_score, 2),
                total_score=round(total_weighted_score, 2),
                max_score=round(max_weighted_score, 2),
                sentiment_ratio=sentiment_ratio,
                available_count=available,
                total_count=len(all_keys),
            ),
        )

    @staticmethod
    def _fetch_historical_spx(start_2y: str, start_1m: str, end_date: str) -> tuple:
        """Fetch historical SPX 2-year close + NDX 1-month history."""
        import yfinance as yf
        close = None
        try:
            raw = yf.Ticker("^GSPC").history(start=start_2y, end=end_date)
            if not raw.empty and "Close" in raw.columns:
                close = pd.to_numeric(raw["Close"], errors="coerce").dropna() or None
        except Exception:
            pass
        ndx_1m_hist = None
        try:
            ndx_1m_hist = yf.Ticker("^NDX").history(start=start_1m, end=end_date)
        except Exception:
            pass
        return close, ndx_1m_hist

    @staticmethod
    def _fetch_historical_vix(start_1m: str, end_date: str) -> tuple:
        """Fetch historical VIX + 1-month change rate."""
        import yfinance as yf
        vix, vix_1m_chg = 20.0, None
        try:
            vix_h = yf.Ticker("^VIX").history(start=start_1m, end=end_date)
            if not vix_h.empty:
                vix = round(float(vix_h["Close"].iloc[-1]), 2)
                if len(vix_h) >= 5:
                    vix_prev = float(vix_h["Close"].iloc[0])
                    if vix_prev > 0:
                        vix_1m_chg = round((vix - vix_prev) / vix_prev * 100, 1)
        except Exception:
            pass
        return vix, vix_1m_chg

    @staticmethod
    def _fetch_historical_composite_returns(start_1m: str, end_date: str) -> tuple:
        """Fetch historical BTC/DXY/Gold/Oil 1-month returns."""
        import yfinance as yf
        btc_ret = dxy_ret = gold_ret = oil_ret = None
        try:
            for sym, key in [("BTC-USD", "btc"), ("DX-Y.NYB", "dxy"), ("GC=F", "gold"), ("CL=F", "oil")]:
                try:
                    h = yf.Ticker(sym).history(start=start_1m, end=end_date)
                    if len(h) >= 5:
                        ret = round((float(h["Close"].iloc[-1]) / float(h["Close"].iloc[0]) - 1) * 100, 2)
                        if key == "oil":
                            ret = max(-25, min(25, ret))
                        if key == "btc":    btc_ret  = ret
                        elif key == "dxy":  dxy_ret  = ret
                        elif key == "gold": gold_ret = ret
                        elif key == "oil":  oil_ret  = ret
                except Exception:
                    pass
        except Exception:
            pass
        return btc_ret, dxy_ret, gold_ret, oil_ret

    @classmethod
    def calculate_historical_regime(cls, date_str: str) -> dict:
        """Calculate market regime for a specific date using historical data, then save to DB.

        - SPX / VIX / BTC / DXY / Gold: uses yfinance historical data
        - FRED indicators: monthly, so current values are used
        - Fear&Greed: current value if within 7 days, otherwise estimated as neutral (50)
        """
        from datetime import datetime, timedelta
        target_dt = datetime.strptime(date_str, "%Y-%m-%d")
        today_dt  = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        end_date  = (target_dt + timedelta(days=1)).strftime("%Y-%m-%d")
        start_2y  = (target_dt - timedelta(days=730)).strftime("%Y-%m-%d")
        start_1m  = (target_dt - timedelta(days=32)).strftime("%Y-%m-%d")

        close, ndx_1m_hist = cls._fetch_historical_spx(start_2y, start_1m, end_date)
        if close is None or close.empty:
            return {"error": f"No SPX data for {date_str}"}

        vix, vix_1m_chg = cls._fetch_historical_vix(start_1m, end_date)
        days_diff  = (today_dt - target_dt).days
        fear_greed = cls._get_fear_greed_index() if days_diff <= 7 else 50
        us_10y_yield = cls._get_us_10y_yield()
        yield_spread = None
        try:
            y2_val, _ = cls._get_fred_latest_pair("DGS2")
            if y2_val is not None:
                yield_spread = round(us_10y_yield - y2_val, 3)
        except Exception:
            pass
        economic_indicators = cls._get_economic_indicators()
        btc_ret, dxy_ret, gold_ret, oil_ret = cls._fetch_historical_composite_returns(start_1m, end_date)

        c = cls._calculate_all_regime_components(
            close, vix, vix_1m_chg, fear_greed, economic_indicators,
            us_10y_yield, yield_spread, btc_ret, dxy_ret, gold_ret, oil_ret, ndx_1m_hist,
        )
        regime_data = cls._assemble_regime_result(
            close, c.ema_map, c.technical_20, c.tech_detail, c.vix_20, c.fng_20, c.econ_20,
            c.other_20, c.other_scores, vix, vix_1m_chg, us_10y_yield,
            yield_spread, btc_ret, dxy_ret, gold_ret, bear_threshold=40,
            economic_phase=c.economic_phase, phase_modifier=c.phase_modifier,
            inflation_pressure=c.inflation_pressure, growth_signal=c.growth_signal,
            inflation_detail=c.inflation_detail, oil_ret=oil_ret,
        )

        try:
            from services.market.stock_meta_service import StockMetaService
            regime_dict = regime_data.model_dump() if hasattr(regime_data, 'model_dump') else regime_data
            StockMetaService.save_market_regime(date_str, regime_dict, vix, fear_greed)
        except Exception:
            pass

        return {"date": date_str, "vix": vix, "fear_greed": fear_greed,
                "us_10y_yield": us_10y_yield, "market_regime": regime_data}

    @classmethod
    def _get_fred_latest_pair(cls, series_id: str | None) -> tuple[float | None, float | None]:
        """Return latest and previous values from a FRED time series."""
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
