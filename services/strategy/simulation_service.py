import time
from typing import List
from services.strategy.trading_strategy_service import TradingStrategyService
from services.market.market_data_service import MarketDataService
from utils.logger import get_logger

logger = get_logger("simulation_service")

class SimulationService:
    """
    Trading simulation execution and management service.
    """
    
    DEFAULT_TARGETS = [
        "005930", # Samsung Electronics
        "000660", # SK Hynix
        "TSLA",   # Tesla
        "AAPL",   # Apple
        "NVDA",   # NVIDIA
        "AMD",    # AMD
        "MSFT",   # Microsoft
        "SOXL",   # Semiconductor 3x Leveraged ETF
        "TQQQ",   # NASDAQ 3x Leveraged ETF
    ]

    SIMULATION_WAIT_DEFAULT = 10

    @classmethod
    def run_live_simulation(cls, tickers: List[str] = None, user_id: str = "sean", wait_seconds: int = None):
        """
        Run strategy simulation based on current market data.
        """
        logger.info("🚀 [Simulation] Starting Strategy Simulation...")
        
        # 1. Force-enable strategy engine
        TradingStrategyService.set_enabled(True)
        
        if wait_seconds is None:
            wait_seconds = cls.SIMULATION_WAIT_DEFAULT
        targets = tickers or cls.DEFAULT_TARGETS
        logger.info(f"📊 Registering {len(targets)} targets for simulation...")
        for ticker in targets:
            MarketDataService.register_ticker(ticker)
            
        # 3. Wait for data loading
        if wait_seconds > 0:
            logger.info(f"⏳ Waiting for data warm-up ({wait_seconds} seconds)...")
            time.sleep(wait_seconds)
            
        # 4. Execute strategy
        logger.info("▶️ Executing Trading Strategy...")
        try:
            TradingStrategyService.run_strategy(user_id=user_id)
            logger.info("✅ Simulation Complete. Check Slack for alerts.")
            return True
        except Exception as e:
            logger.error(f"❌ Simulation Failed: {e}")
            return False
