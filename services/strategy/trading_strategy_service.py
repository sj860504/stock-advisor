import json
import os
from config import Config
from typing import Optional
from datetime import datetime, timedelta
import pytz
from services.market.macro_service import MacroService
from services.trading.portfolio_service import PortfolioService
from services.market.market_data_service import MarketDataService # 추가
from services.market.market_hour_service import MarketHourService
from services.market.data_service import DataService
from services.kis.kis_service import KisService
from services.market.stock_meta_service import StockMetaService
from services.notification.alert_service import AlertService
from services.config.settings_service import SettingsService
from services.trading.order_service import OrderService
from utils.logger import get_logger
from utils.market import is_kr, filter_kr, filter_us

logger = get_logger("strategy_service")

# 캐시 TTL (초)
TOP10_CACHE_TTL_SEC = 6 * 60 * 60


class TradingStrategyService:
    """
    사용자의 투자 전략에 따른 매매 시그널 판단 및 실행 서비스
    """
    _state_path = os.path.join(os.path.dirname(__file__), "..", "data", "strategy_state.json")
    _enabled = False
    _top10_cache = {"timestamp": 0, "tickers": set()}

    # 전략 설정 상수 (SettingsService 연동을 위해 클래스 변수 제거 또는 프로퍼티화)
    # 여기서는 메서드 내에서 호출하도록 변경

    # 가중치 설정
    # 기준: base_score=50, BUY ≤30, SELL ≥70
    # → 매수 신호는 음수(점수 하락), 매도 신호는 양수(점수 상승)
    WEIGHTS = {
        'RSI_OVERSOLD': -20, 'RSI_OVERBOUGHT': +15,   # 과매도=매수, 과매수=매도
        'DIP_BUY_5PCT': -15, 'SURGE_SELL_5PCT': +15,  # 급락=매수, 급등=매도
        'SUPPORT_EMA': -10, 'RESISTANCE_EMA': +10,    # EMA지지=매수, EMA저항=매도
        'ADD_POSITION_LOSS': -10, 'GOLDEN_CROSS_DROP': +15,
        'PANIC_MARKET_BUY': -30, 'PROFIT_TAKE_TARGET': +30,  # 공포=매수, 익절권=매도
        'BULL_MARKET_SECTOR': -15, 'CASH_PENALTY': +15,      # 상승장=매수우위, 현금부족=매도유도
        # DCF 기반 가치평가 가중치
        'DCF_UNDERVALUE_HIGH': -25,  # DCF 대비 20% 이상 저평가 → 매수
        'DCF_UNDERVALUE_MID': -15,   # DCF 대비 10~20% 저평가
        'DCF_UNDERVALUE_LOW': -10,   # DCF 대비 5~10% 저평가
        'DCF_FAIR_VALUE': -5,        # DCF ±5% (적정가, 약한 매수 우위)
        'DCF_OVERVALUE_LOW': +10,    # DCF 대비 5~15% 고평가 → 매도
        'DCF_OVERVALUE_HIGH': +20,   # DCF 대비 15% 이상 고평가
    }

    # ── 섹터 그룹 목표 비중 (주식 자산 내) ────────────────────────────────────
    # StockMeta.sector 문자열 → 그룹 키 (GICS + 한국어 섹터명 매핑)
    SECTOR_GROUP_MAP: dict = {
        # 기술주
        "Technology": "tech", "IT": "tech", "기술": "tech",
        "Information Technology": "tech",
        "Communication Services": "tech", "통신서비스": "tech", "통신": "tech",
        # 가치주 (방어적: 필수소비재 + 헬스케어 + 유틸리티)
        "Consumer Staples": "value", "Consumer Defensive": "value", "필수소비재": "value",
        "Healthcare": "value", "Health Care": "value", "헬스케어": "value", "의료": "value",
        "Utilities": "value", "유틸리티": "value",
        # 금융주
        "Financials": "financial", "Financial": "financial",
        "Financial Services": "financial", "금융": "financial", "은행": "financial",
    }
    # 그룹별 목표 비중 (합계 = 1.0, 주식 자산 대비)
    SECTOR_TARGET_WEIGHT: dict = {"tech": 0.50, "value": 0.30, "financial": 0.20}
    # 리밸런싱 편차 임계값: 목표 대비 ±5% 이탈 시 신호 발생
    SECTOR_REBAL_THRESHOLD: float = 0.05

    @classmethod
    def set_enabled(cls, enabled: bool):
        cls._enabled = enabled
        logger.info(f"⚙️ Trading Strategy Engine {'ENABLED' if enabled else 'DISABLED'}")
        # enabled 상태를 파일에 영속적으로 저장 (재시작 후 복원)
        try:
            state = cls._load_state()
            state["_enabled"] = enabled
            cls._save_state(state)
        except Exception as e:
            logger.warning(f"⚠️ Failed to persist strategy enabled state: {e}")

    @classmethod
    def is_enabled(cls) -> bool:
        return cls._enabled

    @classmethod
    def _restore_enabled_state(cls):
        """앱 시작 시 저장된 enabled 상태를 복원합니다."""
        try:
            state = cls._load_state()
            persisted = state.get("_enabled")
            if persisted is True:
                cls._enabled = True
                logger.info("⚙️ Trading Strategy Engine restored: ENABLED (from last session)")
            else:
                logger.info("⚙️ Trading Strategy Engine restored: DISABLED (default or last session)")
        except Exception as e:
            logger.warning(f"⚠️ Failed to restore strategy enabled state: {e}")

    @classmethod
    def _load_state(cls):
        if os.path.exists(cls._state_path):
            with open(cls._state_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}

    @classmethod
    def _save_state(cls, state):
        with open(cls._state_path, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

    @classmethod
    def _get_ticker_market(cls, ticker: str) -> str:
        return "KR" if is_kr(ticker) else "US"

    @classmethod
    def _get_ticker_sector(cls, ticker: str, holding: Optional[dict] = None) -> str:
        if holding and holding.get("sector"):
            return (getattr(holding, "sector", None) if not isinstance(holding, dict) else holding.get("sector", None))
        meta = StockMetaService.get_stock_meta(ticker)
        return meta.sector if meta and meta.sector else "Others"

    @classmethod
    def _get_sector_group(cls, ticker: str, holding: Optional[dict] = None) -> str:
        """SECTOR_GROUP_MAP 기반 섹터 그룹 반환 ('tech'/'value'/'financial'/'other')."""
        sector = cls._get_ticker_sector(ticker, holding)
        return cls.SECTOR_GROUP_MAP.get(sector, "other")

    @classmethod
    def _get_sector_group_weights(cls, holdings: list, exchange_rate: float = 1400.0) -> dict:
        """주식 자산 내 섹터 그룹 현재 비중 및 목표 대비 편차 반환.

        Returns:
            {
              "total_stock_krw": float,
              "weights": {
                  "tech":      {"value_krw": float, "weight": float, "target": float, "dev": float},
                  "value":     {...},
                  "financial": {...},
                  "other":     {...},
              }
            }
        """
        group_values: dict = {"tech": 0.0, "value": 0.0, "financial": 0.0, "other": 0.0}
        for h in holdings:
            if h.get("quantity", 0) <= 0:
                continue
            val = cls._get_holding_value(h)
            if val <= 0:
                continue
            ticker = h.get("ticker", "")
            if not is_kr(ticker):   # 미국 주식 → KRW 변환
                val *= exchange_rate
            grp = cls._get_sector_group(ticker, h)
            group_values[grp] = group_values.get(grp, 0.0) + val

        total = sum(group_values.values())
        weights: dict = {}
        for grp, val in group_values.items():
            w = val / total if total > 0 else 0.0
            target = cls.SECTOR_TARGET_WEIGHT.get(grp, 0.0)
            weights[grp] = {"value_krw": round(val), "weight": round(w, 4),
                            "target": target, "dev": round(w - target, 4)}
        return {"total_stock_krw": round(total), "weights": weights}

    @classmethod
    def get_sector_rebalance_status(cls, user_id: str = "sean") -> dict:
        """섹터 비중 현황 및 리밸런싱 필요 종목 반환 (API용).

        Returns:
            {
              "weights": {...},          # 그룹별 현재/목표/편차
              "underweight": [...],      # 매수 우선 섹터 종목 목록
              "overweight": [...],       # 매도 고려 섹터 종목 목록
            }
        """
        exchange_rate = MacroService.get_exchange_rate()
        holdings = PortfolioService.load_portfolio(user_id)
        sw = cls._get_sector_group_weights(holdings, exchange_rate)
        weights = sw["weights"]

        underweight, overweight = [], []
        for h in holdings:
            if h.get("quantity", 0) <= 0:
                continue
            ticker = h.get("ticker", "")
            grp = cls._get_sector_group(ticker, h)
            if grp == "other":
                continue
            dev = weights.get(grp, {}).get("dev", 0.0)
            entry = {
                "ticker": ticker,
                "name": h.get("name", ""),
                "group": grp,
                "dev": round(dev, 4),
                "current_weight": weights.get(grp, {}).get("weight", 0),
                "target_weight": weights.get(grp, {}).get("target", 0),
            }
            if dev < -cls.SECTOR_REBAL_THRESHOLD:
                entry["action"] = "buy_priority"
                underweight.append(entry)
            elif dev > cls.SECTOR_REBAL_THRESHOLD:
                entry["action"] = "sell_consider"
                overweight.append(entry)

        underweight.sort(key=lambda x: x["dev"])           # 가장 부족한 순
        overweight.sort(key=lambda x: -x["dev"])           # 가장 초과한 순
        return {"weights": weights, "underweight": underweight, "overweight": overweight,
                "total_stock_krw": sw["total_stock_krw"]}

    @classmethod
    def _get_holding_value(cls, holding: dict) -> float:
        price = holding.get("current_price") or holding.get("buy_price") or 0
        if price <= 0:
            state = MarketDataService.get_state(holding.get("ticker", ""))
            if state and state.current_price:
                price = state.current_price
        return max(0.0, float(price)) * float(holding.get("quantity", 0))

    @classmethod
    def _is_panic_market(cls, macro: dict) -> bool:
        vix = macro.get("vix", 20.0)
        fng = macro.get("fear_greed", 50)
        return vix >= 25 or fng <= 30

    @classmethod
    def _get_target_cash_ratio(cls, market: str, regime_status: str) -> float:
        """시장 국면에 따른 목표 현금 비중 조회 (한국/미국 분리)"""
        regime_key = regime_status.upper()
        if regime_key not in ['BEAR', 'NEUTRAL', 'BULL']:
            regime_key = 'NEUTRAL'
        
        market_key = 'KR' if market == 'KR' else 'US'
        setting_key = f"STRATEGY_TARGET_CASH_RATIO_{market_key}_{regime_key}"
        default_ratios = {
            'KR': {'BEAR': 0.20, 'NEUTRAL': 0.40, 'BULL': 0.50},
            'US': {'BEAR': 0.20, 'NEUTRAL': 0.40, 'BULL': 0.50}
        }
        default = default_ratios.get(market_key, {}).get(regime_key, 0.40)
        return SettingsService.get_float(setting_key, default)

    @classmethod
    def _passes_allocation_limits(
        cls,
        ticker: str,
        add_value: float,
        holdings: list,
        total_assets: float,
        cash_balance: float,
        holding: Optional[dict] = None,
        kr_assets: float = 0.0,
        us_assets_krw: float = 0.0
    ) -> tuple:
        """시장/섹터 비중 제한 검사 (한국/미국 분리)"""
        if total_assets <= 0:
            return True, []

        from services.market.macro_service import MacroService
        from services.trading.portfolio_service import PortfolioService
        exchange_rate = MacroService.get_exchange_rate()

        market = cls._get_ticker_market(ticker)
        sector = cls._get_ticker_sector(ticker, holding)

        # 한국/미국 자산 분리 계산
        kr_holdings = filter_kr(holdings)
        us_holdings = filter_us(holdings)
        
        kr_market_value = sum(cls._get_holding_value(h) for h in kr_holdings if h.get("quantity", 0) > 0)
        # 미국 주식은 USD 가격이므로 KRW로 변환
        us_market_value_krw = sum(cls._get_holding_value(h) * exchange_rate for h in us_holdings if h.get("quantity", 0) > 0)
        
        # 현금 포함 (한국/미국 분리)
        if kr_assets <= 0:
            kr_cash = cash_balance
        else:
            kr_cash = kr_assets - kr_market_value
        
        if us_assets_krw <= 0:
            usd_cash = PortfolioService.get_usd_cash_balance()
            us_cash_krw = usd_cash * exchange_rate
        else:
            us_cash_krw = us_assets_krw - us_market_value_krw
        
        # 추가 매수 반영
        if market == "KR":
            kr_market_value += add_value
        else:
            us_market_value_krw += add_value  # add_value는 이미 KRW 기준

        # 섹터 비중 계산 (KRW 기준)
        sector_values = {}
        for h in holdings:
            if h.get("quantity", 0) <= 0:
                continue
            value = cls._get_holding_value(h)  # 이미 KRW로 변환됨
            if value <= 0:
                continue
            sec = cls._get_ticker_sector(h["ticker"], h)
            sector_values[sec] = sector_values.get(sec, 0.0) + value
        
        # 추가 매수 반영
        sector_values[sector] = sector_values.get(sector, 0.0) + add_value

        # KR/US는 독립 포트폴리오로 관리 — 전체 자산 기준 시장비중 제한 없음.
        # 현금 비중은 _is_cash_ratio_sufficient에서 각 시장 내부 기준으로 별도 체크.
        max_sector = SettingsService.get_float("STRATEGY_MAX_SECTOR_RATIO", 0.3)

        reasons = []
        if max_sector > 0:
            ratio = sector_values.get(sector, 0.0) / total_assets if total_assets > 0 else 0
            if ratio > max_sector:
                reasons.append(f"섹터비중초과({sector} {ratio:.2%} > {max_sector:.2%})")

        # 섹터 그룹 목표 비중 소프트 경고 (차단 아님 — calculate_score에서 점수로 반영)
        grp = cls._get_sector_group(ticker, holding)
        if grp != "other":
            sw = cls._get_sector_group_weights(holdings, exchange_rate)
            grp_info = sw["weights"].get(grp, {})
            grp_weight = grp_info.get("weight", 0.0)
            grp_target = cls.SECTOR_TARGET_WEIGHT.get(grp, 0.0)
            if grp_weight > grp_target + cls.SECTOR_REBAL_THRESHOLD:
                reasons.append(
                    f"섹터그룹비중초과({grp} {grp_weight:.1%} > 목표 {grp_target:.1%})"
                )

        return len(reasons) == 0, reasons

    @classmethod
    def _get_global_state(cls) -> dict:
        state = cls._load_state()
        if "_global" not in state:
            state["_global"] = {}
        return state

    @classmethod
    def get_top_weight_overrides(cls) -> dict:
        """티커별 사용자 가중치 오버라이드 조회"""
        state = cls._get_global_state()
        global_state = state.get("_global", {})
        return global_state.get("top_weight_overrides", {})

    @classmethod
    def set_top_weight_overrides(cls, overrides: dict) -> dict:
        """티커별 사용자 가중치 오버라이드 저장"""
        state = cls._get_global_state()
        state["_global"]["top_weight_overrides"] = overrides or {}
        cls._save_state(state)
        return state["_global"]["top_weight_overrides"]

    @classmethod
    def _get_top10_market_cap_tickers(cls) -> set:
        """미국/한국 시가총액 상위 10개 티커 캐시 반환"""
        now = datetime.now().timestamp()
        if now - cls._top10_cache["timestamp"] < TOP10_CACHE_TTL_SEC:
            return cls._top10_cache["tickers"]
        
        try:
            kr_top = DataService.get_top_krx_tickers(limit=100)[:10]
            us_top = DataService.get_top_us_tickers(limit=100)[:10]
            top10 = set(kr_top + us_top)
        except Exception as e:
            logger.warning(f"⚠️ Failed to refresh top10 market cap tickers: {e}")
            top10 = cls._top10_cache["tickers"]
        
        cls._top10_cache = {"timestamp": now, "tickers": top10}
        return top10

    @classmethod
    def _is_near_market_close(cls, ticker: str, minutes: int = 5) -> bool:
        allow_extended = SettingsService.get_int("STRATEGY_ALLOW_EXTENDED_HOURS", 1) == 1
        if is_kr(ticker):
            tz = pytz.timezone("Asia/Seoul")
            now = datetime.now(tz)
            kr_allow_extended = allow_extended and (not Config.KIS_IS_VTS)
            end_h, end_m = (18, 0) if kr_allow_extended else (15, 30)
            close_time = now.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
            return now.weekday() < 5 and (close_time - timedelta(minutes=minutes)) <= now <= close_time
        tz = pytz.timezone("America/New_York")
        now = datetime.now(tz)
        end_h, end_m = (20, 0) if allow_extended else (16, 0)
        close_time = now.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
        return now.weekday() < 5 and (close_time - timedelta(minutes=minutes)) <= now <= close_time

    @classmethod
    def _evaluate_tick_sell_conditions(cls, ticker: str, holding: dict, state, pnl_pct: float, tp_pct: float, sl_pct: float, trade_state: dict) -> bool:
        """틱매매 매도 조건 확인 및 실행"""
        hold_qty = int(holding.get("quantity", 0))
        if hold_qty > 0 and (pnl_pct >= tp_pct or pnl_pct <= sl_pct):
            result = KisService.send_order(ticker, hold_qty, 0, "sell")
            if result.get("status") == "success":
                reason = "Tick TP" if pnl_pct >= tp_pct else "Tick SL"
                OrderService.record_trade(ticker, "sell", hold_qty, getattr(state, 'current_price', 0), reason, "tick_strategy")
                cls._send_tick_alert(ticker, "sell", getattr(state, 'current_price', 0), hold_qty, reason, pnl_pct, holding)
                trade_state.update({"second_done": False, "last_sell_price": float(getattr(state, 'current_price', 0))})
                return True
        return False

    @classmethod
    def _evaluate_tick_buy_conditions(cls, ticker: str, tranche: float, state, holding: dict, pnl_pct: float, add_pct: float, trade_state: dict, low_1h: float, entry_pct: float) -> bool:
        """틱매매 매수(초기/추가) 조건 확인 및 실행"""
        current_price = getattr(state, 'current_price', 0)
        qty = int(tranche // current_price) if current_price > 0 else 0
        if qty <= 0: return False

        if holding and not trade_state.get("second_done") and pnl_pct <= add_pct:
            result = KisService.send_order(ticker, qty, 0, "buy")
            if result.get("status") == "success":
                OrderService.record_trade(ticker, "buy", qty, current_price, "Tick Add", "tick_strategy")
                cls._send_tick_alert(ticker, "buy", current_price, qty, "Tick Add", holding=holding)
                trade_state["second_done"] = True
                return True
        elif not holding:
            last_sell = trade_state.get("last_sell_price")
            reentry = float(last_sell) * (1 + entry_pct / 100.0) if last_sell else None
            trigger = float(current_price) <= reentry if reentry else float(current_price) <= low_1h * 1.001
            if trigger:
                result = KisService.send_order(ticker, qty, 0, "buy")
                if result.get("status") == "success":
                    reason = "Tick ReEntry" if reentry else "Tick Entry (1h low)"
                    OrderService.record_trade(ticker, "buy", qty, current_price, reason, "tick_strategy")
                    cls._send_tick_alert(ticker, "buy", current_price, qty, reason)
                    trade_state["second_done"] = False
                    return True
        return False

    @classmethod
    def _run_tick_trade(cls, user_id: str, holdings: list, total_assets: float, cash_balance: float) -> bool:
        """하루 1종목 틱매매 (진입/청산/유지)"""
        if SettingsService.get_int("STRATEGY_TICK_ENABLED", 0) != 1: return False
        ticker = (SettingsService.get_setting("STRATEGY_TICK_TICKER", "005930") or "").strip().upper()
        if not ticker: return False

        MarketDataService.register_ticker(ticker)
        state = MarketDataService.get_state(ticker)
        if not state or getattr(state, 'current_price', 0) <= 0: return False
        current_price = getattr(state, 'current_price', 0)

        allow_ext = SettingsService.get_int("STRATEGY_ALLOW_EXTENDED_HOURS", 1) == 1
        if (is_kr(ticker) and not MarketHourService.is_kr_market_open(allow_extended=allow_ext)) or \
           (not is_kr(ticker) and not MarketHourService.is_us_market_open(allow_extended=allow_ext)): return False

        tick_state = cls._load_state()
        user_state = tick_state.setdefault(user_id, {})
        today = datetime.now().strftime("%Y-%m-%d")
        trade_state = user_state.get("tick_trade", {"date": today, "second_done": False, "last_sell_price": None, "price_window": []})
        if trade_state.get("date") != today:
            trade_state = {"date": today, "second_done": False, "last_sell_price": None, "price_window": []}

        now_ts = datetime.now().timestamp()
        pw = [p for p in trade_state.get("price_window", []) if p[0] >= now_ts - 3600]
        pw.append([now_ts, float(current_price)])
        trade_state["price_window"] = pw
        low_1h = min((p[1] for p in pw), default=float(current_price))

        holding = next((h for h in holdings if h["ticker"] == ticker), None)
        close_min = SettingsService.get_int("STRATEGY_TICK_CLOSE_MINUTES", 5)
        
        if holding and cls._is_near_market_close(ticker, close_min):
            qty = int(holding.get("quantity", 0))
            if qty > 0 and KisService.send_order(ticker, qty, 0, "sell").get("status") == "success":
                OrderService.record_trade(ticker, "sell", qty, current_price, "Tick EOD", "tick_strategy")
                holding_eod = next((h for h in holdings if h["ticker"] == ticker), None)
                buy_p = float(holding_eod.get("buy_price", 0)) if holding_eod else 0
                pnl_eod = (current_price - buy_p) / buy_p * 100 if buy_p else 0
                cls._send_tick_alert(ticker, "sell", current_price, qty, "Tick EOD", pnl_eod, holding_eod)
                trade_state.update({"second_done": False, "last_sell_price": float(current_price)})
                user_state["tick_trade"] = trade_state
                cls._save_state(tick_state)
                return True
            return False

        tranche = min(cash_balance, max(0.0, total_assets * SettingsService.get_float("STRATEGY_TICK_CASH_RATIO", 0.2))) / 2
        buy_price = float(holding.get("buy_price", 1)) if holding and float(holding.get("buy_price", 1)) > 0 else 1.0
        pnl_pct = (current_price - buy_price) / buy_price * 100 if holding else 0

        executed = cls._evaluate_tick_sell_conditions(ticker, holding, state, pnl_pct, SettingsService.get_float("STRATEGY_TICK_TAKE_PROFIT_PCT", 1.0), SettingsService.get_float("STRATEGY_TICK_STOP_LOSS_PCT", -5.0), trade_state) if holding else False
        if not executed and tranche > 0:
            executed = cls._evaluate_tick_buy_conditions(ticker, tranche, state, holding, pnl_pct, SettingsService.get_float("STRATEGY_TICK_ADD_PCT", -3.0), trade_state, low_1h, SettingsService.get_float("STRATEGY_TICK_ENTRY_PCT", -1.0))

        user_state["tick_trade"] = trade_state
        cls._save_state(tick_state)
        return executed

    @classmethod
    def _update_target_universe(cls, user_id: str) -> set:
        """Top 100 변경 감지 및 유니버스 정리"""
        from utils.logger import get_logger
        logger = get_logger("strategy_service")
        def _norm_ticker(t):
            t = str(t or "").strip().upper()
            if not t: return ""
            if t.isdigit() and len(t) < 6: t = t.zfill(6)
            return t
        
        kr_tickers = [_norm_ticker(t) for t in DataService.get_top_krx_tickers(limit=100)]
        us_tickers = [_norm_ticker(t) for t in DataService.get_top_us_tickers(limit=100)]
        portfolio = PortfolioService.load_portfolio(user_id)
        holdings = [_norm_ticker(h.get('ticker')) for h in portfolio]
        
        kr_holdings = [t for t in holdings if t and is_kr(t) and len(t) == 6]
        us_holdings = [t for t in holdings if t and t.isalpha()]

        all_kr = list(set([t for t in kr_tickers if t and is_kr(t) and len(t) == 6] + kr_holdings))
        all_us = list(set([t for t in us_tickers if t and t.isalpha()] + us_holdings))
        target_universe = set(all_kr + all_us)
        
        MarketDataService.prune_states(target_universe)
        logger.info(f"🧹 Top 100 변경 감지: 현재 유니버스 {len(target_universe)}개 (KR={len(all_kr)}, US={len(all_us)})")
        return target_universe

    @classmethod
    def _log_intramarket_cash_ratio(cls, holdings: list, cash_balance: float, usd_cash: float, exchange_rate: float, target_cash_kr: float, target_cash_us: float):
        """각 시장별 현금 비중을 로그로 출력 (경고만, 자동 매도 없음)"""
        from utils.logger import get_logger
        logger = get_logger("strategy_service")

        kr_holdings = [h for h in filter_kr(holdings) if h.get('quantity', 0) > 0]
        us_holdings = [h for h in filter_us(holdings) if h.get('quantity', 0) > 0]

        kr_stock_val = sum(h.get('current_price', 0) * h.get('quantity', 0) for h in kr_holdings)
        us_stock_usd = sum(h.get('current_price', 0) * h.get('quantity', 0) for h in us_holdings)

        kr_total = kr_stock_val + max(0.0, cash_balance)
        us_total_usd = us_stock_usd + usd_cash

        kr_cash_ratio = cash_balance / kr_total if kr_total > 0 else 0.0
        us_cash_ratio = usd_cash / us_total_usd if us_total_usd > 0 else 0.0

        kr_stock_ratio = 1.0 - kr_cash_ratio
        us_stock_ratio = 1.0 - us_cash_ratio

        logger.info(
            f"📊 [포트폴리오 비중] "
            f"🇰🇷 주식 {kr_stock_ratio:.1%} / 현금 {kr_cash_ratio:.1%} (목표 현금 {target_cash_kr:.1%}) | "
            f"🇺🇸 주식 {us_stock_ratio:.1%} / 현금 {us_cash_ratio:.1%} (목표 현금 {target_cash_us:.1%})"
        )
        if kr_cash_ratio < target_cash_kr - 0.05 and kr_total > 0:
            logger.warning(f"⚠️ KR 현금 부족 ({kr_cash_ratio:.1%} < 목표 {target_cash_kr:.1%}). 익절 후 현금 확보 권장.")
        if us_cash_ratio < target_cash_us - 0.05 and us_total_usd > 0:
            logger.warning(f"⚠️ US 현금 부족 ({us_cash_ratio:.1%} < 목표 {target_cash_us:.1%}). 익절 후 현금 확보 권장.")

    @classmethod
    def _calculate_total_assets(cls, holdings: list, cash_balance: float, macro_data: dict) -> tuple:
        """총 자산 및 시장 국면별 현금 비중 목표 계산"""
        from utils.logger import get_logger
        logger = get_logger("strategy_service")
        usd_cash = PortfolioService.get_usd_cash_balance()
        exchange_rate = MacroService.get_exchange_rate()
        
        kr_holdings = filter_kr(holdings)
        us_holdings = filter_us(holdings)

        kr_market_value = sum(h.get('current_price', 0) * h.get('quantity', 0) for h in kr_holdings)
        us_market_value_usd = sum(h.get('current_price', 0) * h.get('quantity', 0) for h in us_holdings)
        us_market_value_krw = us_market_value_usd * exchange_rate
        usd_cash_krw = usd_cash * exchange_rate
        
        total_assets = kr_market_value + us_market_value_krw + cash_balance + usd_cash_krw
        
        regime_status = macro_data.get('market_regime', {}).get('status', 'Neutral').upper()
        target_cash_kr = cls._get_target_cash_ratio('KR', regime_status)
        target_cash_us = cls._get_target_cash_ratio('US', regime_status)
        logger.info(f"💰 시장 국면: {regime_status} → 한국 현금비중 목표: {target_cash_kr:.1%}, 미국 현금비중 목표: {target_cash_us:.1%}")
        
        return total_assets, target_cash_kr, target_cash_us

    @classmethod
    def _collect_trading_signals(cls, holdings: list, macro_data: dict, user_state: dict, total_assets: float, cash_balance: float, target_cash_kr: float, target_cash_us: float) -> list:
        """시장 상태를 확인하고 유효한 매매 시그널을 수집"""
        from utils.logger import get_logger
        logger = get_logger("strategy_service")
        allow_extended = SettingsService.get_int("STRATEGY_ALLOW_EXTENDED_HOURS", 1) == 1
        is_kr_open = MarketHourService.is_kr_market_open(allow_extended=allow_extended)
        is_us_open = MarketHourService.is_us_market_open(allow_extended=allow_extended)
        
        analyze_kr = not is_us_open
        analyze_us = not is_kr_open
        logger.info(f"📊 시장 상태: KR개장={is_kr_open}, US개장={is_us_open} → KR분석={analyze_kr}, US분석={analyze_us}")
        
        all_states = MarketDataService.get_all_states()
        prepared_signals = []
        
        holdings_map = {h['ticker']: h for h in holdings}
        for ticker, ticker_state in list(all_states.items()):
            is_kr_ticker = is_kr(ticker)
            if (is_kr_ticker and not analyze_kr) or (not is_kr_ticker and not analyze_us):
                continue
            if not getattr(ticker_state, 'is_ready', False):
                continue

            holding = holdings_map.get(ticker)
            market_cash_ratio = target_cash_kr if is_kr_ticker else target_cash_us
            score, reasons = cls.calculate_score(ticker, ticker_state, holding, macro_data, user_state, total_assets, cash_balance, market_cash_ratio=market_cash_ratio)
            
            prepared_signals.append({"ticker": ticker, "state": ticker_state, "holding": holding, "score": score, "reasons": reasons})
            
        logger.info(f"📊 Signal collection complete. {len(prepared_signals)} stocks ready.")
        return prepared_signals

    @classmethod
    def _execute_collected_signals(cls, user_id: str, prepared_signals: list, holdings: list, total_assets: float, cash_balance: float, target_cash_kr: float, target_cash_us: float, macro_data: dict, user_state: dict = None) -> bool:
        """수집된 시그널을 기반으로 실제 주문 집행"""
        from utils.logger import get_logger
        logger = get_logger("strategy_service")
        buy_max = SettingsService.get_int("STRATEGY_BUY_THRESHOLD_MAX", 30)
        sell_min = SettingsService.get_int("STRATEGY_SELL_THRESHOLD_MIN", 70)
        take_profit_pct = SettingsService.get_float("STRATEGY_TAKE_PROFIT_PCT", 3.0)
        exchange_rate = MacroService.get_exchange_rate()

        # 분할매도 쿨다운: 당일 이미 분할매도한 종목은 재매도 방지
        sell_cooldown: dict = (user_state or {}).setdefault('sell_cooldown', {})
        # 추가매수 쿨다운: 당일 이미 추매한 종목은 재추매 방지
        add_buy_cooldown: dict = (user_state or {}).setdefault('add_buy_cooldown', {})
        stop_loss_pct = SettingsService.get_float("STRATEGY_STOP_LOSS_PCT", -8.0)
        add_rsi_limit = SettingsService.get_float("STRATEGY_ADD_BUY_RSI_LIMIT", 60.0)
        add_score_limit = SettingsService.get_int("STRATEGY_ADD_BUY_SCORE_LIMIT", 55)
        today: str = datetime.now(pytz.timezone('Asia/Seoul')).strftime('%Y-%m-%d')

        trade_executed = False
        for sig in prepared_signals:
            ticker, state, holding = sig['ticker'], sig['state'], sig['holding']
            score, reasons = sig['score'], sig['reasons']
            reason_str = ", ".join(reasons)
            stock_name = getattr(state, "name", "") or (holding.get("name") if holding else "")
            dcf_val = getattr(state, 'dcf_value', None)
            dcf_str = f", DCF={dcf_val:,.0f}" if dcf_val and dcf_val > 0 else ""
            logger.info(f"🔍 Evaluated {ticker} ({stock_name}): Score={score}, RSI={getattr(state, 'rsi', 0):.1f}{dcf_str}, Reasons=[{reason_str}]")

            profit_pct = 0.0
            if holding:
                buy_price = holding.get('buy_price', 0)
                ref_price = float(holding.get("current_price") or getattr(state, 'current_price', 0))
                if buy_price > 0: profit_pct = (ref_price - buy_price) / buy_price * 100

            # 익절 우선 (분할매도 — 쿨다운 적용)
            if holding and profit_pct >= take_profit_pct:
                if sell_cooldown.get(ticker) == today:
                    logger.info(f"⏭️ {ticker} 분할매도 쿨다운 중 (오늘 이미 익절매도). 내일 재판단.")
                    continue
                executed = cls._execute_trade_v2(ticker, "sell", f"익절권({profit_pct:.2f}%)", profit_pct, True, score, getattr(state, 'current_price', 0), total_assets, cash_balance, exchange_rate, holdings=holdings, user_id=user_id, holding=holding, macro=macro_data, target_cash_ratio_kr=target_cash_kr, target_cash_ratio_us=target_cash_us)
                if executed:
                    sell_cooldown[ticker] = today
                trade_executed = trade_executed or bool(executed)
                continue

            # 추가 매수: 보유 종목이 -5% ~ stop_loss 구간 진입 시 분할 추매 (하루 1회)
            # 조건: RSI < 60 (과매수 구간 제외) + 스코어 ≤ add_score_limit (매수 우호적 신호 필요)
            current_rsi = getattr(state, 'rsi', 50.0)
            if holding and profit_pct <= -5.0 and profit_pct > stop_loss_pct:
                if current_rsi >= add_rsi_limit:
                    logger.info(f"⏭️ {ticker} 추가매수 RSI 과매수({current_rsi:.1f} ≥ {add_rsi_limit}). 스킵.")
                    continue
                if score > add_score_limit:
                    logger.info(f"⏭️ {ticker} 추가매수 스코어 불충족({score} > {add_score_limit}). 스킵.")
                    continue
                if add_buy_cooldown.get(ticker) == today:
                    logger.info(f"⏭️ {ticker} 추가매수 쿨다운 중 (오늘 이미 추매). 내일 재판단.")
                    continue
                executed = cls._execute_trade_v2(ticker, "buy", f"추가매수({profit_pct:.2f}%)", profit_pct, True, score, getattr(state, 'current_price', 0), total_assets, cash_balance, exchange_rate, holdings=holdings, user_id=user_id, holding=holding, macro=macro_data, target_cash_ratio_kr=target_cash_kr, target_cash_ratio_us=target_cash_us)
                if executed:
                    add_buy_cooldown[ticker] = today
                trade_executed = trade_executed or bool(executed)
                continue

            # 매수/매도 로직
            if score <= buy_max and not holding:
                executed = cls._execute_trade_v2(ticker, "buy", f"점수 {score} [{reason_str}]", profit_pct, False, score, getattr(state, 'current_price', 0), total_assets, cash_balance, exchange_rate, holdings=holdings, user_id=user_id, holding=holding, macro=macro_data, target_cash_ratio_kr=target_cash_kr, target_cash_ratio_us=target_cash_us)
                trade_executed = trade_executed or bool(executed)
            elif (score >= sell_min or score <= 10) and holding:
                is_stop_loss = score <= 10  # 손절은 전량매도 — 쿨다운 없음
                if not is_stop_loss and sell_cooldown.get(ticker) == today:
                    logger.info(f"⏭️ {ticker} 분할매도 쿨다운 중 (오늘 이미 점수매도). 내일 재판단.")
                    continue
                executed = cls._execute_trade_v2(ticker, "sell", f"점수 {score} [{reason_str}]", profit_pct, True, score, getattr(state, 'current_price', 0), total_assets, cash_balance, exchange_rate, holdings=holdings, user_id=user_id, holding=holding, macro=macro_data, target_cash_ratio_kr=target_cash_kr, target_cash_ratio_us=target_cash_us)
                if executed and not is_stop_loss:
                    sell_cooldown[ticker] = today
                trade_executed = trade_executed or bool(executed)

        return trade_executed

    @classmethod
    def _send_portfolio_report(cls, user_id: str, before_snapshot: dict):
        """매매 전후 잔고를 비교하여 변동이 있으면 포트폴리오 리포트 전송"""
        from utils.logger import get_logger
        logger = get_logger("strategy_service")
        try:
            from services.notification.report_service import ReportService
            PortfolioService.sync_with_kis(user_id)
            latest_holdings = PortfolioService.load_portfolio(user_id)
            summary = PortfolioService.get_last_balance_summary()
            latest_cash = PortfolioService.load_cash(user_id)
            
            after_snapshot = {h["ticker"]: h.get("quantity", 0) for h in latest_holdings}
            if before_snapshot == after_snapshot:
                logger.info("ℹ️ 체결 변경 없음. 포트폴리오 리포트 전송 스킵.")
                return
                
            states = MarketDataService.get_all_states()
            msg = ReportService.format_portfolio_report(latest_holdings, latest_cash, states, summary)
            AlertService.send_slack_alert(msg)
        except Exception as e:
            logger.warning(f"⚠️ 포트폴리오 리포트 전송 실패: {e}")

    @classmethod
    def sell_all_and_rebuy(cls, user_id: str = "sean") -> dict:
        """보유 종목 전량 매도 후 전략대로 재매수.
        Returns: {status, message, sold, failed, failed_tickers, strategy_error?}
        """
        from services.trading.order_service import OrderService
        logger.info("🔄 보유 종목 전량 매도 후 전략 재매수 시작")
        holdings = PortfolioService.sync_with_kis(user_id)
        if not holdings:
            return {"status": "success", "message": "보유 종목이 없습니다.", "sold": 0, "failed": 0}
        logger.info(f"📊 보유 종목 {len(holdings)}개 확인")
        success_count, fail_count, failed_tickers = OrderService.execute_mass_sell(holdings)
        PortfolioService.sync_with_kis(user_id)
        try:
            cls.run_strategy(user_id)
            logger.info("✅ 전략 실행 완료")
            return {
                "status": "success",
                "message": f"전량 매도 및 전략 재매수 완료 (매도 성공: {success_count}, 실패: {fail_count})",
                "sold": success_count,
                "failed": fail_count,
                "failed_tickers": failed_tickers or None,
            }
        except Exception as e:
            logger.error(f"❌ 전략 실행 중 오류: {e}")
            return {
                "status": "partial",
                "message": f"매도 완료 (성공: {success_count}, 실패: {fail_count}), 전략 실행 실패",
                "sold": success_count,
                "failed": fail_count,
                "failed_tickers": failed_tickers or None,
                "strategy_error": str(e),
            }

    @classmethod
    def run_strategy(cls, user_id: str = "sean"):
        """전체 전략 실행 루프"""
        from utils.logger import get_logger
        logger = get_logger("strategy_service")
        if not cls.is_enabled():
            logger.debug(f"⏳ Trading Strategy is currently DISABLED. Skipping analysis.")
            return

        logger.info(f"🚀 Running Trading Strategy for {user_id}...")
        
        target_universe = cls._update_target_universe(user_id)
        
        holdings = PortfolioService.sync_with_kis(user_id)
        before_snapshot = {h["ticker"]: h.get("quantity", 0) for h in holdings}
        macro_data = MacroService.get_macro_data()
        cash_balance = PortfolioService.load_cash(user_id)
        
        state = cls._load_state()
        user_state = state.setdefault(user_id, {})
        if 'panic_locks' not in user_state: user_state['panic_locks'] = {}
        
        total_assets, target_cash_kr, target_cash_us = cls._calculate_total_assets(holdings, cash_balance, macro_data)
        
        exchange_rate = MacroService.get_exchange_rate()
        usd_cash = PortfolioService.get_usd_cash_balance()
        cls._log_intramarket_cash_ratio(holdings, cash_balance, usd_cash, exchange_rate, target_cash_kr, target_cash_us)

        prepared_signals = cls._collect_trading_signals(holdings, macro_data, user_state, total_assets, cash_balance, target_cash_kr, target_cash_us)
        trade_executed = cls._execute_collected_signals(user_id, prepared_signals, holdings, total_assets, cash_balance, target_cash_kr, target_cash_us, macro_data, user_state)
        
        try:
            tick_executed = cls._run_tick_trade(user_id, holdings, total_assets, cash_balance)
            trade_executed = trade_executed or bool(tick_executed)
        except Exception as e:
            logger.warning(f"⚠️ Tick trading process error: {e}")
            
        cls._save_state(state)
        logger.info("✅ 전략 실행 및 매매 판단 완료.")

        if trade_executed:
            cls._send_portfolio_report(user_id, before_snapshot)

    @classmethod
    def get_waiting_list(cls, user_id: str = "sean"):
        """매매 대기 목록 조회 (BUY/SELL 시그널 종목)"""
        all_states = MarketDataService.get_all_states()
        all_state_items = list(all_states.items())
        holdings = PortfolioService.load_portfolio(user_id) # load_inventory -> load_portfolio 오타 수정
        macro_data = MacroService.get_macro_data()
        
        # 설정값 로드
        buy_threshold = SettingsService.get_int("STRATEGY_BUY_THRESHOLD", 75)
        sell_threshold = SettingsService.get_int("STRATEGY_SELL_THRESHOLD", 25)
        
        waiting_list = []
        
        # 임시 상태 로드
        state = cls._load_state()
        user_state = state.get(user_id, {})
        
        # 실제 포트폴리오 기반 자산/현금 계산 (조회용이므로 sync_with_kis 없이 DB값 사용)
        cash_balance = PortfolioService.load_cash(user_id)
        total_assets, _, _ = cls._calculate_total_assets(holdings, cash_balance, macro_data)
        
        buy_threshold_max = SettingsService.get_int("STRATEGY_BUY_THRESHOLD_MAX", 30)
        sell_threshold_min = SettingsService.get_int("STRATEGY_SELL_THRESHOLD_MIN", 70)
        holdings_map = {h['ticker']: h for h in holdings}
        for ticker, ticker_state in all_state_items:
            holding = holdings_map.get(ticker)

            # 점수 계산 (단순화된 버전 또는 전체 로직 사용)
            score, reasons = cls.calculate_score(ticker, ticker_state, holding, macro_data, user_state, total_assets, cash_balance)

            if score <= buy_threshold_max or score >= sell_threshold_min:
                action = "BUY" if score <= buy_threshold_max else "SELL"
                waiting_list.append({
                    "ticker": ticker,
                    "name": getattr(ticker_state, "name", None) or ticker,
                    "current_price": ticker_state.current_price,
                    "score": score,
                    "action": action,
                    "reasons": reasons,
                    "rsi": ticker_state.rsi
                })
                
        return sorted(waiting_list, key=lambda item: item["score"], reverse=True)

    @classmethod
    def get_opportunities(cls, user_id: str = "sean"):
        """스크립트 호환성을 위한 get_waiting_list 별칭"""
        return cls.get_waiting_list(user_id)

    @classmethod
    def execute_sell(cls, ticker: str, quantity: int = 0, user_id: str = "sean"):
        """수동 매도 실행"""
        # 보유 수량 확인
        holdings = PortfolioService.sync_with_kis(user_id)
        holding = next((h for h in holdings if h['ticker'] == ticker), None)
        
        if not holding:
            return {"status": "failed", "msg": "보유 주식이 아닙니다."}
            
        max_qty = (getattr(holding, "quantity", None) if not isinstance(holding, dict) else holding.get("quantity", None))
        if quantity <= 0 or quantity > max_qty:
            quantity = max_qty # 전량 매도
            
        from utils.logger import get_logger
        logger = get_logger("strategy_service")
        logger.info(f"manual sell execution: {ticker} {quantity} qty")
        
        order_result = KisService.send_order(ticker, quantity, 0, "sell")
        if order_result.get("status") == "success":
            # 매매 내역 저장
            OrderService.record_trade(
                ticker=ticker,
                order_type="sell",
                quantity=quantity,
                price=holding.get('current_price', 0), # 현재가
                result_msg="Manual Sell Execution",
                strategy_name="manual"
            )
        return order_result

    @classmethod
    def analyze_ticker(cls, ticker: str, state, holding: Optional[dict], macro: dict, user_state: dict, total_assets: float, cash_balance: float, exchange_rate: float) -> dict:
        """외부에서 개별 종목 분석 결과를 받을 수 있도록 공개된 인터페이스"""
        score, reasons = cls.calculate_score(ticker, state, holding, macro, user_state, total_assets, cash_balance)
        
        buy_threshold_max = SettingsService.get_int("STRATEGY_BUY_THRESHOLD_MAX", 30)
        sell_threshold_min = SettingsService.get_int("STRATEGY_SELL_THRESHOLD_MIN", 70)

        recommendation = "WAIT"
        if score <= buy_threshold_max:
            recommendation = "BUY"
        elif score >= sell_threshold_min:
            recommendation = "SELL"
            
        return {
            "ticker": ticker,
            "score": score,
            "recommendation": recommendation,
            "reasons": reasons,
            "current_price": state.current_price,
            "rsi": state.rsi,
            "dcf_value": getattr(state, 'dcf_value', None),
        }

    @classmethod
    def _score_technical(cls, state, curr_price: float, oversold_rsi: float, overbought_rsi: float, dip_buy_pct: float) -> tuple[int, list]:
        """[A] RSI + 급락/급등 + DCF + EMA200 → (delta, reasons)"""
        delta = 0
        reasons = []
        rsi = state.rsi
        if rsi <= 30:
            rsi_score = -(20 - (rsi / 30) * 10)
            delta += int(rsi_score)
            reasons.append(f"RSI극과매도({rsi:.1f},{int(rsi_score)})")
        elif rsi < 50:
            rsi_score = -(10 - ((rsi - 30) / 20) * 10)
            if rsi_score <= -5:
                delta += int(rsi_score)
                reasons.append(f"RSI과매도({rsi:.1f},{int(rsi_score)})")
        elif rsi <= 70:
            rsi_score = ((rsi - 50) / 20) * 10
            if rsi_score >= 5:
                delta += int(rsi_score)
                reasons.append(f"RSI과매수({rsi:.1f},+{int(rsi_score)})")
        else:
            rsi_score = 10 + ((rsi - 70) / 30) * 10
            delta += int(rsi_score)
            reasons.append(f"RSI극과매수({rsi:.1f},+{int(rsi_score)})")

        change_rate = getattr(state, 'change_rate', 0)
        if change_rate <= dip_buy_pct:
            delta += cls.WEIGHTS['DIP_BUY_5PCT']; reasons.append(f"급락({change_rate:.1f}%)")
        elif change_rate >= 5.0:
            delta += cls.WEIGHTS['SURGE_SELL_5PCT']; reasons.append(f"급등({change_rate:.1f}%)")

        if state.dcf_value and state.dcf_value > 0:
            undervalue_pct = (state.dcf_value - curr_price) / curr_price * 100
            if undervalue_pct >= 20:
                delta += cls.WEIGHTS['DCF_UNDERVALUE_HIGH']; reasons.append(f"DCF고저평가({undervalue_pct:.1f}%)")
            elif undervalue_pct >= 10:
                delta += cls.WEIGHTS['DCF_UNDERVALUE_MID']; reasons.append(f"DCF중저평가({undervalue_pct:.1f}%)")
            elif undervalue_pct >= 5:
                delta += cls.WEIGHTS['DCF_UNDERVALUE_LOW']; reasons.append(f"DCF저평가({undervalue_pct:.1f}%)")
            elif undervalue_pct >= -5:
                delta += cls.WEIGHTS['DCF_FAIR_VALUE']; reasons.append("DCF적정가")
            elif undervalue_pct >= -15:
                delta += cls.WEIGHTS['DCF_OVERVALUE_LOW']; reasons.append(f"DCF고평가({-undervalue_pct:.1f}%)")
            else:
                delta += cls.WEIGHTS['DCF_OVERVALUE_HIGH']; reasons.append(f"DCF고고평가({-undervalue_pct:.1f}%)")

        ema200 = state.ema.get(200) if state.ema else None
        if ema200 and ema200 > 0 and (ema200 <= curr_price <= ema200 * 1.02):
            delta += cls.WEIGHTS['SUPPORT_EMA']; reasons.append("EMA200지지")
        return delta, reasons

    @classmethod
    def _score_portfolio(cls, holding, profit_pct: float, take_profit_pct: float, stop_loss_pct: float) -> tuple[int, list, bool]:
        """[B] 익절 / 추매 / 손절 → (delta, reasons, forced_sell)"""
        if not holding:
            return 0, [], False
        delta = 0
        reasons = []
        if profit_pct >= take_profit_pct:
            delta += cls.WEIGHTS['PROFIT_TAKE_TARGET']; reasons.append(f"익절권({profit_pct:.1f}%)")
        elif profit_pct <= -5.0 and profit_pct > stop_loss_pct:
            delta += cls.WEIGHTS['ADD_POSITION_LOSS']; reasons.append(f"추매권({profit_pct:.1f}%)")
        elif profit_pct <= stop_loss_pct:
            return 0, ["손절도달"], True  # forced_sell: score=100
        return delta, reasons, False

    @classmethod
    def _score_market_context(cls, macro: dict, regime: str) -> tuple[int, list]:
        """[C] 공포/과열 + 상승/하락장 → (delta, reasons)"""
        delta = 0
        reasons = []
        vix = macro.get('vix', 20.0)
        fng = macro.get('fear_greed', 50)
        if vix >= 25 or fng <= 30:
            delta += cls.WEIGHTS['PANIC_MARKET_BUY']; reasons.append("극도의공포(매수기회)")
        elif vix <= 15 or fng >= 70:
            delta += cls.WEIGHTS['PROFIT_TAKE_TARGET'] // 2; reasons.append("시장과열(분할익절)")
        if regime == 'BULL':
            delta += cls.WEIGHTS['BULL_MARKET_SECTOR']; reasons.append("상승장어드밴티지")
        elif regime == 'BEAR':
            delta += 10; reasons.append("하락장리스크관리")
        return delta, reasons

    @classmethod
    def _score_target_prices(cls, state, curr_price: float) -> tuple[int, list]:
        """[D] 사용자 설정 목표 진입가/매도가 도달 → (delta, reasons)"""
        delta = 0
        reasons = []
        target_buy = getattr(state, 'target_buy_price', 0)
        target_sell = getattr(state, 'target_sell_price', 0)
        if target_buy > 0 and curr_price <= target_buy:
            delta -= 30; reasons.append(f"목표진입가도달(${target_buy})")
        if target_sell > 0 and curr_price >= target_sell:
            delta += 30; reasons.append(f"목표매도가도달(${target_sell})")
        return delta, reasons

    @classmethod
    def _score_bonuses(cls, ticker: str, holding, macro: dict, user_state: dict) -> tuple[int, list]:
        """[E-G] 시총상위10 / 사용자가중치 / 섹터비중 보너스 → (delta, reasons)"""
        delta = 0
        reasons = []
        top10_bonus = SettingsService.get_int("STRATEGY_TOP10_BONUS", 10)
        if top10_bonus and ticker in cls._get_top10_market_cap_tickers():
            delta -= top10_bonus; reasons.append(f"시총상위10(-{top10_bonus})")

        overrides = cls.get_top_weight_overrides()
        if ticker in overrides:
            custom_bonus = int(overrides[ticker])
            if custom_bonus != 0:
                delta += custom_bonus; reasons.append(f"가중치사용자설정({custom_bonus:+d})")

        try:
            grp = cls._get_sector_group(ticker, holding)
            if grp != "other":
                exchange_rate_g = MacroService.get_exchange_rate()
                all_holdings = PortfolioService.load_portfolio(user_state.get("user_id", "sean"))
                sw = cls._get_sector_group_weights(all_holdings, exchange_rate_g)
                dev = sw["weights"].get(grp, {}).get("dev", 0.0)
                if dev < -cls.SECTOR_REBAL_THRESHOLD:
                    delta -= 10; reasons.append(f"섹터부족매수우선({grp} {dev:+.1%})")
                elif dev > cls.SECTOR_REBAL_THRESHOLD:
                    delta += 10; reasons.append(f"섹터초과매도우선({grp} {dev:+.1%})")
        except Exception:
            pass
        return delta, reasons

    @classmethod
    def calculate_score(cls, ticker: str, state, holding: Optional[dict], macro: dict, user_state: dict, total_assets: float, cash_balance: float, market_cash_ratio: float = None) -> tuple:
        """개별 종목의 투자 점수 계산 ([A]~[G] 헬퍼 통합)"""
        curr_price = state.current_price
        if curr_price <= 0: return 0, ["가격정보없음"]

        profit_pct = 0.0
        if holding:
            buy_price = (getattr(holding, "buy_price", None) if not isinstance(holding, dict) else holding.get("buy_price", None))
            ref_price = getattr(holding, 'current_price', 0) if not isinstance(holding, dict) else float(holding.get("current_price") or 0)
            if ref_price <= 0:
                ref_price = curr_price
            profit_pct = (ref_price - buy_price) / buy_price * 100 if buy_price > 0 else 0.0

        cash_ratio = cash_balance / total_assets if total_assets > 0 else 0
        panic_locks = user_state.get('panic_locks', {})
        regime = macro.get('market_regime', {}).get('status', 'Unknown').upper()

        if market_cash_ratio is None:
            market = 'KR' if is_kr(ticker) else 'US'
            market_cash_ratio = cls._get_target_cash_ratio(market, regime)
        target_cash_ratio = market_cash_ratio
        base_score = SettingsService.get_int("STRATEGY_BASE_SCORE", 50)
        oversold_rsi  = SettingsService.get_float("STRATEGY_OVERSOLD_RSI", 30.0)
        overbought_rsi = SettingsService.get_float("STRATEGY_OVERBOUGHT_RSI", 70.0)
        dip_buy_pct   = SettingsService.get_float("STRATEGY_DIP_BUY_PCT", -5.0)
        take_profit_pct = SettingsService.get_float("STRATEGY_TAKE_PROFIT_PCT", 3.0)
        stop_loss_pct   = SettingsService.get_float("STRATEGY_STOP_LOSS_PCT", -8.0)

        if ticker in panic_locks:
            return (20, ["3일룰회복대기"]) if state.rsi < oversold_rsi else (50, ["패닉락구간"])

        score = base_score
        reasons: list = []

        # [A] 기술적 지표
        d, r = cls._score_technical(state, curr_price, oversold_rsi, overbought_rsi, dip_buy_pct)
        score += d; reasons.extend(r)

        # [B] 포트폴리오 (손절 시 강제 매도)
        d, r, forced_sell = cls._score_portfolio(holding, profit_pct, take_profit_pct, stop_loss_pct)
        if forced_sell:
            return 100, r
        score += d; reasons.extend(r)

        # [C] 시장/거시
        d, r = cls._score_market_context(macro, regime)
        score += d; reasons.extend(r)

        # [D] 목표가
        d, r = cls._score_target_prices(state, curr_price)
        score += d; reasons.extend(r)

        # [E-G] 보너스
        d, r = cls._score_bonuses(ticker, holding, macro, user_state)
        score += d; reasons.extend(r)

        if cash_ratio < target_cash_ratio and score > 50:
            score += cls.WEIGHTS['CASH_PENALTY']; reasons.append("현금부족")

        return max(0, min(100, score)), reasons

    @classmethod
    def _analyze_stock_v3(cls, ticker: str, state, holding: Optional[dict], macro: dict, user_state: dict, total_assets: float, cash_balance: float, exchange_rate: float, user_id: str = "sean"):
        """기존 내부 분석 루프 (리팩토링된 calculate_score 활용)"""
        score, reasons = cls.calculate_score(ticker, state, holding, macro, user_state, total_assets, cash_balance)
        
        profit_pct = 0.0
        if holding:
            buy_price = (getattr(holding, "buy_price", None) if not isinstance(holding, dict) else holding.get("buy_price", None))
            ref_price = getattr(holding, "current_price", 0) if not isinstance(holding, dict) else float(holding.get("current_price") or 0)
            if ref_price <= 0:
                ref_price = state.current_price
            profit_pct = (ref_price - buy_price) / buy_price * 100 if buy_price > 0 else 0.0

        reason_str = ", ".join(reasons)
        
        buy_threshold_max = SettingsService.get_int("STRATEGY_BUY_THRESHOLD_MAX", 30)  # 30 이하에서 매수
        sell_threshold_min = SettingsService.get_int("STRATEGY_SELL_THRESHOLD_MIN", 70)  # 70 이상에서 매도
        
        # 30 이하에서 매수
        if score <= buy_threshold_max and not holding:
            cls._execute_trade_v2(
                ticker,
                "buy",
                f"점수 {score} [{reason_str}]",
                profit_pct,
                False,
                score,
                state.current_price,
                total_assets,
                cash_balance,
                exchange_rate,
                holdings=PortfolioService.load_portfolio(user_id),
                user_id=user_id,
                holding=holding,
                macro=macro
            )
        # 70 이상에서 매도
        elif score >= sell_threshold_min and holding:
            cls._execute_trade_v2(
                ticker,
                "sell",
                f"점수 {score} [{reason_str}]",
                profit_pct,
                True,
                score,
                state.current_price,
                total_assets,
                cash_balance,
                exchange_rate,
                holdings=PortfolioService.load_portfolio(user_id),
                user_id=user_id,
                holding=holding,
                macro=macro
            )

    @classmethod
    def _check_market_hours(cls, ticker: str) -> bool:
        """시장 운영 시간 체크"""
        allow_extended = SettingsService.get_int("STRATEGY_ALLOW_EXTENDED_HOURS", 1) == 1
        return MarketHourService.is_kr_market_open(allow_extended=allow_extended) if is_kr(ticker) else MarketHourService.is_us_market_open(allow_extended=allow_extended)

    @classmethod
    def _is_cash_ratio_sufficient(cls, ticker: str, holdings: list, cash_balance: float, total_assets: float, exchange_rate: float, target_cash_ratio_kr: float, target_cash_ratio_us: float, macro: dict) -> bool:
        """목표 현금 비중 조건 충족 여부 검사"""
        is_kr_ticker = is_kr(ticker)
        regime_status = (macro or {}).get('market_regime', {}).get('status', 'Neutral').upper()
        target_cash_ratio = target_cash_ratio_kr if is_kr_ticker else target_cash_ratio_us

        if target_cash_ratio is None:
            target_cash_ratio = cls._get_target_cash_ratio('KR' if is_kr_ticker else 'US', regime_status)

        if is_kr_ticker:
            kr_holdings = [h for h in filter_kr(holdings or []) if h.get("quantity", 0) > 0]
            kr_market_value = sum(cls._get_holding_value(h) for h in kr_holdings)
            kr_total = kr_market_value + cash_balance
            cash_ratio = cash_balance / kr_total if kr_total > 0 else 0
        else:
            from services.trading.portfolio_service import PortfolioService
            us_holdings = [h for h in filter_us(holdings or []) if h.get("quantity", 0) > 0]
            us_market_value_krw = sum(cls._get_holding_value(h) * exchange_rate for h in us_holdings if h.get("quantity", 0) > 0)
            usd_cash = PortfolioService.get_usd_cash_balance()
            us_cash_krw = usd_cash * exchange_rate
            us_total = us_market_value_krw + us_cash_krw
            cash_ratio = us_cash_krw / us_total if us_total > 0 else 0
            
        return cash_ratio <= target_cash_ratio and not cls._is_panic_market(macro or {})

    @classmethod
    def _calculate_buy_quantity(cls, score: int, total_assets: float, cash_balance: float, current_price: float, exchange_rate: float, is_kr: bool, market_total_krw: float = 0.0) -> tuple:
        """투자 비중에 따른 매수 수량 및 필요 소요 자금(원화) 계산.
        market_total_krw: 해당 시장(KR 또는 US) 포트폴리오 총액(원화). 0이면 total_assets 사용.
        """
        per_trade_ratio = SettingsService.get_float("STRATEGY_PER_TRADE_RATIO", 0.05)
        split_count = SettingsService.get_int("STRATEGY_SPLIT_COUNT", 3)
        buy_threshold = SettingsService.get_int("STRATEGY_BUY_THRESHOLD", 75)

        # 각 시장 포트폴리오 크기 기준으로 매수 규모 계산 (cross-market 혼용 방지)
        base_assets = market_total_krw if market_total_krw > 0 else total_assets
        multiplier = 2.0 if score >= 90 else (1.5 if score >= 80 else 1.0)
        target_invest_krw = base_assets * per_trade_ratio * multiplier
        one_time_invest_krw = target_invest_krw / split_count
        actual_invest_krw = min(one_time_invest_krw, cash_balance)
        
        final_price = current_price if is_kr else current_price * exchange_rate
        quantity = int(actual_invest_krw // final_price) if final_price > 0 else 0
        
        if quantity == 0 and score >= buy_threshold and cash_balance >= final_price:
            from utils.logger import get_logger
            logger = get_logger("strategy_service")
            logger.info("💡 소액 자산 보정: 최소 수량(1주) 확보를 위해 비중 상향 조정 집행")
            quantity = 1
            
        return quantity, quantity * final_price, final_price

    @classmethod
    def _send_tick_alert(cls, ticker: str, side: str, current_price: float, qty: int, reason: str, pnl_pct: float = 0.0, holding: dict = None):
        """틱매매 체결 알림 (구조화 슬랙 메시지)."""
        meta = StockMetaService.get_stock_meta(ticker)
        name = (holding.get("name") if holding and holding.get("name")
                else (meta.name_ko or meta.name_en or "" if meta else ""))
        is_kr_flag = is_kr(ticker)
        price_str = f"{current_price:,.0f}원" if is_kr_flag else f"${current_price:,.2f}"
        if side == "buy":
            msg = (
                f"🔵 *[매수 체결 - 틱매매]*\n"
                f"• 종목: {ticker} {name}\n"
                f"• 매수가: {price_str}\n"
                f"• 수량: {qty}주\n"
                f"• 사유: {reason}"
            )
        else:
            buy_price = float(holding.get("buy_price", 0)) if holding else 0
            profit_amt = (current_price - buy_price) * qty if buy_price else 0
            profit_amt_str = f"{profit_amt:+,.0f}원" if is_kr_flag else f"${profit_amt:+,.2f}"
            msg = (
                f"🔴 *[매도 체결 - 틱매매]*\n"
                f"• 종목: {ticker} {name}\n"
                f"• 매도가: {price_str}\n"
                f"• 수량: {qty}주\n"
                f"• 수익률: {pnl_pct:+.2f}%  |  수익금: {profit_amt_str}\n"
                f"• 사유: {reason}"
            )
        AlertService.send_slack_alert(msg)

    @classmethod
    def _send_trade_alert(cls, ticker: str, side: str, score: int, current_price: float, change_rate: float, trade_qty: int, profit_pct: float, holding: dict, executed: bool):
        if not executed:
            return
        meta = StockMetaService.get_stock_meta(ticker)
        name = (holding.get("name") if holding and holding.get("name")
                else (meta.name_ko or meta.name_en or "" if meta else ""))
        is_kr_flag = is_kr(ticker)
        currency = "원" if is_kr_flag else "USD"
        price_str = f"{current_price:,.0f}{currency}" if is_kr_flag else f"${current_price:,.2f}"

        if side == "buy":
            msg = (
                f"🔵 *[매수 체결]*\n"
                f"• 종목: {ticker} {name}\n"
                f"• 매수가: {price_str}\n"
                f"• 수량: {trade_qty}주\n"
                f"• 등락률: {change_rate:+.2f}%  |  점수: {score}"
            )
        else:
            buy_price = float(holding.get("buy_price", 0)) if holding else 0
            profit_amt = (current_price - buy_price) * trade_qty if buy_price else 0
            profit_amt_str = (f"{profit_amt:+,.0f}원" if is_kr else f"${profit_amt:+,.2f}")
            msg = (
                f"🔴 *[매도 체결]*\n"
                f"• 종목: {ticker} {name}\n"
                f"• 매도가: {price_str}\n"
                f"• 수량: {trade_qty}주\n"
                f"• 수익률: {profit_pct:+.2f}%  |  수익금: {profit_amt_str}\n"
                f"• 등락률: {change_rate:+.2f}%  |  점수: {score}"
            )
        AlertService.send_slack_alert(msg)

    @classmethod
    def _execute_trade_v2(
        cls, ticker: str, side: str, reason: str, profit_pct: float, is_holding: bool, score: int, current_price: float, total_assets: float, cash_balance: float, exchange_rate: float, holdings: list = None, user_id: str = "sean", holding: dict = None, macro: dict = None, target_cash_ratio_kr: float = None, target_cash_ratio_us: float = None
    ) -> bool:
        """분할 매수/매도 실행 로직"""
        from utils.logger import get_logger
        logger = get_logger("strategy_service")
        logger.info(f"📢 시그널 [{side.upper()}] {ticker} - 사유: {reason}")
        
        if not cls._check_market_hours(ticker):
            logger.info(f"⏭️ {ticker} 시장 비개장. 주문 스킵.")
            return False

        executed = False
        trade_qty = 0
        is_kr_flag = is_kr(ticker)
        state = MarketDataService.get_state(ticker)
        change_rate = getattr(state, 'change_rate', 0.0)

        if side == 'buy':
            # 현금 부족 시 매수 차단 (마진 방지)
            if is_kr_flag and cash_balance <= 0:
                logger.info(f"⏭️ {ticker} 원화 현금 부족 ({cash_balance:,.0f}원). 매수 차단.")
                return False
            if not is_kr_flag:
                from services.trading.portfolio_service import PortfolioService as _PS
                _usd_cash = _PS.get_usd_cash_balance()
                if _usd_cash <= 0:
                    logger.info(f"⏭️ {ticker} USD 현금 부족 (${_usd_cash:.2f}). 매수 차단.")
                    return False

            if is_holding:
                add_position_below = SettingsService.get_float("STRATEGY_ADD_POSITION_BELOW", -5.0)
                if profit_pct > add_position_below:
                    logger.info(f"⏭️ {ticker} 추가매수 조건 미충족. 주문 스킵.")
                    return False

            if cls._is_cash_ratio_sufficient(ticker, holdings, cash_balance, total_assets, exchange_rate, target_cash_ratio_kr, target_cash_ratio_us, macro):
                logger.info(f"⏭️ {ticker} 현금비중 조건으로 인해 매수 스킵.")
                return False
                
            from services.trading.portfolio_service import PortfolioService
            holdings = holdings or PortfolioService.load_portfolio(user_id)
            kr_holdings = filter_kr(holdings)
            us_holdings = filter_us(holdings)
            kr_market_value = sum(cls._get_holding_value(h) for h in kr_holdings if h.get("quantity", 0) > 0)
            us_market_value_krw = sum(cls._get_holding_value(h) * exchange_rate for h in us_holdings if h.get("quantity", 0) > 0)
            usd_cash = PortfolioService.get_usd_cash_balance()
            kr_assets = kr_market_value + cash_balance       # KR 포트폴리오 총액
            us_assets_krw = us_market_value_krw + (usd_cash * exchange_rate)  # US 포트폴리오 총액

            # 매수 규모는 해당 시장 포트폴리오 기준으로 계산
            market_total_krw = kr_assets if is_kr_flag else us_assets_krw
            quantity, est_krw, final_price = cls._calculate_buy_quantity(score, total_assets, cash_balance, current_price, exchange_rate, is_kr_flag, market_total_krw=market_total_krw)
            if quantity > 0:
                
                ok, limit_reasons = cls._passes_allocation_limits(ticker, est_krw, holdings, total_assets, cash_balance, holding, kr_assets, us_assets_krw)
                if not ok:
                    logger.info(f"⏭️ {ticker} 비중 제한 매수 스킵: {', '.join(limit_reasons)}")
                    return False

                trade_qty = quantity
                logger.info(f"⚖️ {ticker} 분할 매수 예정 ({quantity}주)")
                
                order_result = KisService.send_order(ticker, quantity, 0, "buy") if is_kr_flag else KisService.send_overseas_order(ticker, quantity, round(float(current_price), 2), "buy")
                if order_result.get("status") == "success":
                    OrderService.record_trade(ticker, "buy", quantity, final_price, "Strategy execution", "v3_strategy")
                    executed = True
                else: logger.error(f"주문 실패: {order_result}")
            else:
                logger.warning(f"⚠️ {ticker} 잔고 부족 (필요: {final_price:,.0f}원)")
                return False

        elif side == "sell":
            from services.trading.portfolio_service import PortfolioService
            portfolio = holdings or PortfolioService.load_portfolio(user_id)
            current_holding = next((h for h in portfolio if h['ticker'] == ticker), None)
            if not current_holding: return False
            
            holding_qty = current_holding.get('quantity', 0)
            split_count = SettingsService.get_int("STRATEGY_SPLIT_COUNT", 3)
            
            if score <= 10: 
                sell_qty, msg = holding_qty, "전량 매도(손절)"
            else:
                sell_qty, msg = max(1, int(holding_qty / split_count)), "분할 매도(익절)"
            
            trade_qty = sell_qty
            order_result = KisService.send_order(ticker, sell_qty, 0, "sell") if is_kr else KisService.send_overseas_order(ticker, sell_qty, round(float(current_price), 2), "sell")
            if order_result.get("status") == "success":
                OrderService.record_trade(ticker, "sell", sell_qty, current_price, msg, "v3_strategy")
                executed = True
            else: logger.error(f"주문 실패: {order_result}")

        cls._send_trade_alert(ticker, side, score, current_price, change_rate, trade_qty, profit_pct, holding, executed)
        return executed

    # ── 주간 섹터 리밸런싱 ──────────────────────────────────────────────────────

    @classmethod
    def _execute_overweight_sells(
        cls, weights: dict, holdings: list,
        total_assets: float, cash_balance: float, exchange_rate: float,
        user_id: str, macro: dict, target_cash_kr: float, target_cash_us: float,
    ) -> tuple[int, list, list, float]:
        """STEP 1: 초과 섹터 보유 종목 분할 매도 → (sells_executed, sold_list, skipped_list, updated_cash)"""
        overweight_groups = sorted(
            [(grp, info) for grp, info in weights.items()
             if grp != "other" and info["dev"] > cls.SECTOR_REBAL_THRESHOLD],
            key=lambda x: -x[1]["dev"],
        )
        sold, skipped, sells_executed = [], [], 0
        for grp, info in overweight_groups:
            dev = info["dev"]
            grp_holdings = sorted(
                [h for h in holdings if h.get("quantity", 0) > 0 and cls._get_sector_group(h["ticker"], h) == grp],
                key=lambda h: (float(h.get("current_price") or 0) - float(h.get("buy_price") or 1))
                              / float(h.get("buy_price") or 1),
                reverse=True,
            )
            for h in grp_holdings:
                ticker = h["ticker"]
                if not cls._check_market_hours(ticker):
                    skipped.append({"ticker": ticker, "reason": "시장비개장"})
                    continue
                current_price = float(h.get("current_price") or 0)
                buy_price     = float(h.get("buy_price") or 0)
                profit_pct    = (current_price - buy_price) / buy_price * 100 if buy_price > 0 else 0
                if profit_pct < 0:
                    skipped.append({"ticker": ticker, "reason": f"손실중({profit_pct:.1f}%) 리밸런싱 제외"})
                    continue
                executed = cls._execute_trade_v2(
                    ticker, "sell", f"섹터리밸런싱-초과({grp} {dev:+.1%})",
                    profit_pct, True, 60, current_price,
                    total_assets, cash_balance, exchange_rate,
                    holdings=holdings, user_id=user_id, holding=h, macro=macro,
                    target_cash_ratio_kr=target_cash_kr, target_cash_ratio_us=target_cash_us,
                )
                if executed:
                    sells_executed += 1
                    sell_val = current_price * max(1, int(h.get("quantity", 0) / 3))
                    cash_balance += sell_val * (exchange_rate if not is_kr(ticker) else 1.0)
                    sold.append({"ticker": ticker, "group": grp, "dev": round(dev, 4), "profit_pct": round(profit_pct, 2)})
                    break
        return sells_executed, sold, skipped, cash_balance

    @classmethod
    def _execute_underweight_buys(
        cls, weights: dict, holdings: list,
        total_assets: float, cash_balance: float, exchange_rate: float,
        user_id: str, macro: dict, target_cash_kr: float, target_cash_us: float,
    ) -> tuple[int, list, list]:
        """STEP 2: 부족 섹터 후보 종목 매수 → (buys_executed, bought_list, skipped_list)"""
        underweight_groups = sorted(
            [(grp, info) for grp, info in weights.items()
             if grp != "other" and info["dev"] < -cls.SECTOR_REBAL_THRESHOLD],
            key=lambda x: x[1]["dev"],
        )
        all_states   = MarketDataService.get_all_states()
        holdings_map = {h["ticker"]: h for h in holdings}
        user_state   = {"user_id": user_id}
        bought, skipped, buys_executed = [], [], 0
        buy_threshold = SettingsService.get_int("STRATEGY_BUY_THRESHOLD_MAX", 30)

        for grp, info in underweight_groups:
            dev = info["dev"]
            candidates = []
            for ticker, state in all_states.items():
                if not getattr(state, "is_ready", False): continue
                if cls._get_sector_group(ticker) != grp: continue
                if not cls._check_market_hours(ticker): continue
                holding = holdings_map.get(ticker)
                score, reasons = cls.calculate_score(
                    ticker, state, holding, macro, user_state, total_assets, cash_balance,
                    market_cash_ratio=target_cash_kr if is_kr(ticker) else target_cash_us,
                )
                candidates.append((ticker, state, holding, score, reasons))
            candidates.sort(key=lambda x: x[3])

            for ticker, state, holding, score, reasons in candidates:
                if score > buy_threshold + 10:
                    skipped.append({"ticker": ticker, "reason": f"매수신호미달(score={score})"})
                    continue
                executed = cls._execute_trade_v2(
                    ticker, "buy", f"섹터리밸런싱-부족({grp} {dev:+.1%})",
                    0.0, False, score, getattr(state, "current_price", 0),
                    total_assets, cash_balance, exchange_rate,
                    holdings=holdings, user_id=user_id, holding=holding, macro=macro,
                    target_cash_ratio_kr=target_cash_kr, target_cash_ratio_us=target_cash_us,
                )
                if executed:
                    buys_executed += 1
                    bought.append({"ticker": ticker, "group": grp, "dev": round(dev, 4), "score": score})
                    break
        return buys_executed, bought, skipped

    @classmethod
    def _build_rebalance_summary(cls, sold: list, bought: list, weights_before: dict, weights_after: dict) -> str:
        """섹터 리밸런싱 결과 Slack 요약 문자열 생성"""
        summary = (
            f"🔄 *주간 섹터 리밸런싱 완료*\n"
            f"매도: {len(sold)}건  |  매수: {len(bought)}건\n"
        )
        if sold:
            summary += "매도: " + ", ".join(f"{s['ticker']}({s['group']} {s['profit_pct']:+.1f}%)" for s in sold) + "\n"
        if bought:
            summary += "매수: " + ", ".join(f"{b['ticker']}({b['group']})" for b in bought) + "\n"
        for grp in ["tech", "value", "financial"]:
            bef = weights_before.get(grp, {}).get("weight", 0)
            aft = weights_after.get(grp, {}).get("weight", 0)
            tgt = cls.SECTOR_TARGET_WEIGHT.get(grp, 0)
            summary += f"  {grp}: {bef:.1%} → {aft:.1%}  (목표 {tgt:.0%})\n"
        return summary

    @classmethod
    def run_sector_rebalance(cls, user_id: str = "sean") -> dict:
        """주 1회 섹터 그룹 비중 리밸런싱 (헬퍼 위임).

        편차 < 5% → 패스, 5~10% → 절반 리밸런싱, >10% → 전체 리밸런싱
        초과 섹터 → 수익 높은 보유 종목 분할 매도
        부족 섹터 → DCF 저평가 + RSI 낮은 후보 매수
        """
        logger.info("🔄 주간 섹터 리밸런싱 시작...")
        exchange_rate  = MacroService.get_exchange_rate()
        holdings       = PortfolioService.load_portfolio(user_id)
        macro          = MacroService.get_macro_data()
        total_assets, target_cash_kr, target_cash_us = cls._get_portfolio_totals(user_id, holdings)
        cash_balance   = PortfolioService.get_cash_balance(user_id) or 0.0

        weights = cls._get_sector_group_weights(holdings, exchange_rate)["weights"]
        result  = {"sold": [], "bought": [], "skipped": [], "weights_before": weights}

        sells_n, sold, skipped_s, cash_balance = cls._execute_overweight_sells(
            weights, holdings, total_assets, cash_balance, exchange_rate,
            user_id, macro, target_cash_kr, target_cash_us,
        )
        result["sold"].extend(sold); result["skipped"].extend(skipped_s)

        buys_n, bought, skipped_b = cls._execute_underweight_buys(
            weights, holdings, total_assets, cash_balance, exchange_rate,
            user_id, macro, target_cash_kr, target_cash_us,
        )
        result["bought"].extend(bought); result["skipped"].extend(skipped_b)

        sw_after = cls._get_sector_group_weights(PortfolioService.load_portfolio(user_id), exchange_rate)
        result["weights_after"] = sw_after["weights"]

        summary = cls._build_rebalance_summary(result["sold"], result["bought"], weights, sw_after["weights"])
        try:
            from services.notification.alert_service import AlertService
            AlertService.send_slack_alert(summary)
        except Exception:
            pass
        logger.info(summary)
        result["summary"] = summary
        return result