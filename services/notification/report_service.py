from collections import defaultdict
from datetime import datetime
from typing import List, Union

from utils.market import is_kr, filter_kr, filter_us

from models.schemas import ComprehensiveReport


class ReportService:
    """Dedicated Slack message and report text generation. Converts data to formatted strings."""

    @staticmethod
    def _format_price_portfolio_lines(price_info: dict, portfolio: dict) -> str:
        """Return current price and holdings section string."""
        change_pct = price_info.get("change_pct", 0)
        change_icon = "📈" if change_pct > 0 else "📉"
        lines = f"💰 **Price**: ${price_info.get('current')} ({change_pct:+.2f}%) {change_icon}\n"
        if portfolio.get("owned"):
            lines += f"💼 **Avg Cost**: ${portfolio.get('avg_cost')} (Return {portfolio.get('return_pct', 0):+.2f}%)\n"
        return lines + "\n"

    @staticmethod
    def _format_fundamental_lines(fundamental: dict) -> str:
        """Return intrinsic value analysis section string."""
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
        """Return technical indicators section string."""
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
        """Return trade conclusion string."""
        if isinstance(upside_dcf, (int, float)) and upside_dcf > 20 and rsi < 40:
            return "🚀 **Strong Buy (Undervalued + Oversold)**"
        if isinstance(upside_dcf, (int, float)) and upside_dcf > 10:
            return "✅ **Consider Buy (Undervalued)**"
        if rsi > 75:
            return "⚠️ **Consider Sell (Short-term Overheated)**"
        return "👀 **Hold & Watch**"

    @staticmethod
    def format_comprehensive_report(data: Union[dict, ComprehensiveReport]) -> str:
        """Convert comprehensive analysis data (dict or ComprehensiveReport) to Slack message text."""
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
        """Format hourly top gainers report."""
        msg = f"🌍 **Market Summary**\n"
        if macro:
            regime = macro.get('market_regime')
            regime_score = getattr(regime, 'regime_score', 50)
            comp = getattr(regime, 'components', None)
            od = getattr(comp, 'other_detail', None) if comp else None
            t_s = getattr(comp, 'technical', 10) if comp else 10
            v_s = getattr(comp, 'vix', 10) if comp else 10
            f_s = getattr(comp, 'fear_greed', 10) if comp else 10
            e_s = getattr(comp, 'economic', 10) if comp else 10
            o_s = getattr(comp, 'other', 10) if comp else 10
            td = getattr(comp, 'technical_detail', {}) if comp else {}
            spx_1m = td.get('spx_1m_ret') if isinstance(td, dict) else getattr(td, 'spx_1m_ret', None)
            spread = getattr(od, 'yield_spread_10y2y', None) if od else None
            vix_1m = getattr(od, 'vix_1m_chg', None) if od else None
            btc_ret = getattr(od, 'btc_1m_ret', None) if od else None
            dxy_ret = getattr(od, 'dxy_1m_ret', None) if od else None
            gold_ret = getattr(od, 'gold_1m_ret', None) if od else None
            spx_str = f"SPX1M{spx_1m:+.1f}%" if spx_1m is not None else ""
            spread_str = f"{spread:+.2f}%" if spread is not None else "-"
            vix_str = f"(1M{vix_1m:+.0f}%)" if vix_1m is not None else ""
            oil_ret = getattr(od, 'oil_1m_ret', None) if od else None
            btc_str = f"BTC{btc_ret:+.1f}%" if btc_ret is not None else "BTC-"
            dxy_str = f"DXY{dxy_ret:+.1f}%" if dxy_ret is not None else "DXY-"
            gold_str = f"Gold{gold_ret:+.1f}%" if gold_ret is not None else "Gold-"
            oil_str = f"Oil{oil_ret:+.1f}%" if oil_ret is not None else "Oil-"
            phase = getattr(regime, 'economic_phase', '') if regime else ''
            phase_mod = getattr(regime, 'phase_modifier', 0) if regime else 0
            phase_str = f" [{phase}{phase_mod:+d}]" if phase and phase != "Neutral" else ""
            msg += f"🔸 **Status**: {getattr(regime, 'status', '?')} | **{regime_score}/100** (Tech{t_s} VIX{v_s} F&G{f_s} Econ{e_s} Other{o_s}){phase_str}\n"
            msg += f"🔸 **SPX**: MA200 {getattr(regime, 'diff_pct', 0):+.1f}% {spx_str} | Yield {macro.get('us_10y_yield')}% | Curve {spread_str} | VIX {macro.get('vix')}{vix_str} | {btc_str} | {dxy_str} | {gold_str} | {oil_str}\n"

            crypto = macro.get('crypto', {})
            btc = crypto.get('BTC') if isinstance(crypto, dict) else None
            if btc:
                msg += f"🔸 **BTC**: ${btc.price:,.0f} ({btc.change:+.2f}%)\n"

            commodities = macro.get('commodities', {})
            gold = commodities.get('Gold') if isinstance(commodities, dict) else None
            oil = commodities.get('Oil') if isinstance(commodities, dict) else None
            if gold and oil:
                msg += f"🔸 **Gold**: ${gold.price:,.1f} ({gold.change:+.2f}%) | **Oil**: ${oil.price:,.2f} ({oil.change:+.2f}%)\n"
        
        msg += "\n🚀 **Signal Gainers Report**\n"
        for gainer in gainers:
            state_icon = "🌙" if gainer.get("market") == "Pre-market" else "☀️"
            msg += f"{state_icon} **{gainer.get('name')} ({gainer.get('ticker')})**: +{gainer.get('change', 0):.2f}% (${gainer.get('price', 0):.2f})\n"
        return msg

    @staticmethod
    def _get_holding_price(holding: dict, ticker: str, states: dict) -> tuple[float, float]:
        """Return holding's current price and change rate, preferring states cache."""
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
        """Format a single KR holding line (KRW based)."""
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
        """Format a single US holding line (USD based, with KRW conversion)."""
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
    def _compute_portfolio_totals(holdings: list, cash: float, summary: dict = None) -> dict:
        """Return portfolio totals, P&L, and ratios as dict."""
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

        return {
            "kr_holdings": kr_holdings, "us_holdings": us_holdings,
            "kr_stock_val": kr_stock_val, "kr_invested": kr_invested,
            "us_stock_usd": us_stock_usd, "us_invested_usd": us_invested_usd,
            "cash_krw": cash_krw, "usd_cash": usd_cash, "usd_cash_krw": usd_cash_krw,
            "kr_total_krw": kr_total_krw, "us_total_usd": us_total_usd, "us_total_krw": us_total_krw,
            "total_eval": total_eval,
            "kr_profit": kr_profit, "kr_profit_pct": kr_profit_pct,
            "us_profit_usd": us_profit_usd, "us_profit_pct": us_profit_pct,
            "principal_profit": principal_profit, "principal_profit_pct": principal_profit_pct,
            "principal_color": principal_color,
            "kr_ratio": kr_ratio, "us_ratio": us_ratio, "exchange_rate": exchange_rate,
        }

    @staticmethod
    def format_portfolio_report(holdings: list, cash: float, states: dict = None, summary: dict = None) -> str:
        """Portfolio status report — displays KRW/foreign currency assets separately."""
        t = ReportService._compute_portfolio_totals(holdings, cash, summary)

        lines = [
            "📌 **Portfolio Overview**",
            f"- Total Value: {t['total_eval']:,.0f}KRW | Holdings: {len(holdings)}",
            f"- P&L vs Principal: {t['principal_color']} {t['principal_profit']:,.0f}KRW ({t['principal_profit_pct']:+.2f}%)",
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
            t['kr_holdings'], t['kr_stock_val'], t['kr_invested'], t['kr_profit'], t['kr_profit_pct'],
            t['cash_krw'], t['kr_total_krw'], t['kr_ratio'], states
        ))
        lines.extend(ReportService._format_us_section(
            t['us_holdings'], t['us_stock_usd'], t['us_invested_usd'], t['us_profit_usd'], t['us_profit_pct'],
            t['usd_cash'], t['usd_cash_krw'], t['us_total_usd'], t['us_total_krw'], t['us_ratio'],
            t['exchange_rate'], states
        ))
        return "\n".join(lines)

    @staticmethod
    def _format_kr_section(
        kr_holdings: list, kr_stock_val: float, kr_invested: float,
        kr_profit: float, kr_profit_pct: float, cash_krw: float,
        kr_total_krw: float, kr_ratio: float, states: dict,
    ) -> list:
        """Return KRW assets section lines."""
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
        """Return USD assets section lines."""
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
    def _format_changed_ticker_line(
        ticker: str, before_qty: int, after_qty: int, changed_holdings: list,
    ) -> tuple:
        """Format a single changed ticker line. Returns (line_str, is_buy)."""
        diff = after_qty - before_qty
        holding = next((h for h in changed_holdings if h["ticker"] == ticker), None)
        if diff > 0:
            label = "New Buy" if before_qty == 0 else "Add Buy"
            if holding:
                price = holding.get("current_price", 0)
                name = holding.get("name") or ticker
                if is_kr(ticker):
                    return f"  🟢 {ticker} {name} | {label} {diff}sh | Price {price:,.0f}KRW (Hold {after_qty}sh)", True
                else:
                    return f"  🟢 {ticker} {name} | {label} {diff}sh | Price ${price:,.2f} (Hold {after_qty}sh)", True
            return f"  🟢 {ticker} | {label} {diff}sh (Hold {after_qty}sh)", True
        else:
            sold = abs(diff)
            label = "Sell All" if after_qty == 0 else "Partial Sell"
            if holding:
                price = holding.get("current_price", 0)
                name = holding.get("name") or ticker
                buy_price = holding.get("buy_price", 0)
                profit_pct = ((price - buy_price) / buy_price * 100) if buy_price > 0 else 0.0
                color = "🔴" if profit_pct > 0 else "🔵"
                return f"  {color} {ticker} {name} | {label} {sold}sh | {profit_pct:+.2f}% (Remain {after_qty}sh)", False
            return f"  ⚪ {ticker} | {label} {sold}sh", False

    @staticmethod
    def format_trade_result_report(
        changed_holdings: list, changed_tickers: set,
        before_snapshot: dict, after_snapshot: dict,
        cash: float, states: dict = None, summary: dict = None,
    ) -> str:
        """Trade result report showing only changed holdings after execution."""
        lines = ["📈 **Trade Execution Report**", ""]

        buy_lines, sell_lines = [], []
        for ticker in sorted(changed_tickers):
            before_qty = before_snapshot.get(ticker, 0)
            after_qty = after_snapshot.get(ticker, 0)
            diff = after_qty - before_qty
            if diff == 0:
                continue
            line, is_buy = ReportService._format_changed_ticker_line(
                ticker, before_qty, after_qty, changed_holdings,
            )
            if is_buy:
                buy_lines.append(line)
            else:
                sell_lines.append(line)

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
        """Aggregate trade records by ticker."""
        grouped = defaultdict(lambda: {"qty": 0, "total_amt": 0.0, "is_kr": True})
        for t in trade_list:
            grouped[t.ticker]["qty"] += t.quantity
            grouped[t.ticker]["total_amt"] += t.quantity * t.price
            grouped[t.ticker]["is_kr"] = is_kr(t.ticker)
        return grouped

    @staticmethod
    def _format_trade_group_lines(trade_list: List, label: str, icon: str) -> str:
        """Aggregate buy or sell group and return as Slack message section."""
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
        """Format daily trade history as Slack message. Aggregated by ticker."""
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
