import sys
import os
import time
import logging

# 프로젝트 루트 경로 추가
sys.path.append(os.getcwd())

from services.trading_strategy_service import TradingStrategyService
from services.market_data_service import MarketDataService
from services.portfolio_service import PortfolioService
from utils.logger import get_logger

# 로깅 설정 (콘솔 출력)
logging.basicConfig(level=logging.INFO)
logger = get_logger("simulation")

def run_simulation():
    logger.info("🚀 [Simulation] Starting Strategy Simulation...")
    
    # 1. 전략 엔진 강제 활성화 (메모리 상에서만)
    TradingStrategyService.set_enabled(True)
    
    # 2. 시뮬레이션 대상 종목 등록 (데이터 Warm-up)
    # 한국, 미국 주요 종목 및 ETF
    targets = [
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
    
    logger.info(f"📊 Registering {len(targets)} targets for simulation...")
    for ticker in targets:
        MarketDataService.register_ticker(ticker)
        
    # 3. 데이터 로딩 대기 (Warm-up은 별도 스레드에서 실행되므로)
    logger.info("⏳ Waiting for data warm-up (10 seconds)...")
    time.sleep(10)
    
    # 4. 전략 실행
    logger.info("▶️ Executing Trading Strategy...")
    try:
        TradingStrategyService.run_strategy(user_id="sean")
        logger.info("✅ Simulation Complete. Check Slack for alerts.")
    except Exception as e:
        logger.error(f"❌ Simulation Failed: {e}")

if __name__ == "__main__":
    run_simulation()
