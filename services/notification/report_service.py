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
        lines = f"💰 **Price**: ${price_info.get('current')} ({change_pct:+.2f}%) {change_icon}\n"
        if portfolio.get("owned"):
            lines += f"💼 **Avg Cost**: ${portfolio.get('avg_cost')} (Return {portfolio.get('return_pct', 0):+.2f}%)\n"
        return lines + "\n"

    @staticmethod
    def _format_fundamental_lines(fundamental: dict) -> str:
        """내재 가치 분석 섹션 문자열 반환."""
        dcf_fair   = fundamental.get("dcf_fair", "N/A")
        upside_dcf = fundamental.get("upside_dcf", 0)
        lines = f"💎 **Intrinsic Value**\n🔸 DCF Fair: **${dcf_fair}** (Upside {upside_dcf:+.1f}%)\n"
        analyst_target = fundamental.get("analyst_target")
        if analyst_target is not None:
            upside_analyst = fundamental.get("upside_analyst", 0)
            lines += f"🔸 Analyst Target: **${analyst_target}** (Upside {upside_analyst:+.1f}%)\n"
        return lines + "\n"

    @staticmethod
    def _format_technical_lines(technical: dict, current_price: float) -> str:
        """기술적 지표 섹션 문자열 반환."""
        rsi = technical.get("rsi", 50)
        rsi_status = "🔥 Overbought" if rsi > 70 else ("🥶 Oversold" if rsi < 30 else "⚖️ Neutral")
        lines = f"🛠 **Technical Indicators**\n🔸 RSI: {rsi} ({rsi_status})\n"
        ema200 = technical.get("emas", {}).get(200)
        if ema200 is not None:
            dist = round((current_price - ema200) / ema200 * 100, 1)
            lines += f"🔸 EMA200: {dist:+.1f}% ({'Above' if current_price > ema200 else 'Below'})\n"
        return lines + "\n"

    @staticmethod
    def _build_conclusion_line(upside_dcf, rsi: float) -> str:
        """매매 결론 문자열 반환."""
        if isinstance(upside_dcf, (int, float)) and upside_dcf > 20 and rsi < 40:
            return "🚀 **Strong Buy (Undervalued + Oversold)**"
        if isinstance(upside_dcf, (int, float)) and upside_dcf > 10:
            return "✅ **Consider Buy (Undervalued)**"
        if rsi > 75:
            return "⚠️ **Consider Sell (Short-term Overheated)**"
        return "👀 **Hold & Watch**"

    @staticmethod
    def format_comprehensive_report(data: Union[dict, ComprehensiveReport]) -> str:
        """종합 분석 데이터(딕셔너리 또는 ComprehensiveReport)를 Slack 메시지 텍스트로 변환합니다."""
        if isinstance(data, ComprehensiveReport):
            data = data.to_report_dict()
        if "error" in data:
            return f"❌ Analysis failed: {data['error']}"

        price_info    = data.get("price_info", {})
        fundamental   = data.get("fundamental", {})
        technical     = data.get("technical", {})
        macro_context = data.get("macro_context", {})

        msg  = f"📊 **[{data.get('name')} ({data.get('ticker')})] Analysis Report**\n\n"
        msg += ReportService._format_price_portfolio_lines(price_info, data.get("portfolio", {}))
        msg += ReportService._format_fundamental_lines(fundamental)
        msg += ReportService._format_technical_lines(technical, price_info.get("current", 0))

        if macro_context:
            msg += f"🌍 **Macro**: {macro_context.get('regime')} Market (VIX: {macro_context.get('vix')})\n\n"
        if "news_summary" in data:
            msg += data["news_summary"]

        rsi        = technical.get("rsi", 50)
        upside_dcf = fundamental.get("upside_dcf", 0)
        msg += f"\n💡 **Conclusion**: {ReportService._build_conclusion_line(upside_dcf, rsi)}"
        return msg

    @staticmethod
    def format_hourly_gainers(gainers: list, macro: dict) -> str:
        """시간별 급등 종목 리포트 포맷팅"""
        msg = f"🌍 **Market Summary**\n"
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
            oil_ret = od.get('oil_1m_ret')
            btc_str = f"BTC{btc_ret:+.1f}%" if btc_ret is not None else "BTC-"
            dxy_str = f"DXY{dxy_ret:+.1f}%" if dxy_ret is not None else "DXY-"
            gold_str = f"Gold{gold_ret:+.1f}%" if gold_ret is not None else "Gold-"
            oil_str = f"Oil{oil_ret:+.1f}%" if oil_ret is not None else "Oil-"
            phase = regime.get('economic_phase', '')
            phase_mod = regime.get('phase_modifier', 0)
            phase_str = f" [{phase}{phase_mod:+d}]" if phase and phase != "Neutral" else ""
            msg += f"🔸 **Status**: {regime.get('status')} | **{regime_score}/100** (Tech{t_s} VIX{v_s} F&G{f_s} Econ{e_s} Other{o_s}){phase_str}\n"
            msg += f"🔸 **SPX**: MA200 {regime.get('diff_pct', 0):+.1f}% {spx_str} | Yield {macro.get('us_10y_yield')}% | Curve {spread_str} | VIX {macro.get('vix')}{vix_str} | {btc_str} | {dxy_str} | {gold_str} | {oil_str}\n"
            
            btc = macro.get('crypto', {}).get('BTC')
            if btc:
                msg += f"🔸 **BTC**: ${btc['price']:,.0f} ({btc['change']:+.2f}%)\n"
            
            commodities = macro.get('commodities', {})
            gold = commodities.get('Gold')
            oil = commodities.get('Oil')
            if gold and oil:
                msg += f"🔸 **Gold**: ${gold['price']:,.1f} ({gold['change']:+.2f}%) | **Oil**: ${oil['price']:,.2f} ({oil['change']:+.2f}%)\n"
        
        msg += "\n🚀 **Signal Gainers Report**\n"
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
            f"  • {ticker} {name} {current_price:,.0f}KRW ({change_rate:+.2f}%) "
            f"{qty}sh │ Avg {buy_price:,.0f}KRW │ {profit_rate:+.2f}% {color}{profit_amt:,.0f}KRW"
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
            f"{qty}sh │ Avg ${buy_price:,.2f} │ {profit_rate:+.2f}% {color}${profit_usd:,.2f} ({profit_usd * exchange_rate:,.0f}KRW)"
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
            "📌 **Portfolio Overview**",
            f"- Total Value: {total_eval:,.0f}KRW | Holdings: {len(holdings)}",
            f"- P&L vs Principal: {principal_color} {principal_profit:,.0f}KRW ({principal_profit_pct:+.2f}%)",
        ]

        account_eval_profit = None
        if summary:
            try:
                account_eval_profit = float(summary.get("evlu_pfls_smtl_amt"))
            except (TypeError, ValueError):
                pass
        if account_eval_profit is not None:
            kis_color = "🔴" if account_eval_profit > 0 else ("🔵" if account_eval_profit < 0 else "⚪")
            lines.append(f"- Account P&L (KIS): {kis_color} {account_eval_profit:,.0f}KRW")

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
            f"🇰🇷 **KRW Assets** — {kr_total_krw:,.0f}KRW ({kr_ratio:.1f}%)",
            f"  Cash: {cash_krw:,.0f}KRW",
            f"  Stocks: {kr_stock_val:,.0f}KRW (Invested {kr_invested:,.0f}KRW │ {color}{kr_profit:+,.0f}KRW / {kr_profit_pct:+.2f}%)",
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
            f"🇺🇸 **USD Assets** — ${us_total_usd:,.2f} ({us_total_krw:,.0f}KRW / {us_ratio:.1f}%)",
            f"  Cash: ${usd_cash:,.2f} ({usd_cash_krw:,.0f}KRW)",
            f"  Stocks: ${us_stock_usd:,.2f} (Invested ${us_invested_usd:,.2f} │ {color}${us_profit_usd:+,.2f} / {us_profit_pct:+.2f}%)",
        ]
        if us_holdings:
            lines.append("")
            lines.extend(ReportService._format_us_holding_line(h, states, exchange_rate) for h in us_holdings)
        return lines

    @staticmethod
    def format_trade_result_report(
        changed_holdings: list, changed_tickers: set,
        before_snapshot: dict, after_snapshot: dict,
        cash: float, states: dict = None, summary: dict = None,
    ) -> str:
        """매매 후 변동된 종목만 표시하는 거래 결과 리포트."""
        from services.market.macro_service import MacroService
        exchange_rate = MacroService.get_exchange_rate()

        lines = ["📈 **Trade Execution Report**", ""]

        buy_lines, sell_lines = [], []
        for ticker in sorted(changed_tickers):
            before_qty = before_snapshot.get(ticker, 0)
            after_qty = after_snapshot.get(ticker, 0)
            diff = after_qty - before_qty
            holding = next((h for h in changed_holdings if h["ticker"] == ticker), None)

            if diff > 0:
                label = "New Buy" if before_qty == 0 else "Add Buy"
                if holding:
                    price = holding.get("current_price", 0)
                    name = holding.get("name") or ticker
                    if is_kr(ticker):
                        buy_lines.append(f"  🟢 {ticker} {name} | {label} {diff}sh | Price {price:,.0f}KRW (Hold {after_qty}sh)")
                    else:
                        buy_lines.append(f"  🟢 {ticker} {name} | {label} {diff}sh | Price ${price:,.2f} (Hold {after_qty}sh)")
                else:
                    buy_lines.append(f"  🟢 {ticker} | {label} {diff}sh (Hold {after_qty}sh)")
            elif diff < 0:
                sold = abs(diff)
                label = "Sell All" if after_qty == 0 else "Partial Sell"
                if holding:
                    price = holding.get("current_price", 0)
                    name = holding.get("name") or ticker
                    buy_price = holding.get("buy_price", 0)
                    profit_pct = ((price - buy_price) / buy_price * 100) if buy_price > 0 else 0.0
                    color = "🔴" if profit_pct > 0 else "🔵"
                    if is_kr(ticker):
                        sell_lines.append(f"  {color} {ticker} {name} | {label} {sold}sh | {profit_pct:+.2f}% (Remain {after_qty}sh)")
                    else:
                        sell_lines.append(f"  {color} {ticker} {name} | {label} {sold}sh | {profit_pct:+.2f}% (Remain {after_qty}sh)")
                else:
                    sell_lines.append(f"  ⚪ {ticker} | {label} {sold}sh")

        if buy_lines:
            lines.append(f"**Buy** ({len(buy_lines)})")
            lines.extend(buy_lines)
            lines.append("")
        if sell_lines:
            lines.append(f"**Sell** ({len(sell_lines)})")
            lines.extend(sell_lines)
            lines.append("")

        cash_krw = max(0.0, float(cash)) if cash is not None else 0.0
        lines.append(f"💰 Cash: {cash_krw:,.0f}KRW")

        return "\n".join(lines)

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
        header = f"{icon} **{label}** ({len(trade_list)}"
        if total_krw > 0:
            header += f", KR {total_krw:,.0f}KRW"
        if total_usd > 0:
            header += f", US ${total_usd:,.2f}"
        lines = header + ")\n"
        for ticker, info in sorted(groups.items()):
            avg = info["total_amt"] / info["qty"] if info["qty"] else 0
            if info["is_kr"]:
                lines += f"  • {ticker} {info['qty']}sh | Avg {avg:,.0f}KRW | Total {info['total_amt']:,.0f}KRW\n"
            else:
                lines += f"  • {ticker} {info['qty']}sh | Avg ${avg:,.2f} | Total ${info['total_amt']:,.2f}\n"
        return lines

    @staticmethod
    def format_daily_trade_history(trades: list, start_dt: datetime, end_dt: datetime) -> str:
        """일일 매매 내역을 Slack 메시지로 포맷팅합니다. 티커별로 집계하여 보여줍니다."""
        date_str = start_dt.strftime("%m/%d %H:%M") + " ~ " + end_dt.strftime("%m/%d %H:%M")
        msg = f"📋 **Daily Trade History** ({date_str})\n\n"

        if not trades:
            msg += "📭 No trades in this period."
            return msg

        buys  = [t for t in trades if t.order_type == "buy"]
        sells = [t for t in trades if t.order_type == "sell"]
        msg  += f"📊 Total **{len(trades)}** (Buy {len(buys)} / Sell {len(sells)})\n\n"

        if buys:
            msg += ReportService._format_trade_group_lines(buys, "Buy", "🟢") + "\n"
        if sells:
            msg += ReportService._format_trade_group_lines(sells, "Sell", "🔴")
        return msg
