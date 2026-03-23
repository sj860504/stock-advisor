from collections import defaultdict
from datetime import datetime
from typing import List, Optional, Union

from utils.market import is_kr, filter_kr, filter_us
from repositories.stock_meta_repo import StockMetaRepo

from models.schemas import ComprehensiveReport, PortfolioContext, KrPortfolio, UsPortfolio, HoldingSchema, MacroDataSnapshot


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
    def format_hourly_gainers(gainers: list, macro: Optional[MacroDataSnapshot]) -> str:
        """Format hourly top gainers report (Currently just Market Summary)."""
        msg = f"🌍 **Market Summary**\n"
        if macro:
            regime = macro.market_regime
            regime_score = getattr(regime, 'regime_score', 50)
            msg += f"🔸 **Status**: {getattr(regime, 'status', '?')} | **{regime_score}/100**\n"
        return msg

    @staticmethod
    def _get_holding_price(holding, ticker: str, states: dict) -> tuple[float, float]:
        """Return holding's current price and change rate, preferring states cache."""
        current_price = holding.current_price or 0
        change_rate = float(0)
        if states and ticker in states:
            state = states[ticker]
            if state and state.change_rate is not None:
                change_rate = state.change_rate
            if current_price <= 0 and getattr(state, "current_price", 0) > 0:
                current_price = state.current_price
        return current_price, change_rate

    @staticmethod
    def _format_kr_holding_line(holding, states: dict) -> str:
        """Format a single KR holding line — 1-line compact."""
        name = holding.name or holding.ticker
        qty = holding.quantity
        buy_price = holding.buy_price or 0
        current_price, _ = ReportService._get_holding_price(holding, holding.ticker, states)
        profit_rate = ((current_price - buy_price) / buy_price * 100) if buy_price > 0 else 0.0
        profit_amt = (current_price - buy_price) * qty if buy_price > 0 else 0.0
        color = "🔴" if profit_amt > 0 else ("🔵" if profit_amt < 0 else "⚪")
        return f"  {color} {name} ₩{current_price:,.0f}×{qty} | {profit_rate:+.1f}% (₩{profit_amt:+,.0f})"

    @staticmethod
    def _format_us_holding_line(holding, states: dict, exchange_rate: float) -> str:
        """Format a single US holding line — 1-line compact."""
        ticker = holding.ticker
        name = holding.name or ticker
        qty = holding.quantity
        buy_price = holding.buy_price or 0
        current_price, _ = ReportService._get_holding_price(holding, ticker, states)
        profit_rate = ((current_price - buy_price) / buy_price * 100) if buy_price > 0 else 0.0
        profit_usd = (current_price - buy_price) * qty if buy_price > 0 else 0.0
        color = "🔴" if profit_usd > 0 else ("🔵" if profit_usd < 0 else "⚪")
        return f"  {color} {ticker} ${current_price:,.2f}×{qty} | {profit_rate:+.1f}% (${profit_usd:+,.2f})"

    @staticmethod
    def _load_portfolio_context(summary: dict = None) -> PortfolioContext:
        """설정/환율/KIS 요약값 일괄 로드. I/O만, 계산 없음."""
        from services.config.settings_service import SettingsService
        from services.market.macro_service import MacroService

        def _sf(key: str) -> float:
            if not summary:
                return 0.0
            try:
                return float(summary.get(key) or 0)
            except (TypeError, ValueError):
                return 0.0

        initial_principal = SettingsService.get_float("PORTFOLIO_INITIAL_PRINCIPAL", 10000000.0)
        usd_cash = SettingsService.get_float("PORTFOLIO_USD_CASH_BALANCE", 0.0)
        if summary:
            try:
                usd_cash = float(summary.get("_usd_cash_balance") or usd_cash or 0)
            except Exception:
                pass

        return PortfolioContext(
            initial_principal=initial_principal,
            usd_cash=usd_cash,
            exchange_rate=MacroService.get_exchange_rate(),
            kis_scts_evlu=_sf("scts_evlu_amt"),
            kis_pchs_amt=_sf("pchs_amt_smtl_amt"),
            kis_evlu_amt=_sf("evlu_amt_smtl_amt"),
            kis_evlu_pfls=_sf("evlu_pfls_smtl_amt"),
            kis_tot_evlu=_sf("tot_evlu_amt"),
            kis_nass=_sf("nass_amt"),
        )

    @staticmethod
    def _calc_kr_portfolio(kr_holdings: List[HoldingSchema], ctx: PortfolioContext, cash_krw: float) -> KrPortfolio:
        """KR 포트폴리오 평가액/손익 계산. KIS 값 우선, 없으면 holdings 직접 계산."""
        stock_val_calc = sum((h.current_price or 0) * h.quantity for h in kr_holdings)
        invested_calc = sum((h.buy_price or 0) * h.quantity for h in kr_holdings)
        stock_val = ctx.kis_evlu_amt if ctx.kis_evlu_amt > 0 else stock_val_calc
        invested = ctx.kis_pchs_amt if ctx.kis_pchs_amt > 0 else invested_calc
        profit = ctx.kis_evlu_pfls if ctx.kis_evlu_pfls != 0 else (stock_val - invested)
        profit_pct = (profit / invested * 100) if invested > 0 else 0.0
        return KrPortfolio(
            stock_val=stock_val, invested=invested,
            profit=profit, profit_pct=profit_pct,
            cash_krw=cash_krw, total=stock_val + cash_krw,
        )

    @staticmethod
    def _calc_us_portfolio(us_holdings: List[HoldingSchema], ctx: PortfolioContext) -> UsPortfolio:
        """US 포트폴리오 평가액/손익 계산."""
        stock_usd = sum((h.current_price or 0) * h.quantity for h in us_holdings)
        invested_usd = sum((h.buy_price or 0) * h.quantity for h in us_holdings)
        profit_usd = stock_usd - invested_usd
        profit_pct = (profit_usd / invested_usd * 100) if invested_usd > 0 else 0.0
        usd_cash_krw = ctx.usd_cash * ctx.exchange_rate
        return UsPortfolio(
            stock_usd=stock_usd, invested_usd=invested_usd,
            profit_usd=profit_usd, profit_pct=profit_pct,
            total_usd=stock_usd + ctx.usd_cash,
            total_krw=stock_usd * ctx.exchange_rate + usd_cash_krw,
            cash_krw=usd_cash_krw,
        )

    @staticmethod
    def _compute_portfolio_totals(holdings: List[HoldingSchema], cash: float, summary: dict = None) -> dict:
        """포트폴리오 합산 지표 반환 — 조율만.

        Uses KIS output2 summary fields when available:
          scts_evlu_amt 유가평가금액 / tot_evlu_amt 총평가금액 / nass_amt 순자산금액
          pchs_amt_smtl_amt 매입금액합계 / evlu_amt_smtl_amt 평가금액합계
          evlu_pfls_smtl_amt 평가손익합계
        """
        ctx = ReportService._load_portfolio_context(summary)
        kr_holdings = filter_kr(holdings)
        us_holdings = filter_us(holdings)
        cash_krw = float(cash) if cash is not None else 0.0

        kr = ReportService._calc_kr_portfolio(kr_holdings, ctx, cash_krw)
        us = ReportService._calc_us_portfolio(us_holdings, ctx)

        total_eval = kr.total + us.total_krw
        principal_profit = total_eval - ctx.initial_principal
        principal_profit_pct = (principal_profit / ctx.initial_principal * 100) if ctx.initial_principal > 0 else 0.0
        principal_color = "🔴" if principal_profit > 0 else ("🔵" if principal_profit < 0 else "⚪")

        return {
            "kr_holdings": kr_holdings, "us_holdings": us_holdings,
            "kr_stock_val": kr.stock_val, "kr_invested": kr.invested,
            "us_stock_usd": us.stock_usd, "us_invested_usd": us.invested_usd,
            "cash_krw": kr.cash_krw, "usd_cash": ctx.usd_cash, "usd_cash_krw": us.cash_krw,
            "kr_total_krw": kr.total, "us_total_usd": us.total_usd, "us_total_krw": us.total_krw,
            "total_eval": total_eval,
            "kr_profit": kr.profit, "kr_profit_pct": kr.profit_pct,
            "us_profit_usd": us.profit_usd, "us_profit_pct": us.profit_pct,
            "principal_profit": principal_profit, "principal_profit_pct": principal_profit_pct,
            "principal_color": principal_color,
            "kr_ratio": (kr.total / total_eval * 100) if total_eval > 0 else 0.0,
            "us_ratio": (us.total_krw / total_eval * 100) if total_eval > 0 else 0.0,
            "exchange_rate": ctx.exchange_rate,
            "kis_tot_evlu": ctx.kis_tot_evlu, "kis_nass": ctx.kis_nass,
        }

    @staticmethod
    def _format_kis_summary_lines(t: dict, summary: dict) -> list:
        """KIS 계좌 요약 라인 반환 (국내순자산, 평가손익합계)."""
        lines = []
        if t.get("kis_tot_evlu"):
            lines.append(f"- KIS 국내순자산 (주식+예수금): {t['kis_tot_evlu']:,.0f}KRW")
        if summary:
            try:
                account_eval_profit = float(summary.get("evlu_pfls_smtl_amt"))
                kis_color = "🔴" if account_eval_profit > 0 else ("🔵" if account_eval_profit < 0 else "⚪")
                lines.append(f"- KIS 평가손익합계: {kis_color} {account_eval_profit:,.0f}KRW")
            except (TypeError, ValueError):
                pass
        return lines

    @staticmethod
    def format_portfolio_report(
        holdings: List[HoldingSchema], cash: float, states: dict = None, summary: dict = None,
        show_kr: bool = True, show_us: bool = True,
    ) -> str:
        """Portfolio status report — displays open market assets only."""
        t = ReportService._compute_portfolio_totals(holdings, cash, summary)

        lines = [
            "📌 **Portfolio Overview**",
            f"- Total Value: {t['total_eval']:,.0f}KRW | Holdings: {len(holdings)}",
            f"- P&L vs Principal: {t['principal_color']} {t['principal_profit']:,.0f}KRW ({t['principal_profit_pct']:+.2f}%)",
        ]

        lines.extend(ReportService._format_kis_summary_lines(t, summary))

        if show_kr:
            lines.extend(ReportService._format_kr_section(
                t['kr_holdings'], t['kr_stock_val'], t['kr_invested'], t['kr_profit'], t['kr_profit_pct'],
                t['cash_krw'], t['kr_total_krw'], t['kr_ratio'], states
            ))
        if show_us:
            lines.extend(ReportService._format_us_section(
                t['us_holdings'], t['us_stock_usd'], t['us_invested_usd'], t['us_profit_usd'], t['us_profit_pct'],
                t['usd_cash'], t['usd_cash_krw'], t['us_total_usd'], t['us_total_krw'], t['us_ratio'],
                t['exchange_rate'], states
            ))
        return "\n".join(lines)

    @staticmethod
    def _format_kr_section(
        kr_holdings: List[HoldingSchema], kr_stock_val: float, kr_invested: float,
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
        us_holdings: List[HoldingSchema], us_stock_usd: float, us_invested_usd: float,
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
    ) -> str:
        """Format a single changed ticker line (compact). BUY/SELL prefix included."""
        diff = after_qty - before_qty
        holding = next((h for h in changed_holdings if h.ticker == ticker), None)
        is_kr_ticker = is_kr(ticker)
        if diff > 0:
            qty = diff
            price = (holding.current_price or 0) if holding else 0
            name = (holding.name or ticker) if holding else ticker
            fmt_price = f"{price:,.0f}KRW" if is_kr_ticker else f"${price:,.2f}"
            return f"🔵 BUY {ticker} {name} {qty}sh @{fmt_price}"
        else:
            qty = abs(diff)
            price = (holding.current_price or 0) if holding else 0
            name = (holding.name or ticker) if holding else ticker
            buy_price = (holding.buy_price or 0) if holding else 0
            profit_pct = ((price - buy_price) / buy_price * 100) if buy_price > 0 else 0.0
            profit = (price - buy_price) * qty
            fmt_price = f"{price:,.0f}KRW" if is_kr_ticker else f"${price:,.2f}"
            fmt_profit = f"{profit:+,.0f}KRW" if is_kr_ticker else f"${profit:+,.2f}"
            return f"🔴 SELL {ticker} {name} {qty}sh @{fmt_price} | {profit_pct:+.1f}% {fmt_profit}"

    @staticmethod
    def format_trade_result_report(
        changed_holdings: List[HoldingSchema], changed_tickers: set,
        before_snapshot: dict, after_snapshot: dict,
        cash: float, states: dict = None, summary: dict = None,
    ) -> str:
        """Trade result report showing only changed holdings after execution."""
        lines = []

        for ticker in sorted(changed_tickers):
            before_qty = before_snapshot.get(ticker, 0)
            after_qty = after_snapshot.get(ticker, 0)
            if before_qty == after_qty:
                continue
            lines.append(ReportService._format_changed_ticker_line(
                ticker, before_qty, after_qty, changed_holdings,
            ))

        if summary:
            total_eval = summary.get('total_eval', 0)
            cash_val = summary.get('cash_krw', 0) + summary.get('usd_cash_krw', 0)
            lines.append(f"💰 총평가 {total_eval:,.0f} | 현금 {cash_val:,.0f}")
        else:
            cash_krw = max(0.0, float(cash)) if cash is not None else 0.0
            lines.append(f"💰 총평가 — | 현금 {cash_krw:,.0f}")

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
    def _format_trade_group_lines(trade_list: List, label: str, icon: str, name_map: dict) -> str:
        """Aggregate buy or sell group and return as Slack message section."""
        groups = ReportService._aggregate_by_ticker(trade_list)
        total_krw = sum(v["total_amt"] for v in groups.values() if v["is_kr"])
        total_usd = sum(v["total_amt"] for v in groups.values() if not v["is_kr"])
        header = f"{icon} **{label}** ({len(trade_list)}"
        if total_krw > 0:
            header += f", KR {total_krw:,.0f}원"
        if total_usd > 0:
            header += f", US {total_usd:,.2f}달러"
        lines = header + ")\n"
        for ticker, info in sorted(groups.items()):
            display = name_map.get(ticker, ticker)
            avg = info["total_amt"] / info["qty"] if info["qty"] else 0
            if info["is_kr"]:
                lines += f"  • {display} {info['qty']}sh | Avg {avg:,.0f}원 | Total {info['total_amt']:,.0f}원\n"
            else:
                lines += f"  • {display} {info['qty']}sh | Avg {avg:,.2f}달러 | Total {info['total_amt']:,.2f}달러\n"
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

        all_tickers = list({t.ticker for t in trades})
        name_map = StockMetaRepo.get_name_map(all_tickers)

        if buys:
            msg += ReportService._format_trade_group_lines(buys, "Buy", "🟢", name_map) + "\n"
        if sells:
            msg += ReportService._format_trade_group_lines(sells, "Sell", "🔴", name_map)
        return msg
