from typing import Union

from models.schemas import ComprehensiveReport


class ReportService:
    """Slack 메시지 및 리포트 텍스트 생성 전담. 데이터를 받아 문자열로 변환합니다."""

    @staticmethod
    def format_comprehensive_report(data: Union[dict, ComprehensiveReport]) -> str:
        """종합 분석 데이터(딕셔너리 또는 ComprehensiveReport)를 Slack 메시지 텍스트로 변환합니다."""
        if isinstance(data, ComprehensiveReport):
            data = data.to_report_dict()
        if "error" in data:
            return f"❌ 분석 실패: {data['error']}"

        price_info = data.get("price_info", {})
        fundamental = data.get("fundamental", {})
        technical = data.get("technical", {})
        portfolio = data.get("portfolio", {})
        macro_context = data.get("macro_context", {})

        msg = f"📊 **[{data.get('name')} ({data.get('ticker')})] 종합 분석 리포트**\n\n"
        change_pct = price_info.get("change_pct", 0)
        change_icon = "📈" if change_pct > 0 else "📉"
        msg += f"💰 **현재가**: ${price_info.get('current')} ({change_pct:+.2f}%) {change_icon}\n"
        if portfolio.get("owned"):
            msg += f"💼 **나의 평단**: ${portfolio.get('avg_cost')} (현재 수익률 {portfolio.get('return_pct', 0):+.2f}%)\n"
        msg += "\n"

        msg += "💎 **내재 가치 분석**\n"
        dcf_fair = fundamental.get("dcf_fair", "N/A")
        upside_dcf = fundamental.get("upside_dcf", 0)
        msg += f"🔸 DCF 적정가: **${dcf_fair}** (상승여력 {upside_dcf:+.1f}%)\n"
        analyst_target = fundamental.get("analyst_target")
        if analyst_target is not None:
            upside_analyst = fundamental.get("upside_analyst", 0)
            msg += f"🔸 기관 목표가: **${analyst_target}** (상승여력 {upside_analyst:+.1f}%)\n\n"

        rsi = technical.get("rsi", 50)
        rsi_status = "🔥 과매수" if rsi > 70 else ("🥶 과매도" if rsi < 30 else "⚖️ 중립")
        msg += "🛠 **기술적 지표**\n"
        msg += f"🔸 RSI: {rsi} ({rsi_status})\n"
        emas = technical.get("emas", {})
        current_price = price_info.get("current", 0)
        ema200 = emas.get(200)
        if ema200 is not None:
            dist = round((current_price - ema200) / ema200 * 100, 1)
            msg += f"🔸 EMA200 대비: {dist:+.1f}% ({'정배열' if current_price > ema200 else '역배열'})\n\n"

        if macro_context:
            msg += f"🌍 **거시 환경**: {macro_context.get('regime')} Market (VIX: {macro_context.get('vix')})\n\n"
        if "news_summary" in data:
            msg += data["news_summary"]

        conclusion = "판단 유보"
        if isinstance(upside_dcf, (int, float)) and upside_dcf > 20 and rsi < 40:
            conclusion = "🚀 **강력 매수 찬스 (저평가+과매도)**"
        elif isinstance(upside_dcf, (int, float)) and upside_dcf > 10:
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
        for gainer in gainers:
            state_icon = "🌙" if gainer.get("market") == "Pre-market" else "☀️"
            msg += f"{state_icon} **{gainer.get('name')} ({gainer.get('ticker')})**: +{gainer.get('change', 0):.2f}% (${gainer.get('price', 0):.2f})\n"
        return msg

    @staticmethod
    def _get_holding_price(holding: dict, ticker: str, states: dict) -> tuple[float, float]:
        """보유 종목의 현재가와 등락률을 states 캐시 우선으로 반환합니다."""
        current_price = holding.get("current_price", 0)
        change_rate = float(holding.get("change_rate", 0) or 0)
        if states and ticker in states:
            state = states[ticker]
            if state and state.change_rate is not None:
                change_rate = state.change_rate
            if current_price <= 0 and getattr(state, "current_price", 0) > 0:
                current_price = state.current_price
        return current_price, change_rate

    @staticmethod
    def _format_kr_holding_line(holding: dict, states: dict) -> str:
        """국내 보유 종목 한 줄 포맷팅 (원화 기준)."""
        ticker = holding.get("ticker", "")
        name = holding.get("name") or ""
        qty = holding.get("quantity", 0)
        buy_price = holding.get("buy_price", 0)
        current_price, change_rate = ReportService._get_holding_price(holding, ticker, states)
        profit_rate = ((current_price - buy_price) / buy_price * 100) if buy_price > 0 else 0.0
        profit_amt = (current_price - buy_price) * qty if buy_price > 0 else 0.0
        color = "🔴" if profit_amt > 0 else ("🔵" if profit_amt < 0 else "⚪")
        return (
            f"  • {ticker} {name} {current_price:,.0f}원 ({change_rate:+.2f}%) "
            f"{qty}주 │ 평단 {buy_price:,.0f}원 │ {profit_rate:+.2f}% {color}{profit_amt:,.0f}원"
        )

    @staticmethod
    def _format_us_holding_line(holding: dict, states: dict, exchange_rate: float) -> str:
        """미국 보유 종목 한 줄 포맷팅 (달러 기준, 원화 환산 병기)."""
        ticker = holding.get("ticker", "")
        name = holding.get("name") or ""
        qty = holding.get("quantity", 0)
        buy_price = holding.get("buy_price", 0)
        current_price, change_rate = ReportService._get_holding_price(holding, ticker, states)
        profit_rate = ((current_price - buy_price) / buy_price * 100) if buy_price > 0 else 0.0
        profit_usd = (current_price - buy_price) * qty if buy_price > 0 else 0.0
        color = "🔴" if profit_usd > 0 else ("🔵" if profit_usd < 0 else "⚪")
        return (
            f"  • {ticker} {name} ${current_price:,.2f} ({change_rate:+.2f}%) "
            f"{qty}주 │ 평단 ${buy_price:,.2f} │ {profit_rate:+.2f}% {color}${profit_usd:,.2f} ({profit_usd * exchange_rate:,.0f}원)"
        )

    @staticmethod
    def format_portfolio_report(holdings: list, cash: float, states: dict = None, summary: dict = None) -> str:
        """포트폴리오 현황 리포트 — 원화/외화 자산을 분리하여 표시합니다."""
        from services.config.settings_service import SettingsService
        from services.market.macro_service import MacroService

        initial_principal = SettingsService.get_float("PORTFOLIO_INITIAL_PRINCIPAL", 10000000.0)
        usd_cash = SettingsService.get_float("PORTFOLIO_USD_CASH_BALANCE", 0.0)
        exchange_rate = MacroService.get_exchange_rate()
        if summary:
            try:
                usd_cash = float(summary.get("_usd_cash_balance") or usd_cash or 0)
            except Exception:
                pass

        kr_holdings = [h for h in holdings if str(h.get("ticker", "")).isdigit()]
        us_holdings = [h for h in holdings if not str(h.get("ticker", "")).isdigit()]

        kr_stock_val = sum(h.get("current_price", 0) * h.get("quantity", 0) for h in kr_holdings)
        kr_invested = sum(h.get("buy_price", 0) * h.get("quantity", 0) for h in kr_holdings)
        us_stock_usd = sum(h.get("current_price", 0) * h.get("quantity", 0) for h in us_holdings)
        us_invested_usd = sum(h.get("buy_price", 0) * h.get("quantity", 0) for h in us_holdings)
        us_stock_krw = us_stock_usd * exchange_rate
        us_invested_krw = us_invested_usd * exchange_rate
        cash_krw = max(0.0, float(cash)) if cash is not None else 0.0
        usd_cash_krw = usd_cash * exchange_rate

        kr_total_krw = kr_stock_val + cash_krw
        us_total_usd = us_stock_usd + usd_cash
        us_total_krw = us_stock_krw + usd_cash_krw
        total_eval = kr_total_krw + us_total_krw

        kr_profit = kr_stock_val - kr_invested
        kr_profit_pct = (kr_profit / kr_invested * 100) if kr_invested > 0 else 0.0
        us_profit_usd = us_stock_usd - us_invested_usd
        us_profit_pct = (us_profit_usd / us_invested_usd * 100) if us_invested_usd > 0 else 0.0

        principal_profit = total_eval - initial_principal
        principal_profit_pct = (principal_profit / initial_principal * 100) if initial_principal > 0 else 0.0
        principal_color = "🔴" if principal_profit > 0 else ("🔵" if principal_profit < 0 else "⚪")

        kr_ratio = (kr_total_krw / total_eval * 100) if total_eval > 0 else 0.0
        us_ratio = (us_total_krw / total_eval * 100) if total_eval > 0 else 0.0

        lines = [
            "📌 **포트폴리오 현황**",
            f"- 전체 평가 금액: {total_eval:,.0f}원  |  보유 종목: {len(holdings)}개",
            f"- 초기원금 대비 손익: {principal_color} {principal_profit:,.0f}원 ({principal_profit_pct:+.2f}%)",
        ]

        account_eval_profit = None
        if summary:
            try:
                account_eval_profit = float(summary.get("evlu_pfls_smtl_amt"))
            except (TypeError, ValueError):
                pass
        if account_eval_profit is not None:
            kis_color = "🔴" if account_eval_profit > 0 else ("🔵" if account_eval_profit < 0 else "⚪")
            lines.append(f"- 계좌 평가손익(KIS): {kis_color} {account_eval_profit:,.0f}원")

        # ── 원화 자산 ──────────────────────────────
        kr_profit_color = "🔴" if kr_profit > 0 else ("🔵" if kr_profit < 0 else "⚪")
        lines.append("")
        lines.append(f"🇰🇷 **원화 자산** — {kr_total_krw:,.0f}원 ({kr_ratio:.1f}%)")
        lines.append(f"  현금: {cash_krw:,.0f}원")
        lines.append(f"  주식 평가: {kr_stock_val:,.0f}원 (투자 {kr_invested:,.0f}원 │ {kr_profit_color}{kr_profit:+,.0f}원 / {kr_profit_pct:+.2f}%)")
        if kr_holdings:
            lines.append("")
            for h in kr_holdings:
                lines.append(ReportService._format_kr_holding_line(h, states))

        # ── 외화 자산 ──────────────────────────────
        us_profit_color = "🔴" if us_profit_usd > 0 else ("🔵" if us_profit_usd < 0 else "⚪")
        lines.append("")
        lines.append(f"🇺🇸 **외화 자산** — ${us_total_usd:,.2f} ({us_total_krw:,.0f}원 / {us_ratio:.1f}%)")
        lines.append(f"  현금: ${usd_cash:,.2f} ({usd_cash_krw:,.0f}원)")
        lines.append(f"  주식 평가: ${us_stock_usd:,.2f} (투자 ${us_invested_usd:,.2f} │ {us_profit_color}${us_profit_usd:+,.2f} / {us_profit_pct:+.2f}%)")
        if us_holdings:
            lines.append("")
            for h in us_holdings:
                lines.append(ReportService._format_us_holding_line(h, states, exchange_rate))

        return "\n".join(lines)
