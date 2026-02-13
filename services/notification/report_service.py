class ReportService:
    """
    Slack 메시지 및 리포트 텍스트 생성 전담 서비스
    (데이터를 받아서 예쁜 문자열로 변환)
    """

    @staticmethod
    def format_comprehensive_report(data: dict) -> str:
        """종합 분석 데이터를 Slack 메시지 텍스트로 변환"""
        if "error" in data: return f"❌ 분석 실패: {data['error']}"
        
        p = data.get('price_info', {})
        f = data.get('fundamental', {})
        t = data.get('technical', {})
        port = data.get('portfolio', {})
        m = data.get('macro_context', {})
        
        msg = f"📊 **[{data.get('name')} ({data.get('ticker')})] 종합 분석 리포트**\n\n"
        
        # 1. 가격 및 포트폴리오
        change_icon = "📈" if p.get('change_pct', 0) > 0 else "📉"
        msg += f"💰 **현재가**: ${p.get('current')} ({p.get('change_pct', 0):+.2f}%) {change_icon}\n"
        if port.get('owned'):
            msg += f"💼 **나의 평단**: ${port.get('avg_cost')} (현재 수익률 {port.get('return_pct', 0):+.2f}%)\n"
        msg += "\n"
        
        # 2. 가치 평가
        msg += "💎 **내재 가치 분석**\n"
        dcf_fair = f.get('dcf_fair', 'N/A')
        upside = f.get('upside_dcf', 0)
        msg += f"🔸 DCF 적정가: **${dcf_fair}** (상승여력 {upside:+.1f}%)\n"
        
        target = f.get('analyst_target')
        if target:
            t_upside = f.get('upside_analyst', 0)
            msg += f"🔸 기관 목표가: **${target}** (상승여력 {t_upside:+.1f}%)\n\n"
        
        # 3. 기술적 지표
        rsi = t.get('rsi', 50)
        rsi_status = "🔥 과매수" if rsi > 70 else ("🥶 과매도" if rsi < 30 else "⚖️ 중립")
        msg += f"🛠 **기술적 지표**\n"
        msg += f"🔸 RSI: {rsi} ({rsi_status})\n"
        
        emas = t.get('emas', {})
        current = p.get('current', 0)
        ema200 = emas.get('ema200')
        if ema200:
            dist = round((current - ema200)/ema200*100, 1)
            msg += f"🔸 EMA200 대비: {dist:+.1f}% ({'정배열' if current > ema200 else '역배열'})\n\n"
        
        # 4. 거시 환경
        if m:
            msg += f"🌍 **거시 환경**: {m.get('regime')} Market (VIX: {m.get('vix')})\n\n"
        
        # 5. 뉴스
        if 'news_summary' in data:
            msg += data['news_summary']
        
        # 6. 결론
        conclusion = "판단 유보"
        if isinstance(upside, (int, float)) and upside > 20 and rsi < 40: 
            conclusion = "🚀 **강력 매수 찬스 (저평가+과매도)**"
        elif isinstance(upside, (int, float)) and upside > 10: 
            conclusion = "✅ **매수 고려 (저평가)**"
        elif rsi > 75: 
            conclusion = "⚠️ **매도/익절 고려 (단기 과열)**"
        else: 
            conclusion = "👀 **보유 및 관망**"
        
        msg += f"\n💡 **AI 결론**: {conclusion}"
        
        return msg

    @staticmethod
    def format_hourly_gainers(gainers: list, macro: dict) -> str:
        """시간별 급등 종목 리포트 포맷팅"""
        msg = f"🌍 **시장 현황 요약**\n"
        if macro:
            regime = macro.get('market_regime', {})
            msg += f"🔸 **상태**: {regime.get('status')} ({regime.get('diff_pct', 0):+.1f}% above MA200)\n"
            msg += f"🔸 **금리**: {macro.get('us_10y_yield')}%\n"
            msg += f"🔸 **VIX**: {macro.get('vix')}\n"
            
            btc = macro.get('crypto', {}).get('BTC')
            if btc:
                msg += f"🔸 **BTC**: ${btc['price']:,.0f} ({btc['change']:+.2f}%)\n"
            
            commodities = macro.get('commodities', {})
            gold = commodities.get('Gold')
            oil = commodities.get('Oil')
            if gold and oil:
                msg += f"🔸 **Gold**: ${gold['price']:,.1f} ({gold['change']:+.2f}%) | **Oil**: ${oil['price']:,.2f} ({oil['change']:+.2f}%)\n"
        
        msg += "\n🚀 **전분 시그널 급등 리포트 (전체)**\n"
        for g in gainers: 
            state_icon = "🌙" if g['market'] == "Pre-market" else "☀️"
            msg += f"{state_icon} **{g['name']} ({g['ticker']})**: +{g['change']:.2f}% (${g['price']:.2f})\n"
            
        return msg

    @staticmethod
    def format_portfolio_report(holdings: list, cash: float, states: dict = None, summary: dict = None) -> str:
        """포트폴리오 현황 리포트 포맷팅"""
        total_value = sum(h.get("current_price", 0) * h.get("quantity", 0) for h in holdings)
        total_eval = cash + total_value
        total_profit = None
        if summary:
            try:
                total_profit = float(summary.get("evlu_pfls_smtl_amt"))
            except Exception:
                total_profit = None
        msg_lines = [
            "📌 **포트폴리오 현황**",
            f"- 전체 평가 금액: {total_eval:,.0f}원",
            f"- 보유 현금: {cash:,.0f}원",
            f"- 보유 종목 수: {len(holdings)}",
            f"- 보유 평가액: {total_value:,.0f}원",
        ]
        if total_profit is not None:
            total_color = "🔴" if total_profit > 0 else ("🔵" if total_profit < 0 else "⚪")
            msg_lines.append(f"- 계좌 전체 손익: {total_color} {total_profit:,.0f}원")
        for h in holdings:
            ticker = h.get("ticker")
            name = h.get("name") or ""
            qty = h.get("quantity", 0)
            buy_price = h.get("buy_price", 0)
            current_price = h.get("current_price", 0)
            change_rate = float(h.get("change_rate", 0) or 0)
            if states and ticker in states:
                state = states[ticker]
                if state and state.change_rate is not None:
                    change_rate = state.change_rate
                if current_price <= 0 and getattr(state, "current_price", 0) > 0:
                    current_price = state.current_price
            profit_rate = ((current_price - buy_price) / buy_price * 100) if buy_price > 0 else 0.0
            profit_amt = (current_price - buy_price) * qty if buy_price > 0 else 0.0
            profit_color = "🔴" if profit_amt > 0 else ("🔵" if profit_amt < 0 else "⚪")
            msg_lines.append(
                f"  • {ticker} {name} "
                f"{current_price:,.0f}원 "
                f"({change_rate:+.2f}%) "
                f"{qty}주 "
                f"평균단가 {buy_price:,.0f}원 "
                f"수익률 {profit_rate:+.2f}% "
                f"수익금 {profit_color} {profit_amt:,.0f}원"
            )
        return "\n".join(msg_lines)
