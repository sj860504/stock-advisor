from collections import defaultdict
from datetime import datetime
from typing import List, Union

from utils.market import is_kr, filter_kr, filter_us

from models.schemas import ComprehensiveReport


class ReportService:
    """Slack 메시지 및 리포트 텍스트 생성 전담. 데이터를 받아 문자열로 변환합니다."""

    @staticmethod
    def _format_price_portfolio_lines(price_info: dict, portfolio: dict) -> str:
        """현재가·보유 현황 섹션 문자열 반환."""
        change_pct = price_info.get("change_pct", 0)
        change_icon = "📈" if change_pct > 0 else "📉"
        lines = f"💰 **현재가**: ${price_info.get('current')} ({change_pct:+.2f}%) {change_icon}\n"
        if portfolio.get("owned"):
            lines += f"💼 **나의 평단**: ${portfolio.get('avg_cost')} (현재 수익률 {portfolio.get('return_pct', 0):+.2f}%)\n"
        return lines + "\n"

    @staticmethod
    def _format_fundamental_lines(fundamental: dict) -> str:
        """내재 가치 분석 섹션 문자열 반환."""
        dcf_fair   = fundamental.get("dcf_fair", "N/A")
        upside_dcf = fundamental.get("upside_dcf", 0)
        lines = f"💎 **내재 가치 분석**\n🔸 DCF 적정가: **${dcf_fair}** (상승여력 {upside_dcf:+.1f}%)\n"
        analyst_target = fundamental.get("analyst_target")
        if analyst_target is not None:
            upside_analyst = fundamental.get("upside_analyst", 0)
            lines += f"🔸 기관 목표가: **${analyst_target}** (상승여력 {upside_analyst:+.1f}%)\n"
        return lines + "\n"

    @staticmethod
    def _format_technical_lines(technical: dict, current_price: float) -> str:
        """기술적 지표 섹션 문자열 반환."""
        rsi = technical.get("rsi", 50)
        rsi_status = "🔥 과매수" if rsi > 70 else ("🥶 과매도" if rsi < 30 else "⚖️ 중립")
        lines = f"🛠 **기술적 지표**\n🔸 RSI: {rsi} ({rsi_status})\n"
        ema200 = technical.get("emas", {}).get(200)
        if ema200 is not None:
            dist = round((current_price - ema200) / ema200 * 100, 1)
            lines += f"🔸 EMA200 대비: {dist:+.1f}% ({'정배열' if current_price > ema200 else '역배열'})\n"
        return lines + "\n"

    @staticmethod
    def _build_conclusion_line(upside_dcf, rsi: float) -> str:
        """매매 결론 문자열 반환."""
        if isinstance(upside_dcf, (int, float)) and upside_dcf > 20 and rsi < 40:
            return "🚀 **강력 매수 찬스 (저평가+과매도)**"
        if isinstance(upside_dcf, (int, float)) and upside_dcf > 10:
            return "✅ **매수 고려 (저평가)**"
        if rsi > 75:
            return "⚠️ **매도/익절 고려 (단기 과열)**"
        return "👀 **보유 및 관망**"

    @staticmethod
    def format_comprehensive_report(data: Union[dict, ComprehensiveReport]) -> str:
        """종합 분석 데이터(딕셔너리 또는 ComprehensiveReport)를 Slack 메시지 텍스트로 변환합니다."""
        if isinstance(data, ComprehensiveReport):
            data = data.to_report_dict()
        if "error" in data:
            return f"❌ 분석 실패: {data['error']}"

        price_info    = data.get("price_info", {})
        fundamental   = data.get("fundamental", {})
        technical     = data.get("technical", {})
        macro_context = data.get("macro_context", {})

        msg  = f"📊 **[{data.get('name')} ({data.get('ticker')})] 종합 분석 리포트**\n\n"
        msg += ReportService._format_price_portfolio_lines(price_info, data.get("portfolio", {}))
        msg += ReportService._format_fundamental_lines(fundamental)
        msg += ReportService._format_technical_lines(technical, price_info.get("current", 0))

        if macro_context:
            msg += f"🌍 **거시 환경**: {macro_context.get('regime')} Market (VIX: {macro_context.get('vix')})\n\n"
        if "news_summary" in data:
            msg += data["news_summary"]

        rsi        = technical.get("rsi", 50)
        upside_dcf = fundamental.get("upside_dcf", 0)
        msg += f"\n💡 **AI 결론**: {ReportService._build_conclusion_line(upside_dcf, rsi)}"
        return msg

    @staticmethod
    def format_hourly_gainers(gainers: list, macro: dict) -> str:
        """시간별 급등 종목 리포트 포맷팅"""
        msg = f"🌍 **시장 현황 요약**\n"
        if macro:
            regime = macro.get('market_regime', {})
            regime_score = regime.get('regime_score', 50)
            comp = regime.get('components', {})
            od = comp.get('other_detail', {})
            t_s = comp.get('technical', 10)
            v_s = comp.get('vix', 10)
            f_s = comp.get('fear_greed', 10)
            e_s = comp.get('economic', 10)
            o_s = comp.get('other', 10)
            td = comp.get('technical_detail', {})
            spx_1m = td.get('spx_1m_ret')
            spread = od.get('yield_spread_10y2y')
            vix_1m = od.get('vix_1m_chg')
            btc_ret = od.get('btc_1m_ret')
            dxy_ret = od.get('dxy_1m_ret')
            gold_ret = od.get('gold_1m_ret')
            spx_str = f"SPX1M{spx_1m:+.1f}%" if spx_1m is not None else ""
            spread_str = f"{spread:+.2f}%" if spread is not None else "-"
            vix_str = f"(1M{vix_1m:+.0f}%)" if vix_1m is not None else ""
            btc_str = f"BTC{btc_ret:+.1f}%" if btc_ret is not None else "BTC-"
            dxy_str = f"DXY{dxy_ret:+.1f}%" if dxy_ret is not None else "DXY-"
            gold_str = f"Gold{gold_ret:+.1f}%" if gold_ret is not None else "Gold-"
            msg += f"🔸 **상태**: {regime.get('status')} | **{regime_score}점/100** (기술{t_s} VIX{v_s} F&G{f_s} 경제{e_s} 기타{o_s})\n"
            msg += f"🔸 **SPX**: MA200 {regime.get('diff_pct', 0):+.1f}% {spx_str} | 금리 {macro.get('us_10y_yield')}% | 곡선 {spread_str} | VIX {macro.get('vix')}{vix_str} | {btc_str} | {dxy_str} | {gold_str}\n"
            
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

        kr_holdings = filter_kr(holdings)
        us_holdings = filter_us(holdings)

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

        lines.extend(ReportService._format_kr_section(
            kr_holdings, kr_stock_val, kr_invested, kr_profit, kr_profit_pct, cash_krw, kr_total_krw, kr_ratio, states
        ))
        lines.extend(ReportService._format_us_section(
            us_holdings, us_stock_usd, us_invested_usd, us_profit_usd, us_profit_pct,
            usd_cash, usd_cash_krw, us_total_usd, us_total_krw, us_ratio, exchange_rate, states
        ))
        return "\n".join(lines)

    @staticmethod
    def _format_kr_section(
        kr_holdings: list, kr_stock_val: float, kr_invested: float,
        kr_profit: float, kr_profit_pct: float, cash_krw: float,
        kr_total_krw: float, kr_ratio: float, states: dict,
    ) -> list:
        """원화 자산 섹션 lines 반환."""
        color = "🔴" if kr_profit > 0 else ("🔵" if kr_profit < 0 else "⚪")
        lines = [
            "",
            f"🇰🇷 **원화 자산** — {kr_total_krw:,.0f}원 ({kr_ratio:.1f}%)",
            f"  현금: {cash_krw:,.0f}원",
            f"  주식 평가: {kr_stock_val:,.0f}원 (투자 {kr_invested:,.0f}원 │ {color}{kr_profit:+,.0f}원 / {kr_profit_pct:+.2f}%)",
        ]
        if kr_holdings:
            lines.append("")
            lines.extend(ReportService._format_kr_holding_line(h, states) for h in kr_holdings)
        return lines

    @staticmethod
    def _format_us_section(
        us_holdings: list, us_stock_usd: float, us_invested_usd: float,
        us_profit_usd: float, us_profit_pct: float, usd_cash: float,
        usd_cash_krw: float, us_total_usd: float, us_total_krw: float,
        us_ratio: float, exchange_rate: float, states: dict,
    ) -> list:
        """외화 자산 섹션 lines 반환."""
        color = "🔴" if us_profit_usd > 0 else ("🔵" if us_profit_usd < 0 else "⚪")
        lines = [
            "",
            f"🇺🇸 **외화 자산** — ${us_total_usd:,.2f} ({us_total_krw:,.0f}원 / {us_ratio:.1f}%)",
            f"  현금: ${usd_cash:,.2f} ({usd_cash_krw:,.0f}원)",
            f"  주식 평가: ${us_stock_usd:,.2f} (투자 ${us_invested_usd:,.2f} │ {color}${us_profit_usd:+,.2f} / {us_profit_pct:+.2f}%)",
        ]
        if us_holdings:
            lines.append("")
            lines.extend(ReportService._format_us_holding_line(h, states, exchange_rate) for h in us_holdings)
        return lines

    @staticmethod
    def _aggregate_by_ticker(trade_list: List) -> dict:
        """매매 내역을 티커별로 집계합니다."""
        grouped = defaultdict(lambda: {"qty": 0, "total_amt": 0.0, "is_kr": True})
        for t in trade_list:
            grouped[t.ticker]["qty"] += t.quantity
            grouped[t.ticker]["total_amt"] += t.quantity * t.price
            grouped[t.ticker]["is_kr"] = is_kr(t.ticker)
        return grouped

    @staticmethod
    def _format_trade_group_lines(trade_list: List, label: str, icon: str) -> str:
        """매수 또는 매도 그룹을 집계하여 Slack 메시지 섹션으로 반환합니다."""
        groups = ReportService._aggregate_by_ticker(trade_list)
        total_krw = sum(v["total_amt"] for v in groups.values() if v["is_kr"])
        total_usd = sum(v["total_amt"] for v in groups.values() if not v["is_kr"])
        header = f"{icon} **{label}** ({len(trade_list)}건"
        if total_krw > 0:
            header += f", KR {total_krw:,.0f}원"
        if total_usd > 0:
            header += f", US ${total_usd:,.2f}"
        lines = header + ")\n"
        for ticker, info in sorted(groups.items()):
            avg = info["total_amt"] / info["qty"] if info["qty"] else 0
            if info["is_kr"]:
                lines += f"  • {ticker} {info['qty']}주 | 평균 {avg:,.0f}원 | 합계 {info['total_amt']:,.0f}원\n"
            else:
                lines += f"  • {ticker} {info['qty']}주 | 평균 ${avg:,.2f} | 합계 ${info['total_amt']:,.2f}\n"
        return lines

    @staticmethod
    def format_daily_trade_history(trades: list, start_dt: datetime, end_dt: datetime) -> str:
        """일일 매매 내역을 Slack 메시지로 포맷팅합니다. 티커별로 집계하여 보여줍니다."""
        date_str = start_dt.strftime("%m/%d %H:%M") + " ~ " + end_dt.strftime("%m/%d %H:%M")
        msg = f"📋 **일일 매매 히스토리** ({date_str})\n\n"

        if not trades:
            msg += "📭 해당 기간 매매 내역이 없습니다."
            return msg

        buys  = [t for t in trades if t.order_type == "buy"]
        sells = [t for t in trades if t.order_type == "sell"]
        msg  += f"📊 총 **{len(trades)}건** (매수 {len(buys)}건 / 매도 {len(sells)}건)\n\n"

        if buys:
            msg += ReportService._format_trade_group_lines(buys, "매수", "🟢") + "\n"
        if sells:
            msg += ReportService._format_trade_group_lines(sells, "매도", "🔴")
        return msg
