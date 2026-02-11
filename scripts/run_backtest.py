from services.backtest_service import BacktestService
import json

# ?좏뵆 諛깊뀒?ㅽ똿 ?ㅽ뻾
try:
    stats, trades = BacktestService.run_rsi_backtest('AAPL', years=3)

    print('\n=== ?뱤 Backtest Result (AAPL) ===')
    print(f"?뮥 珥덇린 ?먮낯: ${stats['initial_capital']:,.0f}")
    print(f"?뮯 理쒖쥌 媛移? ${stats['final_value']:,.0f}")
    print(f"?뱢 ?꾩쟻 ?섏씡瑜? {stats['total_return_pct']}%")
    print(f"?룇 ?밸쪧: {stats['win_rate']}%")
    print(f"?뱣 MDD (理쒕??숉룺): {stats['mdd']}%")
    print(f"?봽 珥?留ㅻℓ ?잛닔: {stats['trade_count']}??)

    print('\n=== ?뱶 Trade Log (Last 5) ===')
    for t in trades[-5:]:
        type_icon = '?뵶 SELL' if t['type'] == 'SELL' else '?뵷 BUY'
        profit_str = f" (Profit: {t['profit']:.1f}%)" if 'profit' in t else ''
        print(f"{t['date'].date()} {type_icon} @ ${t['price']:.2f} (RSI: {t['rsi']:.1f}){profit_str}")

except Exception as e:
    print(f"Error: {e}")
