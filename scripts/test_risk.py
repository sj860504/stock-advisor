from services.backtest_service import BacktestService

try:
    results = BacktestService.run_rsi_backtest('AAPL', years=3)

    print('\n=== ?뷂툘 諛깊뀒?ㅽ똿 ?寃? 紐곕뭇 vs ?먭툑愿由?===')
    
    # Strategy A
    res_a = results["A"]
    print(f'[A] 100% 紐곕뭇 ?꾨왂')
    print(f'?뮥 理쒖쥌: ${res_a["final"]:,.0f} (?섏씡瑜?{res_a["return_pct"]:.1f}%)')
    print(f'?뱣 MDD: {res_a["mdd"]:.1f}% (硫섑깉 遺뺢눼 二쇱쓽!)')

    # Strategy B
    res_b = results["B"]
    print(f'\n[B] 30% 遺꾩궛?ъ옄 ?꾨왂 (Risk Managed)')
    print(f'?뮥 理쒖쥌: ${res_b["final"]:,.0f} (?섏씡瑜?{res_b["return_pct"]:.1f}%)')
    print(f'?뱣 MDD: {res_b["mdd"]:.1f}% (?덉젙??')

    diff_mdd = res_a["mdd"] - res_b["mdd"]
    print(f'\n?뮕 寃곕줎: ?먭툑 愿由щ? ?섎㈃ MDD媛 {abs(diff_mdd):.1f}%p 媛쒖꽑?⑸땲??')

except Exception as e:
    print(f"Error: {e}")
