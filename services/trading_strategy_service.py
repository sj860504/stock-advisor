import json
import os
from datetime import datetime, timedelta
from stock_advisor.services.macro_service import MacroService
from stock_advisor.services.portfolio_service import PortfolioService
from stock_advisor.services.market_data_service import MarketDataService # 추가
from stock_advisor.services.kis_service import KisService
from stock_advisor.services.alert_service import AlertService
from stock_advisor.utils.logger import get_logger

logger = get_logger("strategy_service")

class TradingStrategyService:
    """
    사용자의 투자 전략에 따른 매매 시그널 판단 및 실행 서비스
    """
    _state_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'strategy_state.json')
    _enabled = False # 기본값: 비활성화 (사용자 승인 필요)

    # 전략 설정 상수 (한글화)
    TARGET_CASH_RATIO = 0.40
    PER_TRADE_RATIO = 0.05 # 기본 1회 매수 비중 (5%)
    BASE_SCORE = 50
    BUY_THRESHOLD = 75
    SELL_THRESHOLD = 25
    SPLIT_COUNT = 3

    # 가중치 설정
    WEIGHTS = {
        'RSI_OVERSOLD': 20, 'RSI_OVERBOUGHT': -15,
        'DIP_BUY_5PCT': 15, 'SURGE_SELL_5PCT': -15,
        'SUPPORT_EMA': 10, 'RESISTANCE_EMA': -10,
        'ADD_POSITION_LOSS': 10, 'GOLDEN_CROSS_DROP': -15,
        'PANIC_MARKET_BUY': 25, 'PROFIT_TAKE_TARGET': -30,
        'BULL_MARKET_SECTOR': 10, 'CASH_PENALTY': -15
    }

    # 기준값
    STOP_LOSS_PCT = -10.0
    TAKE_PROFIT_PCT = 5.0
    DIP_BUY_PCT = -5.0
    OVERSOLD_RSI = 30.0
    OVERBOUGHT_RSI = 70.0

    @classmethod
    def set_enabled(cls, enabled: bool):
        cls._enabled = enabled
        logger.info(f"⚙️ Trading Strategy Engine {'ENABLED' if enabled else 'DISABLED'}")

    @classmethod
    def is_enabled(cls) -> bool:
        return cls._enabled
    
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
    def run_strategy(cls, user_id: str = "sean"):
        """전체 전략 실행 루프"""
        if not cls.is_enabled():
            logger.debug(f"⏳ Trading Strategy is currently DISABLED. Skipping analysis.")
            return

        logger.info(f"🚀 Running Trading Strategy for {user_id}...")
        
        # 1. KIS 실제 잔고 동기화 및 로딩
        holdings = PortfolioService.sync_with_kis(user_id)
        macro_data = MacroService.get_macro_data()
        exchange_rate = MacroService.get_exchange_rate()
        
        state = cls._load_state()
        if user_id not in state: state[user_id] = {}
        user_state = state[user_id]
        if 'panic_locks' not in user_state: user_state['panic_locks'] = {}
        
        # 총 자산 계산
        total_value = sum(h['current_price'] * h['quantity'] for h in holdings)
        cash_balance = PortfolioService.load_cash(user_id)
        total_assets = total_value + cash_balance
        
        # 2. MarketDataService에서 관리하는 모든 종목 분석
        all_states = MarketDataService.get_all_states()
        
        for ticker, ticker_state in all_states.items():
            holding = next((h for h in holdings if h['ticker'] == ticker), None)
            cls._analyze_stock_v3(ticker, ticker_state, holding, macro_data, user_state, total_assets, cash_balance, exchange_rate)
            
        cls._save_state(state)
        logger.info("✅ 전략 분석 완료.")

    @classmethod
    def _analyze_stock_v3(cls, ticker: str, state, holding: Optional[dict], macro: dict, user_state: dict, total_assets: float, cash_balance: float, exchange_rate: float):
        """개선된 점수 기반 전략 분석 (한글화)"""
        curr_price = state.current_price
        if curr_price <= 0: return

        profit_pct = 0.0
        if holding:
            profit_pct = (curr_price - holding['buy_price']) / holding['buy_price'] * 100

        cash_ratio = cash_balance / total_assets if total_assets > 0 else 0
        panic_locks = user_state.get('panic_locks', {})
        regime = macro.get('market_regime', {}).get('status', 'Unknown').upper()

        # 3-Day Rule (Panic Lock) 체크
        if ticker in panic_locks:
            if state.rsi < cls.OVERSOLD_RSI:
                logger.info(f"🔓 [3-Day Rule] {ticker}: 모니터링 해제 -> 회복 매수 진입!")
                cls._execute_trade_v2(ticker, "buy", f"3일 룰 회복 (RSI {state.rsi:.1f})", profit_pct, True, 100, curr_price, total_assets, cash_balance, exchange_rate)
                del panic_locks[ticker]
                return
            else:
                return

        # 점수 계산
        score = cls.BASE_SCORE
        reasons = []

        # [A] 기술적 지표
        rsi = state.rsi
        if rsi < cls.OVERSOLD_RSI: 
            score += cls.WEIGHTS['RSI_OVERSOLD']
            reasons.append(f"RSI과매도({rsi:.1f})")
        elif rsi > cls.OVERBOUGHT_RSI: 
            score += cls.WEIGHTS['RSI_OVERBOUGHT']
            reasons.append(f"RSI과매수({rsi:.1f})")

        if state.change_rate <= cls.DIP_BUY_PCT: 
            score += cls.WEIGHTS['DIP_BUY_5PCT']
            reasons.append(f"급락({state.change_rate:.1f}%)")
        elif state.change_rate >= 5.0: 
            score += cls.WEIGHTS['SURGE_SELL_5PCT']
            reasons.append(f"급등({state.change_rate:.1f}%)")

        ema200 = state.ema.get(200) if state.ema else None
        if ema200 and ema200 > 0 and (ema200 * 1.00 <= curr_price <= ema200 * 1.02):
            score += cls.WEIGHTS['SUPPORT_EMA']; reasons.append("EMA200지지")

        # [B] 포트폴리오
        if holding:
            if profit_pct >= cls.TAKE_PROFIT_PCT: 
                score += cls.WEIGHTS['PROFIT_TAKE_TARGET']
                reasons.append(f"익절권({profit_pct:.1f}%)")
            elif profit_pct <= -5.0 and profit_pct > cls.STOP_LOSS_PCT: 
                score += cls.WEIGHTS['ADD_POSITION_LOSS']
                reasons.append(f"추매권({profit_pct:.1f}%)")
            elif profit_pct <= cls.STOP_LOSS_PCT:
                if state.change_rate < -10.0:
                    # 패닉 셀 조건 발생 시 3-Day Rule 락 설정
                    logger.warning(f"🚨 [Panic Lock] {ticker}: 급락으로 인한 모니터링 모드 진입")
                    panic_locks[ticker] = datetime.now().isoformat()
                    return
                else: 
                    score = 0; reasons.append("손절도달")

        # [C] 시장/거시
        vix = macro.get('vix', 20.0)
        fng = macro.get('fear_greed', 50)
        if vix >= 20 and fng <= 40:
            score += cls.WEIGHTS['PANIC_MARKET_BUY']; reasons.append("공포장세")
        
        if regime in ['PANIC', 'BEAR'] and score < 50 and score > 0:
            score = 50; reasons.append("하락장매도금지")

        if cash_ratio < cls.TARGET_CASH_RATIO and score > 50:
            score += cls.WEIGHTS['CASH_PENALTY']; reasons.append("현금부족")

        score = max(0, min(100, score))
        
        # 판단
        reason_str = ", ".join(reasons)
        if score >= cls.BUY_THRESHOLD:
            cls._execute_trade_v2(ticker, "buy", f"점수 {score} [{reason_str}]", profit_pct, holding is not None, score, curr_price, total_assets, cash_balance, exchange_rate)
        elif score <= cls.SELL_THRESHOLD:
            if holding:
                cls._execute_trade_v2(ticker, "sell", f"점수 {score} [{reason_str}]", profit_pct, True, score, curr_price, total_assets, cash_balance, exchange_rate)

    @classmethod
    def _execute_trade_v2(cls, ticker: str, side: str, reason: str, profit_pct: float, is_holding: bool, score: int, current_price: float, total_assets: float, cash_balance: float, exchange_rate: float):
        """개선된 분할 매매 실행 (한글화)"""
        logger.info(f"📢 시그널 [{side.upper()}] {ticker} - 사유: {reason}")
        
        split_denominator = cls.SPLIT_COUNT  # 3분할
        
        if side == 'buy':
            # 1. 투자 강도 결정
            multiplier = 1.0
            if score >= 90: multiplier = 2.0
            elif score >= 80: multiplier = 1.5
            
            # 2. 목표 투자 금액 (KRW)
            target_invest_krw = total_assets * cls.PER_TRADE_RATIO * multiplier
            
            # 3. 이번 회차 분할 매수 금액
            one_time_invest_krw = target_invest_krw / split_denominator
            
            # 4. 가용 현금 체크
            actual_invest_krw = min(one_time_invest_krw, cash_balance)
            
            # 환율 적용 (숫자가 아니면 미국 주식으로 간주)
            is_us = not ticker.isdigit()
            final_price = current_price * exchange_rate if is_us else current_price
            
            # 수량 계산
            quantity = int(actual_invest_krw // final_price)
            est_krw = quantity * final_price
            
            if quantity > 0:
                logger.info(f"⚖️ {ticker} {split_denominator}분할 매수 중 1회차 집행 예정 ({quantity}주)")
                # order_res = KisService.send_order(ticker, quantity, "buy")
            else:
                logger.warning(f"⚠️ {ticker} 잔고 부족으로 매수 불가 (필요: {final_price:,.0f}원)")
                return

        elif side == 'sell':
            # 보유 수량 확인 (PortfolioService 활용)
            portfolio = PortfolioService.load_portfolio("sean") # 임시 하드코딩
            holding = next((h for h in portfolio if h['ticker'] == ticker), None)
            if not holding: return
            
            holding_qty = holding['quantity']
            sell_qty = 0
            split_msg = ""
            
            if score <= 10: 
                sell_qty = holding_qty # 전량 매도
                split_msg = "전량 매도 (손절/위험)"
            else:
                sell_qty = max(1, int(holding_qty / split_denominator)) # 1/3 매도
                split_msg = "1/3 분할 매도 (익절)"
            
            logger.info(f"⚖️ {ticker} {split_msg} 집행 예정 ({sell_qty}주)")
            # order_res = KisService.send_order(ticker, sell_qty, "sell")

        # 슬랙 알림
        emoji = "🔵" if side == "buy" else "🔴"
        msg = (
            f"{emoji} **[{side.upper()} 시그널] {ticker}**\n"
            f"- 사유: {reason}\n"
            f"- 수익률: {profit_pct:.2f}%\n"
            f"- 전략: {cls.SPLIT_COUNT}분할 매매 적용\n"
            f"- 상태: 매매 시뮬레이션 모드"
        )
        AlertService.send_slack_alert(msg)
