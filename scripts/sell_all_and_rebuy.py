#!/usr/bin/env python3
"""
보유 종목 전량 매도 후 전략대로 재매수 스크립트
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.trading.portfolio_service import PortfolioService
from services.kis.kis_service import KisService
from services.strategy.trading_strategy_service import TradingStrategyService
from utils.logger import get_logger

logger = get_logger("sell_all_rebuy")

def sell_all_holdings(user_id: str = "sean"):
    """보유 종목 전량 매도"""
    logger.info("🔄 KIS 잔고 동기화 중...")
    holdings = PortfolioService.sync_with_kis(user_id)
    
    if not holdings:
        logger.info("✅ 보유 종목이 없습니다.")
        return True
    
    logger.info(f"📊 보유 종목 {len(holdings)}개 확인")
    
    success_count = 0
    fail_count = 0
    
    for holding in holdings:
        ticker = holding['ticker']
        name = holding.get('name', ticker)
        quantity = holding['quantity']
        current_price = holding.get('current_price', 0)
        
        if quantity <= 0:
            continue
        
        logger.info(f"📤 {ticker} ({name}) {quantity}주 매도 시도...")
        
        # 국내/해외 구분
        is_us = not ticker.isdigit()
        
        try:
            if is_us:
                # 해외 주식
                if current_price <= 0:
                    logger.warning(f"⚠️ {ticker} 현재가 정보 없음. 스킵.")
                    fail_count += 1
                    continue
                
                us_price = round(float(current_price), 2)
                res = KisService.send_overseas_order(
                    ticker=ticker,
                    quantity=quantity,
                    price=us_price,
                    order_type="sell"
                )
            else:
                # 국내 주식
                res = KisService.send_order(ticker, quantity, 0, "sell")
            
            if res.get('status') == 'success':
                logger.info(f"✅ {ticker} ({name}) {quantity}주 매도 성공")
                success_count += 1
            else:
                logger.error(f"❌ {ticker} ({name}) 매도 실패: {res.get('msg', 'Unknown error')}")
                fail_count += 1
        except Exception as e:
            logger.error(f"❌ {ticker} ({name}) 매도 중 오류: {e}")
            fail_count += 1
    
    logger.info(f"📊 매도 완료: 성공 {success_count}개, 실패 {fail_count}개")
    return fail_count == 0

def main():
    user_id = "sean"
    
    logger.info("=" * 60)
    logger.info("🚀 보유 종목 전량 매도 후 전략 재매수 시작")
    logger.info("=" * 60)
    
    # 1. 전량 매도
    logger.info("\n[1단계] 보유 종목 전량 매도")
    sell_success = sell_all_holdings(user_id)
    
    if not sell_success:
        logger.warning("⚠️ 일부 종목 매도 실패. 계속 진행합니다.")
    
    # 2. 잔고 동기화 (매도 후 현금 반영)
    logger.info("\n[2단계] 잔고 동기화")
    PortfolioService.sync_with_kis(user_id)
    
    # 3. 전략 실행
    logger.info("\n[3단계] 전략 실행 (매수)")
    try:
        TradingStrategyService.run_strategy(user_id)
        logger.info("✅ 전략 실행 완료")
    except Exception as e:
        logger.error(f"❌ 전략 실행 중 오류: {e}")
        return
    
    logger.info("\n" + "=" * 60)
    logger.info("✅ 모든 작업 완료")
    logger.info("=" * 60)

if __name__ == "__main__":
    main()
