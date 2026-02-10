from stock_advisor.services.backtest_service import BacktestService

try:
    results = BacktestService.run_rsi_backtest('AAPL', years=3)

    print('\n=== ⚔️ 백테스팅 대결: 몰빵 vs 자금관리 ===')
    
    # Strategy A
    res_a = results["A"]
    print(f'[A] 100% 몰빵 전략')
    print(f'💰 최종: ${res_a["final"]:,.0f} (수익률 {res_a["return_pct"]:.1f}%)')
    print(f'📉 MDD: {res_a["mdd"]:.1f}% (멘탈 붕괴 주의!)')

    # Strategy B
    res_b = results["B"]
    print(f'\n[B] 30% 분산투자 전략 (Risk Managed)')
    print(f'💰 최종: ${res_b["final"]:,.0f} (수익률 {res_b["return_pct"]:.1f}%)')
    print(f'📉 MDD: {res_b["mdd"]:.1f}% (안정적)')

    diff_mdd = res_a["mdd"] - res_b["mdd"]
    print(f'\n💡 결론: 자금 관리를 하면 MDD가 {abs(diff_mdd):.1f}%p 개선됩니다.')

except Exception as e:
    print(f"Error: {e}")
