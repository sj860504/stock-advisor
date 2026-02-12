import json
import os
from config import Config
from typing import Optional
from datetime import datetime, timedelta
from services.market.macro_service import MacroService
from services.trading.portfolio_service import PortfolioService
from services.market.market_data_service import MarketDataService # 추가
from services.kis.kis_service import KisService
from services.notification.alert_service import AlertService
from services.config.settings_service import SettingsService
from services.trading.order_service import OrderService
from utils.logger import get_logger

logger = get_logger("strategy_service")

class TradingStrategyService:
    """
    사용자의 투자 전략에 따른 매매 시그널 판단 및 실행 서비스
    """
    _state_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'strategy_state.json')
    _enabled = False # 기본값: 비활성화 (사용자 승인 필요)

    # 전략 설정 상수 (SettingsService 연동을 위해 클래스 변수 제거 또는 프로퍼티화)
    # 여기서는 메서드 내에서 호출하도록 변경

    # 가중치 설정
    WEIGHTS = {
        'RSI_OVERSOLD': 20, 'RSI_OVERBOUGHT': -15,
        'DIP_BUY_5PCT': 15, 'SURGE_SELL_5PCT': -15,
        'SUPPORT_EMA': 10, 'RESISTANCE_EMA': -10,
        'ADD_POSITION_LOSS': 10, 'GOLDEN_CROSS_DROP': -15,
        'PANIC_MARKET_BUY': 25, 'PROFIT_TAKE_TARGET': -30,
        'BULL_MARKET_SECTOR': 10, 'CASH_PENALTY': -15
    }

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
    def get_waiting_list(cls, user_id: str = "sean"):
        """매매 대기 목록 조회 (BUY/SELL 시그널 종목)"""
        # 1. 자산 정보 로드 (현금 비중 계산용)
        # 실시간 잔고 동기화는 비용이 크므로, 이 메서드에서는 생략하거나 필요 시 추가
        # 여기서는 단순히 점수 기반으로 필터링
        
        all_states = MarketDataService.get_all_states()
        holdings = PortfolioService.load_inventory(user_id) # DB에서 조회
        macro_data = MacroService.get_macro_data()
        
        # 설정값 로드
        buy_threshold = SettingsService.get_int("STRATEGY_BUY_THRESHOLD", 75)
        sell_threshold = SettingsService.get_int("STRATEGY_SELL_THRESHOLD", 25)
        
        waiting_list = []
        
        # 임시 상태 로드
        state = cls._load_state()
        user_state = state.get(user_id, {})
        
        # 자산 가치 대략 추정 (점수 계산에 필요)
        # 정확한 계산을 위해서는 PortfolioService.sync_with_kis가 필요하지만, 조회용이므로 DB값 사용
        # total_assets, cash_balance = ... (생략하고 0으로 처리하거나 기본값 사용)
        total_assets = 10000000 # 임시
        cash_balance = 5000000  # 임시
        
        for ticker, ticker_state in all_states.items():
            holding = next((h for h in holdings if h['ticker'] == ticker), None)
            
            # 점수 계산 (단순화된 버전 또는 전체 로직 사용)
            score, reasons = cls.calculate_score(ticker, ticker_state, holding, macro_data, user_state, total_assets, cash_balance)
            
            if score >= buy_threshold or score <= sell_threshold:
                action = "BUY" if score >= buy_threshold else "SELL"
                waiting_list.append({
                    "ticker": ticker,
                    "name": ticker_state.ticker, # 이름 정보가 state에 있다면 사용
                    "current_price": ticker_state.current_price,
                    "score": score,
                    "action": action,
                    "reasons": reasons,
                    "rsi": ticker_state.rsi
                })
                
        return sorted(waiting_list, key=lambda x: x['score'], reverse=True)

    @classmethod
    def execute_sell(cls, ticker: str, quantity: int = 0, user_id: str = "sean"):
        """수동 매도 실행"""
        # 보유 수량 확인
        holdings = PortfolioService.sync_with_kis(user_id)
        holding = next((h for h in holdings if h['ticker'] == ticker), None)
        
        if not holding:
            return {"status": "failed", "msg": "보유 주식이 아닙니다."}
            
        max_qty = holding['quantity']
        if quantity <= 0 or quantity > max_qty:
            quantity = max_qty # 전량 매도
            
        logger.info(f"manual sell execution: {ticker} {quantity} qty")
        
        # 실제 주문
        res = KisService.send_order(ticker, quantity, 0, "sell")
        
        if res['status'] == 'success':
            # 매매 내역 저장
            OrderService.record_trade(
                ticker=ticker,
                order_type="sell",
                quantity=quantity,
                price=holding.get('current_price', 0), # 현재가
                result_msg="Manual Sell Execution",
                strategy_name="manual"
            )
            
        return res

    @classmethod
    def analyze_ticker(cls, ticker: str, state, holding: Optional[dict], macro: dict, user_state: dict, total_assets: float, cash_balance: float, exchange_rate: float) -> dict:
        """외부에서 개별 종목 분석 결과를 받을 수 있도록 공개된 인터페이스"""
        score, reasons = cls.calculate_score(ticker, state, holding, macro, user_state, total_assets, cash_balance)
        
        buy_threshold = SettingsService.get_int("STRATEGY_BUY_THRESHOLD", 75)
        sell_threshold = SettingsService.get_int("STRATEGY_SELL_THRESHOLD", 25)

        recommendation = "WAIT"
        if score >= buy_threshold:
            recommendation = "BUY"
        elif score <= sell_threshold:
            recommendation = "SELL"
            
        return {
            "ticker": ticker,
            "score": score,
            "recommendation": recommendation,
            "reasons": reasons,
            "current_price": state.current_price,
            "rsi": state.rsi
        }

    @classmethod
    def calculate_score(cls, ticker: str, state, holding: Optional[dict], macro: dict, user_state: dict, total_assets: float, cash_balance: float) -> tuple:
        """개별 종목의 투자 점수 계산 (로직 분리)"""
        curr_price = state.current_price
        if curr_price <= 0: return 0, ["가격정보없음"]

        profit_pct = 0.0
        if holding:
            buy_price = holding['buy_price']
            profit_pct = (curr_price - buy_price) / buy_price * 100 if buy_price > 0 else 0.0

        cash_ratio = cash_balance / total_assets if total_assets > 0 else 0
        panic_locks = user_state.get('panic_locks', {})
        regime = macro.get('market_regime', {}).get('status', 'Unknown').upper()

        target_cash_ratio = SettingsService.get_float("STRATEGY_TARGET_CASH_RATIO", 0.4)
        base_score = SettingsService.get_int("STRATEGY_BASE_SCORE", 50)
        oversold_rsi = SettingsService.get_float("STRATEGY_OVERSOLD_RSI", 30.0)
        overbought_rsi = SettingsService.get_float("STRATEGY_OVERBOUGHT_RSI", 70.0)
        dip_buy_pct = SettingsService.get_float("STRATEGY_DIP_BUY_PCT", -5.0)
        take_profit_pct = SettingsService.get_float("STRATEGY_TAKE_PROFIT_PCT", 5.0)
        stop_loss_pct = SettingsService.get_float("STRATEGY_STOP_LOSS_PCT", -10.0)

        if ticker in panic_locks:
            return (100, ["3일룰회복대기"]) if state.rsi < oversold_rsi else (0, ["패닉락구간"])

        # 점수 계산
        score = base_score
        reasons = []

        # [A] 기술적 지표
        rsi = state.rsi
        if rsi < oversold_rsi: 
            score += cls.WEIGHTS['RSI_OVERSOLD']
            reasons.append(f"RSI과매도({rsi:.1f})")
        elif rsi > overbought_rsi: 
            score += cls.WEIGHTS['RSI_OVERBOUGHT']
            reasons.append(f"RSI과매수({rsi:.1f})")

        change_rate = getattr(state, 'change_rate', 0)
        if change_rate <= dip_buy_pct: 
            score += cls.WEIGHTS['DIP_BUY_5PCT']
            reasons.append(f"급락({change_rate:.1f}%)")
        elif change_rate >= 5.0: 
            score += cls.WEIGHTS['SURGE_SELL_5PCT']
            reasons.append(f"급등({change_rate:.1f}%)")

        ema200 = state.ema.get(200) if state.ema else None
        if ema200 and ema200 > 0 and (ema200 * 1.00 <= curr_price <= ema200 * 1.02):
            score += cls.WEIGHTS['SUPPORT_EMA']; reasons.append("EMA200지지")

        # [B] 포트폴리오
        if holding:
            if profit_pct >= take_profit_pct: 
                score += cls.WEIGHTS['PROFIT_TAKE_TARGET']; reasons.append(f"익절권({profit_pct:.1f}%)")
            elif profit_pct <= -5.0 and profit_pct > stop_loss_pct: 
                score += cls.WEIGHTS['ADD_POSITION_LOSS']; reasons.append(f"추매권({profit_pct:.1f}%)")
            elif profit_pct <= stop_loss_pct:
                score = 0; reasons.append("손절도달")

        # [C] 시장/거시
        vix = macro.get('vix', 20.0)
        fng = macro.get('fear_greed', 50)
        if vix >= 20 and fng <= 40:
            score += cls.WEIGHTS['PANIC_MARKET_BUY']; reasons.append("공포장세")
        
        if regime in ['PANIC', 'BEAR'] and score < 50 and score > 0:
            score = 50; reasons.append("하락장매도금지")

        if cash_ratio < target_cash_ratio and score > 50:
            score += cls.WEIGHTS['CASH_PENALTY']; reasons.append("현금부족")

        return max(0, min(100, score)), reasons

    @classmethod
    def _analyze_stock_v3(cls, ticker: str, state, holding: Optional[dict], macro: dict, user_state: dict, total_assets: float, cash_balance: float, exchange_rate: float):
        """기존 내부 분석 루프 (리팩토링된 calculate_score 활용)"""
        score, reasons = cls.calculate_score(ticker, state, holding, macro, user_state, total_assets, cash_balance)
        
        profit_pct = 0.0
        if holding:
            buy_price = holding['buy_price']
            profit_pct = (state.current_price - buy_price) / buy_price * 100 if buy_price > 0 else 0.0

        reason_str = ", ".join(reasons)
        
        buy_threshold = SettingsService.get_int("STRATEGY_BUY_THRESHOLD", 75)
        sell_threshold = SettingsService.get_int("STRATEGY_SELL_THRESHOLD", 25)
        
        if score >= buy_threshold:
            cls._execute_trade_v2(ticker, "buy", f"점수 {score} [{reason_str}]", profit_pct, holding is not None, score, state.current_price, total_assets, cash_balance, exchange_rate)
        elif score <= sell_threshold:
            if holding:
                cls._execute_trade_v2(ticker, "sell", f"점수 {score} [{reason_str}]", profit_pct, True, score, state.current_price, total_assets, cash_balance, exchange_rate)

    @classmethod
    def _execute_trade_v2(cls, ticker: str, side: str, reason: str, profit_pct: float, is_holding: bool, score: int, current_price: float, total_assets: float, cash_balance: float, exchange_rate: float):
        """개선된 분할 매매 실행 (한글화)"""
        logger.info(f"📢 시그널 [{side.upper()}] {ticker} - 사유: {reason}")
        
        split_count = SettingsService.get_int("STRATEGY_SPLIT_COUNT", 3)
        per_trade_ratio = SettingsService.get_float("STRATEGY_PER_TRADE_RATIO", 0.05)
        buy_threshold = SettingsService.get_int("STRATEGY_BUY_THRESHOLD", 75)

        split_denominator = split_count
        
        if side == 'buy':
            # 1. 투자 강도 결정
            multiplier = 1.0
            if score >= 90: multiplier = 2.0
            elif score >= 80: multiplier = 1.5
            
            # 2. 목표 투자 금액 (KRW)
            target_invest_krw = total_assets * per_trade_ratio * multiplier
            
            # 3. 이번 회차 분할 매수 금액
            one_time_invest_krw = target_invest_krw / split_denominator
            
            # 4. 가용 현금 체크
            actual_invest_krw = min(one_time_invest_krw, cash_balance)
            
            # 환율 적용 (숫자가 아니면 미국 주식으로 간주)
            is_us = not ticker.isdigit()
            final_price = current_price * exchange_rate if is_us else current_price
            
            # 수량 계산
            quantity = int(actual_invest_krw // final_price)
            
            # [소액 자산 보정] 수량이 0주이나 확실한 신호(점수 75+)이고 현금이 있다면 최소 1주 매수
            if quantity == 0 and score >= buy_threshold and cash_balance >= final_price:
                logger.info(f"💡 소액 자산 보정: 최소 수량(1주) 확보를 위해 비중 상향 조정 집행")
                quantity = 1
                
            est_krw = quantity * final_price
            
            if quantity > 0:
                logger.info(f"⚖️ {ticker} {split_denominator}분할 매수 중 1회차 집행 예정 ({quantity}주)")
                
                # 주문 실행
                res = KisService.send_order(ticker, quantity, 0, "buy")
                
                if res['status'] == 'success':
                    # 매매 내역 저장
                    OrderService.record_trade(ticker, "buy", quantity, final_price, "Strategy execution", "v3_strategy")
                else:
                    logger.error(f"주문 실패: {res}")
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
            
            # 주문 실행
            res = KisService.send_order(ticker, sell_qty, 0, "sell")
            
            if res['status'] == 'success':
                OrderService.record_trade(ticker, "sell", sell_qty, current_price, split_msg, "v3_strategy")
            else:
                logger.error(f"주문 실패: {res}")

        # 슬랙 알림
        emoji = "🔵" if side == "buy" else "🔴"
        msg = (
            f"{emoji} **[{side.upper()} 시그널] {ticker}**\n"
            f"- 사유: {reason}\n"
            f"- 수익률: {profit_pct:.2f}%\n"
            f"- 전략: {split_count}분할 매매 적용\n"
            f"- 상태: 매매 실행 중"
        )
        AlertService.send_slack_alert(msg)
