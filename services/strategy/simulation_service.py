import time
from typing import List
from services.strategy.trading_strategy_service import TradingStrategyService
from services.market.market_data_service import MarketDataService
from utils.logger import get_logger

logger = get_logger("simulation_service")

class SimulationService:
    """
    트레이딩 시뮬레이션 실행 및 관리 서비스
    """
    
    DEFAULT_TARGETS = [
        "005930", # 삼성전자
        "000660", # SK하이닉스
        "TSLA",   # 테슬라
        "AAPL",   # 애플
        "NVDA",   # 엔비디아
        "AMD",    # AMD
        "MSFT",   # 마이크로소프트
        "SOXL",   # 반도체 3배 레버리지
        "TQQQ",   # 나스닥 3배 레버리지
    ]

    @classmethod
    def run_live_simulation(cls, tickers: List[str] = None, user_id: str = "sean", wait_seconds: int = 10):
        """
        현재 시장 데이터를 기반으로 전략 시뮬레이션 실행
        """
        logger.info("🚀 [Simulation] Starting Strategy Simulation...")
        
        # 1. 전략 엔진 강제 활성화
        TradingStrategyService.set_enabled(True)
        
        # 2. 종목 등록 (Warm-up)
        targets = tickers or cls.DEFAULT_TARGETS
        logger.info(f"📊 Registering {len(targets)} targets for simulation...")
        for ticker in targets:
            MarketDataService.register_ticker(ticker)
            
        # 3. 데이터 로딩 대기
        if wait_seconds > 0:
            logger.info(f"⏳ Waiting for data warm-up ({wait_seconds} seconds)...")
            time.sleep(wait_seconds)
            
        # 4. 전략 실행
        logger.info("▶️ Executing Trading Strategy...")
        try:
            TradingStrategyService.run_strategy(user_id=user_id)
            logger.info("✅ Simulation Complete. Check Slack for alerts.")
            return True
        except Exception as e:
            logger.error(f"❌ Simulation Failed: {e}")
            return False
