import pandas as pd
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from config import Config
from services.kis.kis_service import KisService
from services.kis.fetch.kis_fetcher import KisFetcher
from utils.logger import get_logger

logger = get_logger("macro_service")

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

    # weight: 지표별 중요도 (합계 기준 정규화됨 — 절대값 불변)
    # 핵심 인플레이션/고용(3): CPI, 실업률, 비농업고용
    # 주요 선행/심리(2): PMI, 소비자신뢰, PPI, 소매판매, 내구재주문
    # 보조(1): 나머지
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
        us_10y_yield = cls._get_us_10y_yield()
        crypto = cls._get_crypto_data()
        commodities = cls._get_commodity_data()
        market_regime = cls._get_market_regime(
            vix=vix,
            fear_greed=fear_greed,
            economic_indicators=economic_indicators,
            us_10y_yield=us_10y_yield,
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

        # 오늘 날짜로 레짐 스냅샷 DB 저장 (이력 추적용)
        # SPX fetch 실패 시 불완전 데이터 저장 방지
        if market_regime.get("_fetch_failed"):
            logger.warning("SPX fetch failed — skipping regime DB save")
        else:
            try:
                from services.market.stock_meta_service import StockMetaService
                today_str = datetime.now().strftime("%Y-%m-%d")
                StockMetaService.save_market_regime(
                    today_str, market_regime, vix or 0, fear_greed or 50
                )
            except Exception as _e:
                logger.warning(f"Regime DB save failed: {_e}")

        return data

    @classmethod
    def invalidate_cache(cls):
        """경제지표 발표 후 macro 캐시를 강제 초기화 (다음 호출 시 전체 재계산)."""
        cls._cache.pop('macro', None)

    @classmethod
    def refresh_on_release(cls, release_name: str, series_ids: list) -> dict:
        """경제지표 발표 트리거 → 캐시 초기화 + 재계산 + DB 저장 + Slack 알림."""
        logger.info(f"Economic release detected: {release_name} — recalculating regime...")
        cls.invalidate_cache()
        data = cls.get_macro_data()   # 캐시 없으므로 전체 재계산 & DB auto-save
        regime = data.get("market_regime", {})
        score  = regime.get("regime_score", "?")
        status = regime.get("status", "?")
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

    @classmethod
    def get_exchange_rate(cls) -> float:
        """KIS 등을 활용한 환율 정보 (임시 고정 또는 API 호출)"""
        # KIS에서도 환율 정보를 제공하지만, 여기서는 단순화하여 1400 유지 또는 추후 확장
        return 1400.0

    # yfinance 폴백 심볼 (KIS IDX가 0을 반환할 때 사용)
    _YFINANCE_INDEX_MAP = {"S&P500": "^GSPC", "Dow": "^DJI", "Nasdaq100": "^NDX"}

    @classmethod
    def _get_major_indices(cls) -> dict:
        """KIS API를 통한 주요 지수 시세 (장 마감·미응답 시 yfinance 폴백)"""
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
                indices[name] = {
                    "price": res.get("price", 0),
                    "change": res.get("change_rate", 0),
                }
            except Exception:
                indices[name] = {"price": 0, "change": 0}

        # yfinance 폴백: KIS에서 0을 반환한 미국 지수만 보완
        try:
            import yfinance as yf
            for name, sym in cls._YFINANCE_INDEX_MAP.items():
                if indices.get(name, {}).get("price", 0) == 0:
                    hist = yf.Ticker(sym).history(period="2d")
                    if len(hist) >= 2:
                        price = float(hist["Close"].iloc[-1])
                        prev  = float(hist["Close"].iloc[-2])
                        indices[name] = {
                            "price":  round(price, 2),
                            "change": round((price / prev - 1) * 100, 2),
                            "source": "yfinance",
                        }
        except Exception:
            pass

        return indices

    @classmethod
    def _get_crypto_data(cls) -> dict:
        """가상자산 시세 (yfinance BTC-USD)"""
        result = {"BTC": {"price": 0, "change": 0}}
        try:
            import yfinance as yf
            hist = yf.Ticker("BTC-USD").history(period="2d")
            if len(hist) >= 2:
                price = float(hist["Close"].iloc[-1])
                prev = float(hist["Close"].iloc[-2])
                result["BTC"] = {"price": round(price, 0), "change": round((price / prev - 1) * 100, 2)}
        except Exception:
            pass
        return result

    @classmethod
    def _get_commodity_data(cls) -> dict:
        """원자재 시세 (yfinance GC=F, CL=F)"""
        result = {"Gold": {"price": 0, "change": 0}, "Oil": {"price": 0, "change": 0}}
        try:
            import yfinance as yf
            for name, symbol in [("Gold", "GC=F"), ("Oil", "CL=F")]:
                hist = yf.Ticker(symbol).history(period="2d")
                if len(hist) >= 2:
                    price = float(hist["Close"].iloc[-1])
                    prev = float(hist["Close"].iloc[-2])
                    result[name] = {"price": round(price, 2), "change": round((price / prev - 1) * 100, 2)}
        except Exception:
            pass
        return result

    @classmethod
    def _get_us_10y_yield(cls) -> float:
        """미국 10년물 국채 금리 (yfinance ^TNX)"""
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
        """VIX 공포 지수 (KIS → yfinance ^VIX 폴백)"""
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

    # ── 점수 계산 헬퍼 (현재/과거 공용) ──────────────────────────────────────

    @staticmethod
    def _to_20(raw: int | float, max_val: int | float) -> int:
        """±max_val 범위의 raw 점수를 0~20으로 정규화 (중립=10)."""
        return max(0, min(20, round((raw + max_val) / (2 * max_val) * 20)))

    @classmethod
    def _calc_ema_raw(cls, close: pd.Series, current_price: float, ema_map: dict) -> int:
        """EMA 배열 정렬 + 기울기 점수 합산."""
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
        """SPX/NDX 1M·2W 모멘텀 + 52주 ATH 드로다운 점수 합산."""
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
        """EMA 배열 + SPX/NDX/2주 모멘텀 + ATH드로다운 → (technical_20: 0~20, detail, ema_map)."""
        current_price = float(close.iloc[-1])
        ema_map = {p: float(close.ewm(span=p, adjust=False).mean().iloc[-1]) for p in [5, 20, 60, 120, 200]}
        ema_raw = cls._calc_ema_raw(close, current_price, ema_map)
        momentum_raw, tech_detail = cls._calc_momentum_raw(close, ndx_1m_hist)
        tech_raw = ema_raw + momentum_raw
        max_abs_tech = sum({5: 12, 20: 16, 60: 16, 120: 20, 200: 24}.values()) + 12 + (4 * 3) + 10 + 5 + 6 + 8
        technical_score = max(-30, min(30, int(round((tech_raw / max_abs_tech) * 30))))
        return cls._to_20(technical_score, 30), tech_detail, ema_map

    @classmethod
    def _calc_vix_20(cls, vix: float, vix_1m_chg: float | None) -> int:
        """VIX 레벨(±8) + 속도(±4) → 0~20."""
        vix_score = 0
        if vix >= 30:    vix_score = -8
        elif vix >= 25:  vix_score = -5
        elif vix >= 22:  vix_score = -2
        elif vix <= 13:  vix_score = +8
        elif vix <= 18:  vix_score = +4

        vix_speed = 0
        if vix_1m_chg is not None:
            if vix_1m_chg > 30:    vix_speed = -4
            elif vix_1m_chg > 15:  vix_speed = -2
            elif vix_1m_chg < -25: vix_speed = +2
            elif vix_1m_chg < -10: vix_speed = +1
        return cls._to_20(max(-12, min(12, vix_score + vix_speed)), 12)

    @staticmethod
    def _calc_fng_20(fear_greed: int) -> int:
        """Fear&Greed 6단계 → 0~20."""
        fng_score = 0
        if fear_greed <= 20:    fng_score = -10
        elif fear_greed <= 35:  fng_score = -6
        elif fear_greed <= 45:  fng_score = -2
        elif fear_greed >= 80:  fng_score = +10
        elif fear_greed >= 65:  fng_score = +6
        elif fear_greed >= 55:  fng_score = +2
        return MacroService._to_20(fng_score, 10)

    @staticmethod
    def _calc_econ_20(economic_indicators: dict) -> int:
        """FRED 경제지표 가중 점수 합산 → 0~20."""
        econ_summary = (economic_indicators or {}).get("summary", {})
        econ_total   = float(econ_summary.get("total_score", 0) or 0)
        econ_max_val = float(econ_summary.get("max_score", 0) or 0)
        econ_score   = int(round((econ_total / econ_max_val) * 10)) if econ_max_val > 0 else 0
        return MacroService._to_20(max(-10, min(10, econ_score)), 10)

    # ── 5-Phase 경제 국면 판별 ────────────────────────────────────────────

    @staticmethod
    def _calc_inflation_pressure(oil_1m_ret: float | None, economic_indicators: dict | None) -> tuple:
        """유가·CPI·PPI MoM 기반 인플레이션 압력 점수 (-10 ~ +10)."""
        pressure = 0
        detail = {}

        # Oil 1M return
        if oil_1m_ret is not None:
            detail["oil_1m_ret"] = oil_1m_ret
            if oil_1m_ret > 20:     pressure += 4
            elif oil_1m_ret > 10:   pressure += 2
            elif oil_1m_ret > 5:    pressure += 1
            elif oil_1m_ret < -10:  pressure -= 2
            elif oil_1m_ret < -5:   pressure -= 1

        # CPI / PPI MoM (FRED indicators에서 추출)
        indicators = (economic_indicators or {}).get("indicators", {})
        for key, weight in [("cpi", 3), ("ppi", 2)]:
            ind = indicators.get(key, {})
            latest, prev = ind.get("latest"), ind.get("previous")
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
        """SPX 모멘텀·VIX·F&G·경제지표 기반 성장 신호 (-10 ~ +10)."""
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

        # VIX level
        if vix >= 30:           signal -= 3
        elif vix >= 25:         signal -= 2
        elif vix >= 20:         signal -= 1
        elif vix <= 13:         signal += 3
        elif vix <= 18:         signal += 1

        # Fear & Greed
        if fear_greed <= 20:    signal -= 2
        elif fear_greed <= 35:  signal -= 1
        elif fear_greed >= 80:  signal += 2
        elif fear_greed >= 65:  signal += 1

        # FRED econ (10 = 중립)
        signal += (econ_20 - 10)

        return max(-10, min(10, signal)), detail

    ECONOMIC_PHASES = {
        "Stagflation":  {"modifier": -15, "label": "Stagflation"},
        "Deflation":    {"modifier": -10, "label": "Deflation"},
        "Inflation":    {"modifier":  -8, "label": "Inflation"},
        "Reflation":    {"modifier":  +3, "label": "Reflation"},
        "Goldilocks":   {"modifier": +10, "label": "Goldilocks"},
    }

    @classmethod
    def _determine_economic_phase(cls, inflation_pressure: int, growth_signal: int) -> tuple:
        """인플레 압력 × 성장 신호 → (phase_name, modifier)."""
        if inflation_pressure >= 3 and growth_signal <= -2:
            phase = "Stagflation"
        elif inflation_pressure >= 3 and growth_signal > -2:
            phase = "Inflation"
        elif inflation_pressure <= -3 and growth_signal <= -2:
            phase = "Deflation"
        elif inflation_pressure <= 0 and growth_signal >= 2:
            phase = "Goldilocks"
        elif inflation_pressure > 0 and growth_signal >= 0:
            phase = "Reflation"
        else:
            return "Neutral", 0
        return phase, cls.ECONOMIC_PHASES[phase]["modifier"]

    @staticmethod
    def _calc_composite_20(
        us_10y_yield: float,
        yield_spread: float | None,
        btc_ret: float | None,
        dxy_ret: float | None,
        gold_ret: float | None,
        oil_ret: float | None = None,
    ) -> tuple:
        """금리레벨(±8)+수익률곡선(±6)+DXY(±4)+BTC(±3)+Gold(±4)+Oil(±5) → (other_20: 0~20, score_detail)."""
        yield_score = 0
        if us_10y_yield <= 3.5:    yield_score = +8
        elif us_10y_yield <= 4.0:  yield_score = +4
        elif us_10y_yield <= 4.5:  yield_score = 0
        elif us_10y_yield <= 5.0:  yield_score = -4
        else:                       yield_score = -8

        curve_score = 0
        if yield_spread is not None:
            if yield_spread > 1.0:    curve_score = +6
            elif yield_spread > 0.3:  curve_score = +3
            elif yield_spread < -1.0: curve_score = -6
            elif yield_spread < -0.3: curve_score = -3

        btc_score = 0
        if btc_ret is not None:
            if btc_ret > 20:    btc_score = +3
            elif btc_ret > 10:  btc_score = +1
            elif btc_ret < -25: btc_score = -3
            elif btc_ret < -12: btc_score = -1

        dxy_score = 0
        if dxy_ret is not None:
            if dxy_ret > 3:     dxy_score = -4
            elif dxy_ret > 1:   dxy_score = -2
            elif dxy_ret < -3:  dxy_score = +4
            elif dxy_ret < -1:  dxy_score = +2

        gold_score = 0
        if gold_ret is not None:
            if gold_ret > 5:    gold_score = -4
            elif gold_ret > 2:  gold_score = -2
            elif gold_ret < -5: gold_score = +4
            elif gold_ret < -2: gold_score = +2

        oil_score = 0
        if oil_ret is not None:
            if oil_ret > 20:     oil_score = -5
            elif oil_ret > 10:   oil_score = -3
            elif oil_ret > 5:    oil_score = -1
            elif oil_ret < -15:  oil_score = +3
            elif oil_ret < -5:   oil_score = +1

        other_raw = yield_score + curve_score + dxy_score + btc_score + gold_score + oil_score
        other_20  = MacroService._to_20(other_raw, 31)
        return other_20, {
            "yield_score": yield_score, "curve_score": curve_score,
            "dxy_score": dxy_score, "btc_score": btc_score,
            "gold_score": gold_score, "oil_score": oil_score,
        }

    @classmethod
    def _fetch_composite_assets(cls, us_10y_yield: float) -> tuple:
        """수익률 곡선 스프레드 + BTC/DXY/Gold/Oil 1M 수익률 조회."""
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
        """Bear 지속성에 따른 동적 임계값 (기본 40, 1개월 Bear→44, 2개월 Bear→48)."""
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
        """실시간 SPX 2년치 close + NDX 1개월 히스토리 조회."""
        import yfinance as yf
        close = None
        try:
            raw = yf.Ticker("^GSPC").history(period="2y")
            if not raw.empty and "Close" in raw.columns:
                close = pd.to_numeric(raw["Close"], errors="coerce").dropna() or None
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
        """VIX 1개월 변화율 조회."""
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
    ) -> dict:
        """컴포넌트 점수를 합산해 시장 국면 결과 dict를 반환합니다."""
        base_score = technical_20 + vix_20 + fng_20 + econ_20 + other_20
        regime_score = max(0, min(100, base_score + phase_modifier))
        if regime_score >= 65:
            status = "Bull"
        elif regime_score <= bear_threshold:
            status = "Bear"
        else:
            status = "Neutral"
        current_price = float(close.iloc[-1])
        ema200 = ema_map[200]
        ma200  = float(close.rolling(window=200).mean().iloc[-1]) if len(close) >= 200 else ema200
        diff_pct = round((current_price - ma200) / ma200 * 100, 2) if ma200 else 0
        return {
            "status": status,
            "current": round(current_price, 2),
            "ma200": round(float(ma200 or 0), 2),
            "diff_pct": float(diff_pct),
            "regime_score": regime_score,
            "bear_threshold": bear_threshold,
            "economic_phase": economic_phase,
            "phase_modifier": phase_modifier,
            "ema": {f"ema{p}": round(v, 2) for p, v in ema_map.items()},
            "components": {
                "technical": technical_20, "technical_detail": tech_detail,
                "vix": vix_20, "fear_greed": fng_20, "economic": econ_20, "other": other_20,
                "other_detail": {
                    "us_10y_yield": round(us_10y_yield, 3),
                    "yield_spread_10y2y": yield_spread,
                    "vix_1m_chg": vix_1m_chg,
                    "btc_1m_ret": btc_ret, "dxy_1m_ret": dxy_ret,
                    "gold_1m_ret": gold_ret, "oil_1m_ret": oil_ret,
                    **other_scores,
                },
                "economic_phase_detail": {
                    "phase": economic_phase,
                    "modifier": phase_modifier,
                    "inflation_pressure": inflation_pressure,
                    "growth_signal": growth_signal,
                    **(inflation_detail or {}),
                },
            },
        }

    @classmethod
    def _get_market_regime(
        cls,
        vix: float | None = None,
        fear_greed: int | None = None,
        economic_indicators: dict | None = None,
        us_10y_yield: float | None = None,
    ) -> dict:
        """시장 국면 판단 (Bull/Bear/Neutral) 및 100점 기준 점수 계산 (5-Phase 경제 국면 반영)."""
        close, ndx_1m_hist = cls._fetch_spx_and_ndx_history()
        if close is None or close.empty:
            return {"status": "Unknown", "current": 0, "ma200": 0, "diff_pct": 0,
                    "regime_score": -1, "_fetch_failed": True}

        technical_20, tech_detail, ema_map = cls._calc_technical_20(close, ndx_1m_hist)

        if vix is None:
            vix = cls._get_vix()
        vix_1m_chg = cls._fetch_vix_1m_change(vix)
        vix_20 = cls._calc_vix_20(vix, vix_1m_chg)

        if fear_greed is None:
            fear_greed = cls._get_fear_greed_index()
        fng_20 = cls._calc_fng_20(fear_greed)

        if economic_indicators is None:
            economic_indicators = cls._get_economic_indicators()
        econ_20 = cls._calc_econ_20(economic_indicators)

        if us_10y_yield is None:
            us_10y_yield = cls._get_us_10y_yield()
        yield_spread, btc_ret, dxy_ret, gold_ret, oil_ret = cls._fetch_composite_assets(us_10y_yield)
        other_20, other_scores = cls._calc_composite_20(
            us_10y_yield, yield_spread, btc_ret, dxy_ret, gold_ret, oil_ret,
        )

        # 5-Phase 경제 국면 판별
        inflation_pressure, inflation_detail = cls._calc_inflation_pressure(oil_ret, economic_indicators)
        growth_signal, _growth_detail = cls._calc_growth_signal(tech_detail, vix, fear_greed, econ_20)
        economic_phase, phase_modifier = cls._determine_economic_phase(inflation_pressure, growth_signal)

        bear_threshold = cls._get_bear_threshold()
        return cls._assemble_regime_result(
            close, ema_map, technical_20, tech_detail, vix_20, fng_20, econ_20,
            other_20, other_scores, vix, vix_1m_chg, us_10y_yield,
            yield_spread, btc_ret, dxy_ret, gold_ret, bear_threshold,
            economic_phase=economic_phase, phase_modifier=phase_modifier,
            inflation_pressure=inflation_pressure, growth_signal=growth_signal,
            inflation_detail=inflation_detail, oil_ret=oil_ret,
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
        # CNN Fear & Greed 공개 엔드포인트 사용 (브라우저 헤더 필요)
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
        """섹터별 성과 (XLK, XLF 등)"""
        return {} # 필요 시 KIS로 개별 ETF 조회하도록 확장 가능

    @classmethod
    def _score_single_indicator(cls, key: str, series_id: str, latest, prev) -> dict:
        """지표 하나의 점수·상태 dict를 반환합니다."""
        rule = cls.MACRO_RULES.get(key, {})
        name, weight = rule.get("name", key), float(rule.get("weight", 1))
        higher_is_good = bool(rule.get("higher_is_good", True))
        if latest is None or prev is None:
            return {"name": name, "series_id": series_id, "weight": weight,
                    "latest": None, "previous": None, "delta": None,
                    "score": 0, "weighted_score": 0, "status": "no_data"}
        delta = latest - prev
        raw = (1 if higher_is_good else -1) if delta > 0 else (-1 if higher_is_good else 1) if delta < 0 else 0
        return {
            "name": name, "series_id": series_id, "weight": weight,
            "latest": round(float(latest), 4), "previous": round(float(prev), 4), "delta": round(float(delta), 4),
            "score": raw, "weighted_score": raw * weight,
            "status": "positive" if raw > 0 else "negative" if raw < 0 else "neutral",
        }

    @classmethod
    def _get_economic_indicators(cls) -> dict:
        """FRED 전체 14개 지표를 병렬 조회 후 가중치 기반 점수 산출."""
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
            if ind["status"] != "no_data":
                total_weighted_score += ind["weighted_score"]
                max_weighted_score += ind["weight"]

        sentiment_ratio = round(total_weighted_score / max_weighted_score, 4) if max_weighted_score > 0 else 0
        available = sum(1 for v in indicators.values() if v["status"] != "no_data")
        return {
            "indicators": indicators,
            "summary": {
                "total_weighted_score": round(total_weighted_score, 2),
                "max_weighted_score":   round(max_weighted_score, 2),
                "total_score":          round(total_weighted_score, 2),  # 하위호환
                "max_score":            round(max_weighted_score, 2),    # 하위호환
                "sentiment_ratio":      sentiment_ratio,
                "available_count":      available,
                "total_count":          len(all_keys),
            },
        }

    @staticmethod
    def _fetch_historical_spx(start_2y: str, start_1m: str, end_date: str) -> tuple:
        """과거 SPX 2년치 close + NDX 1개월 히스토리 조회."""
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
        """과거 VIX + 1개월 변화율 조회."""
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
        """과거 BTC/DXY/Gold/Oil 1개월 수익률 조회."""
        import yfinance as yf
        btc_ret = dxy_ret = gold_ret = oil_ret = None
        try:
            for sym, key in [("BTC-USD", "btc"), ("DX-Y.NYB", "dxy"), ("GC=F", "gold"), ("CL=F", "oil")]:
                try:
                    h = yf.Ticker(sym).history(start=start_1m, end=end_date)
                    if len(h) >= 5:
                        ret = round((float(h["Close"].iloc[-1]) / float(h["Close"].iloc[0]) - 1) * 100, 2)
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
        """특정 날짜의 시장 국면을 역사적 데이터로 계산 후 DB 저장.

        - SPX / VIX / BTC / DXY / Gold: yfinance 과거 데이터 사용
        - FRED 경제지표: 월별이므로 현재값과 동일하게 사용
        - Fear&Greed: 오늘 기준 7일 이내면 현재값, 그 외 중립(50)으로 추정
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

        technical_20, tech_detail, ema_map = cls._calc_technical_20(close, ndx_1m_hist)

        vix, vix_1m_chg = cls._fetch_historical_vix(start_1m, end_date)
        vix_20 = cls._calc_vix_20(vix, vix_1m_chg)

        days_diff  = (today_dt - target_dt).days
        fear_greed = cls._get_fear_greed_index() if days_diff <= 7 else 50
        fng_20     = cls._calc_fng_20(fear_greed)

        us_10y_yield = cls._get_us_10y_yield()
        yield_spread = None
        try:
            y2_val, _ = cls._get_fred_latest_pair("DGS2")
            if y2_val is not None:
                yield_spread = round(us_10y_yield - y2_val, 3)
        except Exception:
            pass

        economic_indicators = cls._get_economic_indicators()
        econ_20 = cls._calc_econ_20(economic_indicators)

        btc_ret, dxy_ret, gold_ret, oil_ret = cls._fetch_historical_composite_returns(start_1m, end_date)
        other_20, other_scores = cls._calc_composite_20(
            us_10y_yield, yield_spread, btc_ret, dxy_ret, gold_ret, oil_ret,
        )

        inflation_pressure, inflation_detail = cls._calc_inflation_pressure(oil_ret, economic_indicators)
        growth_signal, _growth_detail = cls._calc_growth_signal(tech_detail, vix, fear_greed, econ_20)
        economic_phase, phase_modifier = cls._determine_economic_phase(inflation_pressure, growth_signal)

        regime_data = cls._assemble_regime_result(
            close, ema_map, technical_20, tech_detail, vix_20, fng_20, econ_20,
            other_20, other_scores, vix, vix_1m_chg, us_10y_yield,
            yield_spread, btc_ret, dxy_ret, gold_ret, bear_threshold=40,
            economic_phase=economic_phase, phase_modifier=phase_modifier,
            inflation_pressure=inflation_pressure, growth_signal=growth_signal,
            inflation_detail=inflation_detail, oil_ret=oil_ret,
        )

        try:
            from services.market.stock_meta_service import StockMetaService
            StockMetaService.save_market_regime(date_str, regime_data, vix, fear_greed)
        except Exception:
            pass

        return {"date": date_str, "vix": vix, "fear_greed": fear_greed,
                "us_10y_yield": us_10y_yield, "market_regime": regime_data}

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
