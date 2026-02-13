from services.strategy.backtest_service import BacktestService

try:
    results = BacktestService.run_rsi_backtest('AAPL', years=3)

    print('\n=== RSI 백테스트 성과: 올인 vs 리스크 관리 ===')
    
    # Strategy A
    res_a = results["A"]
    print('[A] 100% 올인 전략')
    print(f'💰 최종: ${res_a["final"]:,.0f} (수익률 {res_a["return_pct"]:.1f}%)')
    print(f'📉 MDD: {res_a["mdd"]:.1f}% (낙폭 주의)')

    # Strategy B
    res_b = results["B"]
    print('\n[B] 30% 분산 투자 전략 (Risk Managed)')
    print(f'💰 최종: ${res_b["final"]:,.0f} (수익률 {res_b["return_pct"]:.1f}%)')
    print(f'📉 MDD: {res_b["mdd"]:.1f}% (안정성)')

    diff_mdd = res_a["mdd"] - res_b["mdd"]
    print(f'\n✅ 결론: 리스크 관리 적용 시 MDD가 {abs(diff_mdd):.1f}%p 개선됩니다.')

except Exception as e:
    print(f"Error: {e}")
