
import os
import sys

# 프로젝트 루트 경로 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.config.settings_service import SettingsService
from services.strategy.trading_strategy_service import TradingStrategyService
from services.market.market_hour_service import MarketHourService
import logging

# 로깅 설정 (콘솔 출력 확인용)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("TestMarketSeparation")

def test_market_separation():
    print("\n=== [Test] Market-Specific Strategy Control ===")
    
    # 1. 초기 상태 확인
    master = TradingStrategyService.is_enabled()
    kr = SettingsService.get_bool("STRATEGY_ENABLED_KR", True)
    us = SettingsService.get_bool("STRATEGY_ENABLED_US", True)
    print(f"Initial Status -> Master: {master}, KR: {kr}, US: {us}")

    # 2. 전제 조건 검사 (영업일 여부와 관계없이 로직 테스트를 위해 mock 사용 고려 가능하나 여기서는 흐름만 확인)
    is_kr_open = MarketHourService.is_kr_market_open()
    is_us_open = MarketHourService.is_us_market_open()
    print(f"Market Status  -> KR Open: {is_kr_open}, US Open: {is_us_open}")

    # 3. 마스터 스위치 OFF 테스트
    print("\n[Scenario 1] Master Switch OFF")
    TradingStrategyService.set_enabled(False)
    TradingStrategyService.run_strategy("sean")
    # 로그에 "⏳ Trading Strategy is currently DISABLED. Skipping analysis." 가 찍혀야 함

    # 4. 개별 시장 OFF 테스트 (KR=False, US=True)
    print("\n[Scenario 2] Master=ON, KR=OFF, US=ON")
    TradingStrategyService.set_enabled(True)
    SettingsService.set_setting("STRATEGY_ENABLED_KR", "false")
    SettingsService.set_setting("STRATEGY_ENABLED_US", "true")
    
    # run_strategy 호출 (현재 KR은 닫혀있고 US는 열려있다고 가정 - 테스트 환경에 따라 다름)
    TradingStrategyService.run_strategy("sean")

    # 5. 원래대로 복구
    print("\n[Restoring Settings]")
    SettingsService.set_setting("STRATEGY_ENABLED", "true" if master else "false")
    SettingsService.set_setting("STRATEGY_ENABLED_KR", "true" if kr else "false")
    SettingsService.set_setting("STRATEGY_ENABLED_US", "true" if us else "false")
    print("Done.")

if __name__ == "__main__":
    test_market_separation()
