from services.strategy.backtest_service import BacktestService

try:
    results = BacktestService.run_rsi_backtest('AAPL', years=3)

    print('\n=== RSI Backtest: All-in vs Risk Managed ===')

    # Strategy A
    res_a = results["A"]
    print('[A] 100% All-in Strategy')
    print(f'💰 Final: ${res_a["final"]:,.0f} (Return {res_a["return_pct"]:.1f}%)')
    print(f'📉 MDD: {res_a["mdd"]:.1f}%')

    # Strategy B
    res_b = results["B"]
    print('\n[B] 30% Diversified Strategy (Risk Managed)')
    print(f'💰 Final: ${res_b["final"]:,.0f} (Return {res_b["return_pct"]:.1f}%)')
    print(f'📉 MDD: {res_b["mdd"]:.1f}%')

    diff_mdd = res_a["mdd"] - res_b["mdd"]
    print(f'\n✅ Risk management improves MDD by {abs(diff_mdd):.1f}%p.')

except Exception as e:
    print(f"Error: {e}")
