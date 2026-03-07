from services.strategy.backtest_service import BacktestService
import json

# RSI 백테스팅 실행
try:
    stats, trades = BacktestService.run_rsi_backtest('AAPL', years=3)

    print('\n=== RSI Backtest Result (AAPL) ===')
    print(f"📊 Initial capital: ${stats['initial_capital']:,.0f}")
    print(f"💰 Final value: ${stats['final_value']:,.0f}")
    print(f"📈 Cumulative return: {stats['total_return_pct']}%")
    print(f"🎯 Win rate: {stats['win_rate']}%")
    print(f"📉 MDD (Max Drawdown): {stats['mdd']}%")
    print(f"🔄 Total trades: {stats['trade_count']}")

    print('\n=== Trade Log (Last 5) ===')
    for t in trades[-5:]:
        type_icon = '🔴 SELL' if t['type'] == 'SELL' else '🔵 BUY'
        profit_str = f" (Profit: {t['profit']:.1f}%)" if 'profit' in t else ''
        print(f"{t['date'].date()} {type_icon} @ ${t['price']:.2f} (RSI: {t['rsi']:.1f}){profit_str}")

except Exception as e:
    print(f"Error: {e}")
