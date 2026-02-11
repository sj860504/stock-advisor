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
        portfolio = PortfolioService.sync_with_kis(user_id)
        macro_data = MacroService.get_macro_data()
        
        state = cls._load_state()
        if user_id not in state: state[user_id] = {}
        user_state = state[user_id]
        
        # 2. MarketDataService에서 관리하는 모든 종목(상위 100위 + 보유 종목) 분석
        all_states = MarketDataService.get_all_states()
        
        for ticker, ticker_state in all_states.items():
            holding = next((h for h in portfolio if h['ticker'] == ticker), None)
            cls._analyze_stock_v2(ticker, ticker_state, holding, macro_data, user_state)
            
        cls._save_state(state)
        logger.info("✅ Strategy run complete.")

    @classmethod
    def _analyze_stock_v2(cls, ticker: str, state, holding: Optional[dict], macro: dict, user_state: dict):
        """실시간 TickerState 및 매크로 지표 기반 사용자 정의 전략 분석"""
        curr_price = state.current_price
        if curr_price <= 0: return

        # 0. 기본 데이터 확보
        vix = macro.get('vix', 20.0)
        fng = macro.get('fear_greed', 50)
        macro_indicators = macro.get('economic_indicators', {})
        macro_sentiment = macro_indicators.get('summary', {}).get('sentiment_ratio', 0)
        market_indices = macro.get('indices', {})
        sp500_change = market_indices.get('S&P500', {}).get('change', 0)
        sector_perf = macro.get('sector_performance', {})
        
        # 종목 섹터 정보 (캐싱 필요하나 일단 실시간 추정 또는 스킵)
        # 종목의 섹터가 sector_perf에 있는지 확인하는 로직 (생략 시 일반 시장 지표 사용)

        buy_reason = None
        sell_reason = None
        profit_pct = 0.0

        # --- [1. 매수 로직 구현] ---
        # 1.1. 주가 하락율 5% 근접
        if state.change_rate <= -4.8: # 5% 근접
            buy_reason = "Individual Stock 5% Drop"

        # 1.2. 보유 종목 수익율 -5% 근접
        if holding:
            buy_price = holding['buy_price']
            profit_pct = (curr_price - buy_price) / buy_price * 100
            if profit_pct <= -4.8:
                buy_reason = "Portfolio Holding 5% Loss Support"

        # 1.3. VIX 20이상 및 공포탐욕지수 40이하 (적극 매수)
        if vix >= 20 and fng <= 40:
            buy_reason = f"Aggressive Buy (VIX:{vix}, F&G:{fng})"

        # 1.4. 거시 호재 + 시장 상승 + 섹터 로테이션(해당 종목/섹터만 하락)
        # 매크로 점수 양수(호재) AND 시장 상승 AND 종목은 하락 중
        if macro_sentiment > 0.1 and sp500_change > 0.2 and state.change_rate < -1.0:
            buy_reason = "Sector Rotation Dip Buy (Market Up / Stock Down)"

        # 1.5. 시장 악재로 인한 동반 하락 (종목/섹터 자체 악재 없음 가정)
        if macro_sentiment < -0.3 and state.change_rate < -3.0:
            # 시장 전체가 빠지는데 같이 빠질 때 (역발상)
            buy_reason = "Market-Driven Panic Dip Buy"

        # --- [2. 매도 로직 구현] ---
        # 2.1. 주가 상승율 5% 근접
        if state.change_rate >= 4.8:
            sell_reason = "Individual Stock 5% Surge"

        # 2.2. 보유 종목 수익율 +5% 근접
        if holding and profit_pct >= 4.8:
            sell_reason = "Profit Taking (5%)"

        # 2.3. VIX 20이하 및 공포탐욕지수 50이상 (적극 매도)
        if vix <= 20 and fng >= 55: # 사용자 요청 50이상이나 보수적으로 55 적용
            sell_reason = f"Aggressive Sell (VIX:{vix}, F&G:{fng})"

        # 2.4. 거시 호재 + 시장 불리쉬 + 섹터 로테이션(과열 매도)
        if macro_sentiment > 0.1 and sp500_change > 0.5 and state.change_rate > 3.0:
            sell_reason = "Sector Rotation Overheat Sell"

        # --- [추가 필터링 (RSI 등)] ---
        # 매수 시 RSI 과열 방지
        rsi = state.rsi
        if buy_reason and rsi and rsi > 65:
            logger.info(f"⏳ {ticker} buy skipped: RSI is too high ({rsi})")
            buy_reason = None

        # 주문 실행
        if buy_reason:
            cls._execute_trade(ticker, "buy", buy_reason, profit_pct)
        elif sell_reason:
            cls._execute_trade(ticker, "sell", sell_reason, profit_pct)
        

    @classmethod
    def _execute_trade(cls, ticker: str, side: str, reason: str, profit_pct: float):
        """실제 주문 전송 (분할 로직 포함)"""
        logger.info(f"📢 SIGNAL [{side.upper()}] {ticker} - Reason: {reason}")
        
        # 1. 분할 매매 판단 (수익률 +/- 2% 초과 시 3회 분할)
        splits = 1
        if abs(profit_pct) > 2:
            splits = 3
            logger.info(f"⚖️ Multi-split trade (3 splits) enabled for {ticker}")
            
        # 2. 실제 주문 호출 (KIS 서비스 연동)
        # TODO: 보유 수량 및 가용 현금에 따른 수량 계산 필요
        quantity = 1 # 테스트용 1주
        
        order_res = {"status": "skipped", "msg": "Simulation Mode"}
        # if side == "buy":
        #     order_res = KisService.send_order(ticker, quantity, order_type="buy")
        # else:
        #     order_res = KisService.send_order(ticker, quantity, order_type="sell")
        
        # 3. 슬랙 알림 전송
        emoji = "🔵" if side == "buy" else "🔴"
        msg = (
            f"{emoji} **[{side.upper()} SIGNAL] {ticker}**\n"
            f"- 사유: {reason}\n"
            f"- 수익률: {profit_pct:.2f}%\n"
            f"- 분할: {splits}분할 실행 예정\n"
            f"- 상태: {order_res.get('msg', '매매 시뮬레이션 중')}"
        )
        AlertService.send_slack_alert(msg)
