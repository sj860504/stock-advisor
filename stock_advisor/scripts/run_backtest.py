from stock_advisor.services.backtest_service import BacktestService
import json

# 애플 백테스팅 실행
try:
    stats, trades = BacktestService.run_rsi_backtest('AAPL', years=3)

    print('\n=== 📊 Backtest Result (AAPL) ===')
    print(f"💰 초기 자본: ${stats['initial_capital']:,.0f}")
    print(f"💸 최종 가치: ${stats['final_value']:,.0f}")
    print(f"📈 누적 수익률: {stats['total_return_pct']}%")
    print(f"🏆 승률: {stats['win_rate']}%")
    print(f"📉 MDD (최대낙폭): {stats['mdd']}%")
    print(f"🔄 총 매매 횟수: {stats['trade_count']}회")

    print('\n=== 📜 Trade Log (Last 5) ===')
    for t in trades[-5:]:
        type_icon = '🔴 SELL' if t['type'] == 'SELL' else '🔵 BUY'
        profit_str = f" (Profit: {t['profit']:.1f}%)" if 'profit' in t else ''
        print(f"{t['date'].date()} {type_icon} @ ${t['price']:.2f} (RSI: {t['rsi']:.1f}){profit_str}")

except Exception as e:
    print(f"Error: {e}")
