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
        "cpi":                   {"higher_is_good": False, "name": "소비자 물가 지수(CPI)",   "weight": 3},
        "unemployment_rate":     {"higher_is_good": False, "name": "실업률",                  "weight": 3},
        "nonfarm_payrolls":      {"higher_is_good": True,  "name": "비농업 고용(NFP)",         "weight": 3},
        "pmi":                   {"higher_is_good": True,  "name": "제조업 생산 지수(PMI)",    "weight": 2},
        "consumer_confidence":   {"higher_is_good": True,  "name": "소비자 신뢰 지수",         "weight": 2},
        "ppi":                   {"higher_is_good": False, "name": "생산자 물가 지수(PPI)",    "weight": 2},
        "retail_sales":          {"higher_is_good": True,  "name": "소매 판매",                "weight": 2},
        "durable_goods_orders":  {"higher_is_good": True,  "name": "내구재 주문",              "weight": 2},
        "initial_jobless_claims":{"higher_is_good": False, "name": "실업 수당 청구",           "weight": 2},
        "industrial_production": {"higher_is_good": True,  "name": "산업 생산 지수",           "weight": 1},
        "capacity_utilization":  {"higher_is_good": True,  "name": "설비 가동률",              "weight": 1},
        "avg_hourly_earnings":   {"higher_is_good": False, "name": "시간당 평균 임금",          "weight": 1},
        "housing_starts":        {"higher_is_good": True,  "name": "주택 착공",                "weight": 1},
        "building_permits":      {"higher_is_good": True,  "name": "건축 허가",                "weight": 1},
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
        try:
            from services.market.stock_meta_service import StockMetaService
            today_str = datetime.now().strftime("%Y-%m-%d")
            StockMetaService.save_market_regime(
                today_str, market_regime, vix or 0, fear_greed or 50
            )
        except Exception as _e:
            pass  # DB 저장 실패가 응답을 막지 않도록

        return data

    @classmethod
    def invalidate_cache(cls):
        """경제지표 발표 후 macro 캐시를 강제 초기화 (다음 호출 시 전체 재계산)."""
        cls._cache.pop('macro', None)

    @classmethod
    def refresh_on_release(cls, release_name: str, series_ids: list) -> dict:
        """경제지표 발표 트리거 → 캐시 초기화 + 재계산 + DB 저장 + Slack 알림."""
        logger.info(f"📊 경제지표 발표 감지: {release_name} → regime 재계산 중...")
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
                f"📊 *경제지표 발표* — {release_name}\n"
                f"시장 국면 갱신: *{status}* ({score}/100)\n"
                f"VIX: {vix}  |  Fear&Greed: {fng}"
            )
        except Exception:
            pass
        logger.info(f"✅ regime 갱신 완료: {status} ({score}/100)")
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
    def _calc_technical_20(cls, close: pd.Series, ndx_1m_hist=None) -> tuple:
        """EMA 배열 + SPX/NDX/2주 모멘텀 + ATH드로다운 → (technical_20: 0~20, detail, ema_map)."""
        current_price = float(close.iloc[-1])
        ema_periods = [5, 20, 60, 120, 200]
        ema_map = {p: float(close.ewm(span=p, adjust=False).mean().iloc[-1]) for p in ema_periods}

        tech_raw = 0
        price_weights = {5: 12, 20: 16, 60: 16, 120: 20, 200: 24}
        for p, w in price_weights.items():
            tech_raw += w if current_price >= ema_map[p] else -w

        ema5, ema20, ema60, ema120, ema200 = (ema_map[p] for p in ema_periods)
        if ema5 > ema20 > ema60 > ema120 > ema200:
            tech_raw += 12
        elif ema5 < ema20 < ema60 < ema120 < ema200:
            tech_raw -= 12
        for p in [20, 60, 120]:
            ema_series = close.ewm(span=p, adjust=False).mean()
            slope_up = len(ema_series) >= 2 and float(ema_series.iloc[-1]) > float(ema_series.iloc[-2])
            tech_raw += 4 if slope_up else -4

        spx_1m_ret = None
        if len(close) >= 21:
            spx_1m_ret = round((float(close.iloc[-1]) / float(close.iloc[-21]) - 1) * 100, 2)
            if spx_1m_ret > 3:    tech_raw += 10
            elif spx_1m_ret > 1:  tech_raw += 5
            elif spx_1m_ret < -3: tech_raw -= 10
            elif spx_1m_ret < -1: tech_raw -= 5

        ndx_1m_ret = None
        if ndx_1m_hist is not None and len(ndx_1m_hist) >= 5:
            ndx_1m_ret = round(
                (float(ndx_1m_hist["Close"].iloc[-1]) / float(ndx_1m_hist["Close"].iloc[0]) - 1) * 100, 2
            )
            if ndx_1m_ret > 3:    tech_raw += 5
            elif ndx_1m_ret > 1:  tech_raw += 2
            elif ndx_1m_ret < -3: tech_raw -= 5
            elif ndx_1m_ret < -1: tech_raw -= 2

        spx_2w_ret = None
        if len(close) >= 11:
            spx_2w_ret = round((float(close.iloc[-1]) / float(close.iloc[-11]) - 1) * 100, 2)
            if spx_2w_ret > 2:      tech_raw += 6
            elif spx_2w_ret > 0.5:  tech_raw += 3
            elif spx_2w_ret < -2:   tech_raw -= 6
            elif spx_2w_ret < -0.5: tech_raw -= 3

        spx_from_ath = None
        if len(close) >= 252:
            ath_52w = float(close.iloc[-252:].max())
            if ath_52w > 0:
                spx_from_ath = round((current_price / ath_52w - 1) * 100, 2)
                if spx_from_ath < -20:   tech_raw -= 8
                elif spx_from_ath < -10: tech_raw -= 4
                elif spx_from_ath < -5:  tech_raw -= 2

        max_abs_tech = sum(price_weights.values()) + 12 + (4 * 3) + 10 + 5 + 6 + 8
        technical_score = max(-30, min(30, int(round((tech_raw / max_abs_tech) * 30))))
        technical_20 = cls._to_20(technical_score, 30)

        return technical_20, {
            "spx_1m_ret": spx_1m_ret,
            "ndx_1m_ret": ndx_1m_ret,
            "spx_2w_ret": spx_2w_ret,
            "spx_from_ath_pct": spx_from_ath,
        }, ema_map

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

    @staticmethod
    def _calc_composite_20(
        us_10y_yield: float,
        yield_spread: float | None,
        btc_ret: float | None,
        dxy_ret: float | None,
        gold_ret: float | None,
    ) -> tuple:
        """금리레벨(±8)+수익률곡선(±6)+DXY(±4)+BTC(±3)+Gold(±4) → (other_20: 0~20, score_detail)."""
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

        other_raw = yield_score + curve_score + dxy_score + btc_score + gold_score
        other_20  = MacroService._to_20(other_raw, 26)
        return other_20, {
            "yield_score": yield_score, "curve_score": curve_score,
            "dxy_score": dxy_score, "btc_score": btc_score, "gold_score": gold_score,
        }

    @classmethod
    def _fetch_composite_assets(cls, us_10y_yield: float) -> tuple:
        """수익률 곡선 스프레드 + BTC/DXY/Gold 1M 수익률 조회 → (yield_spread, btc_ret, dxy_ret, gold_ret)."""
        yield_spread = None
        try:
            y2_val, _ = cls._get_fred_latest_pair("DGS2")
            if y2_val is not None:
                yield_spread = round(us_10y_yield - y2_val, 3)
        except Exception:
            pass

        btc_ret = dxy_ret = gold_ret = None
        try:
            import yfinance as yf
            for sym, key in [("BTC-USD", "btc"), ("DX-Y.NYB", "dxy"), ("GC=F", "gold")]:
                try:
                    h = yf.Ticker(sym).history(period="1mo")
                    if len(h) >= 5:
                        ret = round((float(h["Close"].iloc[-1]) / float(h["Close"].iloc[0]) - 1) * 100, 2)
                        if key == "btc":    btc_ret  = ret
                        elif key == "dxy":  dxy_ret  = ret
                        elif key == "gold": gold_ret = ret
                except Exception:
                    pass
        except Exception:
            pass
        return yield_spread, btc_ret, dxy_ret, gold_ret

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
    def _get_market_regime(
        cls,
        vix: float | None = None,
        fear_greed: int | None = None,
        economic_indicators: dict | None = None,
        us_10y_yield: float | None = None,
    ) -> dict:
        """시장 국면 판단 (Bull/Bear/Neutral) 및 100점 기준 점수 계산.

        배점 구조 (각 20점, 합계 0~100, 중립=50):
          - 기술 (EMA 배열): 20점
          - VIX (공포지수): 20점
          - Fear&Greed:     20점
          - 경제지표(FRED): 20점
          - 기타(금리+BTC+Gold): 20점
        """
        # ── SPX 2년치 조회 ──────────────────────────────────────────────────
        hist = pd.DataFrame()
        try:
            import yfinance as yf
            raw = yf.Ticker("^GSPC").history(period="2y")
            if not raw.empty and "Close" in raw.columns:
                hist = raw[["Close"]].copy()
        except Exception:
            pass

        if hist.empty or "Close" not in hist.columns:
            return {"status": "Neutral", "current": 0, "ma200": 0, "diff_pct": 0, "regime_score": 50}

        close = pd.to_numeric(hist["Close"], errors="coerce").dropna()
        if close.empty:
            return {"status": "Neutral", "current": 0, "ma200": 0, "diff_pct": 0, "regime_score": 50}

        # ── NDX 1개월 히스토리 ──────────────────────────────────────────────
        ndx_1m_hist = None
        try:
            import yfinance as yf
            ndx_1m_hist = yf.Ticker("^NDX").history(period="1mo")
        except Exception:
            pass

        # ── 1. 기술 점수 ─────────────────────────────────────────────────────
        technical_20, tech_detail, ema_map = cls._calc_technical_20(close, ndx_1m_hist)

        # ── 2. VIX 점수 ──────────────────────────────────────────────────────
        if vix is None:
            vix = cls._get_vix()
        vix_1m_chg = None
        try:
            import yfinance as yf
            vix_h = yf.Ticker("^VIX").history(period="1mo")
            if len(vix_h) >= 5:
                vix_prev = float(vix_h["Close"].iloc[0])
                if vix_prev > 0:
                    vix_1m_chg = round((vix - vix_prev) / vix_prev * 100, 1)
        except Exception:
            pass
        vix_20 = cls._calc_vix_20(vix, vix_1m_chg)

        # ── 3. Fear&Greed 점수 ───────────────────────────────────────────────
        if fear_greed is None:
            fear_greed = cls._get_fear_greed_index()
        fng_20 = cls._calc_fng_20(fear_greed)

        # ── 4. 경제지표 점수 ─────────────────────────────────────────────────
        if economic_indicators is None:
            economic_indicators = cls._get_economic_indicators()
        econ_20 = cls._calc_econ_20(economic_indicators)

        # ── 5. 기타 복합 지표 ────────────────────────────────────────────────
        if us_10y_yield is None:
            us_10y_yield = cls._get_us_10y_yield()
        yield_spread, btc_ret, dxy_ret, gold_ret = cls._fetch_composite_assets(us_10y_yield)
        other_20, other_scores = cls._calc_composite_20(us_10y_yield, yield_spread, btc_ret, dxy_ret, gold_ret)

        # ── 합산 + Bear 지속성 ───────────────────────────────────────────────
        regime_score  = max(0, min(100, technical_20 + vix_20 + fng_20 + econ_20 + other_20))
        bear_threshold = cls._get_bear_threshold()

        if regime_score >= 65:
            status = "Bull"
        elif regime_score <= bear_threshold:
            status = "Bear"
        else:
            status = "Neutral"

        current_price = float(close.iloc[-1])
        ema200 = ema_map[200]
        ma200  = float(close.rolling(window=200).mean().iloc[-1]) if len(close) >= 200 else ema200
        diff_pct = (current_price - ma200) / ma200 * 100 if ma200 else 0

        return {
            "status": status,
            "current": round(current_price, 2),
            "ma200": round(float(ma200 or 0), 2),
            "diff_pct": round(float(diff_pct), 2),
            "regime_score": regime_score,
            "bear_threshold": bear_threshold,
            "ema": {f"ema{p}": round(v, 2) for p, v in ema_map.items()},
            "components": {
                "technical": technical_20,
                "technical_detail": tech_detail,
                "vix": vix_20,
                "fear_greed": fng_20,
                "economic": econ_20,
                "other": other_20,
                "other_detail": {
                    "us_10y_yield": round(us_10y_yield, 3),
                    "yield_spread_10y2y": yield_spread,
                    "vix_1m_chg": vix_1m_chg,
                    "btc_1m_ret": btc_ret,
                    "dxy_1m_ret": dxy_ret,
                    "gold_1m_ret": gold_ret,
                    **other_scores,
                },
            },
        }

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
    def _get_economic_indicators(cls) -> dict:
        """FRED 전체 14개 지표를 병렬 조회 후 가중치 기반 점수 산출."""
        all_keys = list(cls.FRED_SERIES.keys())

        # 병렬 FRED 조회 (최대 8 스레드)
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
            rule = cls.MACRO_RULES.get(key, {})
            higher_is_good = bool(rule.get("higher_is_good", True))
            name = rule.get("name", key)
            weight = float(rule.get("weight", 1))

            if latest is None or prev is None:
                indicators[key] = {
                    "name": name,
                    "series_id": series_id,
                    "weight": weight,
                    "latest": None,
                    "previous": None,
                    "delta": None,
                    "score": 0,
                    "weighted_score": 0,
                    "status": "no_data",
                }
                continue

            delta = latest - prev
            raw_score = 0
            if delta > 0:
                raw_score = 1 if higher_is_good else -1
            elif delta < 0:
                raw_score = -1 if higher_is_good else 1

            weighted_score = raw_score * weight
            status = "positive" if raw_score > 0 else "negative" if raw_score < 0 else "neutral"

            indicators[key] = {
                "name": name,
                "series_id": series_id,
                "weight": weight,
                "latest": round(float(latest), 4),
                "previous": round(float(prev), 4),
                "delta": round(float(delta), 4),
                "score": raw_score,
                "weighted_score": weighted_score,
                "status": status,
            }
            total_weighted_score += weighted_score
            max_weighted_score += weight

        sentiment_ratio = round(total_weighted_score / max_weighted_score, 4) if max_weighted_score > 0 else 0
        available = sum(1 for v in indicators.values() if v["status"] != "no_data")
        return {
            "indicators": indicators,
            "summary": {
                "total_weighted_score": round(total_weighted_score, 2),
                "max_weighted_score": round(max_weighted_score, 2),
                "total_score": round(total_weighted_score, 2),   # 하위호환
                "max_score": round(max_weighted_score, 2),       # 하위호환
                "sentiment_ratio": sentiment_ratio,
                "available_count": available,
                "total_count": len(all_keys),
            },
        }

    @classmethod
    def calculate_historical_regime(cls, date_str: str) -> dict:
        """특정 날짜의 시장 국면을 역사적 데이터로 계산 후 DB 저장.

        - SPX / VIX / BTC / DXY / Gold: yfinance 과거 데이터 사용
        - FRED 경제지표: 월별이므로 현재값과 동일하게 사용
        - Fear&Greed: 오늘 기준 7일 이내면 현재값, 그 외 중립(50)으로 추정
        """
        import yfinance as yf
        from datetime import datetime, timedelta

        target_dt = datetime.strptime(date_str, "%Y-%m-%d")
        today_dt  = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        end_date  = (target_dt + timedelta(days=1)).strftime("%Y-%m-%d")
        start_2y  = (target_dt - timedelta(days=730)).strftime("%Y-%m-%d")
        start_1m  = (target_dt - timedelta(days=32)).strftime("%Y-%m-%d")

        # ── SPX 2년치 ──────────────────────────────────────────────────────
        hist = pd.DataFrame()
        try:
            raw = yf.Ticker("^GSPC").history(start=start_2y, end=end_date)
            if not raw.empty and "Close" in raw.columns:
                hist = raw[["Close"]].copy()
        except Exception:
            pass

        if hist.empty:
            return {"error": f"SPX 데이터 없음 for {date_str}"}

        close = pd.to_numeric(hist["Close"], errors="coerce").dropna()

        # ── NDX 1개월 히스토리 ────────────────────────────────────────────
        ndx_1m_hist = None
        try:
            ndx_1m_hist = yf.Ticker("^NDX").history(start=start_1m, end=end_date)
        except Exception:
            pass

        # ── 1. 기술 점수 ─────────────────────────────────────────────────
        technical_20, tech_detail, ema_map = cls._calc_technical_20(close, ndx_1m_hist)

        # ── 2. VIX 점수 ──────────────────────────────────────────────────
        vix = 20.0
        vix_1m_chg = None
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
        vix_20 = cls._calc_vix_20(vix, vix_1m_chg)

        # ── 3. Fear&Greed 점수 ─────────────────────────────────────────
        days_diff  = (today_dt - target_dt).days
        fear_greed = cls._get_fear_greed_index() if days_diff <= 7 else 50
        fng_20     = cls._calc_fng_20(fear_greed)

        # ── 4. 경제지표 점수 (FRED 월별, 현재값 사용) ────────────────────
        economic_indicators = cls._get_economic_indicators()
        econ_20 = cls._calc_econ_20(economic_indicators)

        # ── 5. 기타 복합 지표 ────────────────────────────────────────────
        us_10y_yield = cls._get_us_10y_yield()
        yield_spread = None
        try:
            y2_val, _ = cls._get_fred_latest_pair("DGS2")
            if y2_val is not None:
                yield_spread = round(us_10y_yield - y2_val, 3)
        except Exception:
            pass

        btc_ret = dxy_ret = gold_ret = None
        try:
            for sym, key in [("BTC-USD", "btc"), ("DX-Y.NYB", "dxy"), ("GC=F", "gold")]:
                try:
                    h = yf.Ticker(sym).history(start=start_1m, end=end_date)
                    if len(h) >= 5:
                        ret = round((float(h["Close"].iloc[-1]) / float(h["Close"].iloc[0]) - 1) * 100, 2)
                        if key == "btc":    btc_ret  = ret
                        elif key == "dxy":  dxy_ret  = ret
                        elif key == "gold": gold_ret = ret
                except Exception:
                    pass
        except Exception:
            pass

        other_20, other_scores = cls._calc_composite_20(us_10y_yield, yield_spread, btc_ret, dxy_ret, gold_ret)

        # ── 합산 ──────────────────────────────────────────────────────────
        regime_score  = max(0, min(100, technical_20 + vix_20 + fng_20 + econ_20 + other_20))
        status = "Bull" if regime_score >= 65 else "Bear" if regime_score <= 40 else "Neutral"

        current_price = float(close.iloc[-1])
        ema200   = ema_map[200]
        ma200    = float(close.rolling(window=200).mean().iloc[-1]) if len(close) >= 200 else ema200
        diff_pct = round((current_price - ma200) / ma200 * 100, 2) if ma200 else 0

        regime_data = {
            "status":       status,
            "current":      round(current_price, 2),
            "ma200":        round(float(ma200), 2),
            "diff_pct":     diff_pct,
            "regime_score": regime_score,
            "ema":          {f"ema{p}": round(v, 2) for p, v in ema_map.items()},
            "components": {
                "technical":        technical_20,
                "technical_detail": tech_detail,
                "vix":              vix_20,
                "fear_greed":       fng_20,
                "economic":         econ_20,
                "other":            other_20,
                "other_detail": {
                    "us_10y_yield":       round(us_10y_yield, 3),
                    "yield_spread_10y2y": yield_spread,
                    "vix_1m_chg":         vix_1m_chg,
                    "btc_1m_ret":         btc_ret,
                    "dxy_1m_ret":         dxy_ret,
                    "gold_1m_ret":        gold_ret,
                    **other_scores,
                },
            },
        }

        try:
            from services.market.stock_meta_service import StockMetaService
            StockMetaService.save_market_regime(date_str, regime_data, vix, fear_greed)
        except Exception:
            pass

        return {
            "date":          date_str,
            "vix":           vix,
            "fear_greed":    fear_greed,
            "us_10y_yield":  us_10y_yield,
            "market_regime": regime_data,
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
