"""Portfolio management service (DB + KIS sync)."""
import json
import os
from datetime import datetime
from typing import Dict, List, Optional

from models.schemas import PortfolioHoldingDto, HoldingSchema, PortfolioSchema
from repositories.portfolio_repo import PortfolioRepo
from services.base.file_service import FileService
from services.config.settings_service import SettingsService
from services.kis.kis_service import KisService
from services.market.data_service import DataService
from services.market.stock_meta_service import StockMetaService
from services.market.ticker_service import TickerService
from services.notification.alert_service import AlertService
from utils.logger import get_logger
from utils.market import is_kr, filter_kr, filter_us, profit_pct

logger = get_logger("portfolio_service")

DEFAULT_SECTOR = "Others"
DEFAULT_EXCHANGE_RATE = 1350.0


class PortfolioService:
    """Portfolio and holdings management (DB persistence, KIS balance sync)."""
    _last_balance_summary: dict = {}

    @staticmethod
    def _extract_float(data: dict, *keys) -> float:
        """Return the first valid float from dict keys. Allows negatives; avoids "0" string truthy bug."""
        for key in keys:
            val = data.get(key)
            if val is not None and val != "":
                try:
                    return float(val)
                except (TypeError, ValueError):
                    pass
        return 0.0

    @classmethod
    def _extract_holding_fields(cls, h) -> tuple:
        """Extract common fields from HoldingSchema or dict."""
        if isinstance(h, dict):
            return (
                h.get("ticker", ""),
                h.get("name"),
                h.get("quantity", 0),
                h.get("buy_price", 0.0),
                h.get("current_price") or 0.0,
                h.get("sector") or DEFAULT_SECTOR,
            )
        return (
            h.ticker,
            h.name,
            h.quantity,
            h.buy_price,
            h.current_price or 0.0,
            h.sector or DEFAULT_SECTOR,
        )

    @classmethod
    def save_portfolio(
        cls,
        user_id: str,
        holdings,
        cash_balance: Optional[float] = None,
    ) -> bool:
        """Save portfolio and holdings to DB. Accepts both HoldingSchema and dict."""
        holding_dicts = []
        for h in holdings:
            ticker, name, quantity, buy_price, current_price, sector = cls._extract_holding_fields(h)
            holding_dicts.append({
                "ticker": ticker, "name": name, "quantity": quantity,
                "buy_price": buy_price, "current_price": current_price, "sector": sector,
            })
        return PortfolioRepo.save(user_id, holding_dicts, cash_balance)

    @classmethod
    def load_portfolio(cls, user_id: str) -> List[HoldingSchema]:
        """Load portfolio holdings from DB and return as list of HoldingSchema."""
        raw = PortfolioRepo.load_holdings(user_id)
        return [HoldingSchema(**h) for h in raw]

    @classmethod
    def load_portfolio_dtos(cls, user_id: str) -> List[PortfolioHoldingDto]:
        """Load portfolio holdings from DB and return as list of DTOs."""
        return [PortfolioHoldingDto(**h) for h in cls.load_portfolio(user_id)]

    @classmethod
    def load_cash(cls, user_id: str) -> float:
        """Load cash balance from DB."""
        return PortfolioRepo.load_cash(user_id)

    @classmethod
    def _parse_balance_holdings(
        cls, balance_data: dict, existing_sector_map: dict
    ) -> tuple[List[HoldingSchema], Dict[str, HoldingSchema]]:
        """Parse KIS balance data into KR holdings list and US holdings dict."""
        holdings: List[HoldingSchema] = []
        us_by_ticker: Dict[str, HoldingSchema] = {}
        for item in balance_data.get("holdings", []):
            ticker = str(item.get('pdno') or item.get('symb') or "").strip().upper()
            if not ticker:
                continue
            qty = int(float(item.get('hldg_qty') or item.get('ovrs_cblc_qty') or item.get('ord_psbl_qty') or 0))
            if qty <= 0:
                continue
            parsed = HoldingSchema(
                ticker=ticker,
                name=item.get('prdt_name') or item.get('ovrs_item_name') or item.get('hldg_pdno_name') or ticker,
                quantity=qty,
                buy_price=float(item.get('pchs_avg_pric') or item.get('pavg_unit_amt') or 0),
                current_price=float(item.get('prpr') or item.get('ovrs_now_pric') or item.get('now_pric') or 0),
                sector=existing_sector_map.get(ticker, DEFAULT_SECTOR),
            )
            if is_kr(ticker):
                holdings.append(parsed)
            else:
                us_by_ticker[ticker] = parsed
        return holdings, us_by_ticker

    @classmethod
    def _apply_overseas_balance_override(
        cls, overseas_balance: dict, us_by_ticker: Dict[str, HoldingSchema], existing_sector_map: dict
    ) -> None:
        """Override US holdings with latest overseas balance data."""
        if not overseas_balance or not overseas_balance.get("holdings"):
            return
        for item in overseas_balance["holdings"]:
            ticker = str(
                item.get("ovrs_pdno") or item.get("pdno") or item.get("symb") or item.get("ovrs_item_cd") or ""
            ).strip().upper()
            if not ticker or is_kr(ticker):
                continue
            qty = int(float(item.get("ovrs_cblc_qty") or item.get("hldg_qty") or item.get("ord_psbl_qty") or 0))
            if qty <= 0:
                continue
            us_by_ticker[ticker] = HoldingSchema(
                ticker=ticker,
                name=item.get("ovrs_item_name") or item.get("prdt_name") or ticker,
                quantity=qty,
                buy_price=float(item.get("pchs_avg_pric") or item.get("avg_unpr") or item.get("pavg_unit_amt") or 0),
                current_price=float(
                    item.get("now_pric2") or item.get("ovrs_now_pric") or item.get("prpr") or item.get("now_pric") or 0
                ),
                sector=existing_sector_map.get(ticker, DEFAULT_SECTOR),
            )

    @classmethod
    def _extract_kr_cash_from_summary(cls, balance_data: dict) -> tuple[dict, float]:
        """Return the best summary item and KR cash from balance_data.

        KIS output2 key fields (from API docs 주식잔고조회 v1_국내주식-006):
          dnca_tot_amt          예수금총금액
          prvs_rcdl_excc_amt    가수도정산금액 (D+2 예수금, 실질 주문가능현금)
          scts_evlu_amt         유가평가금액 (보유주식 평가합계)
          tot_evlu_amt          총평가금액 (= 유가평가금액 + D+2 예수금)
          nass_amt              순자산금액
          pchs_amt_smtl_amt     매입금액합계금액
          evlu_amt_smtl_amt     평가금액합계금액
          evlu_pfls_smtl_amt    평가손익합계금액
        """
        summary_items = balance_data.get("summary", [])
        if len(summary_items) > 1:
            summary = max(
                summary_items,
                key=lambda r: float(r.get("tot_evlu_amt") or r.get("dnca_tot_amt") or 0),
            )
        else:
            summary = summary_items[0] if summary_items else {}
        # D+2 예수금 = 실질 현금 (미수/신용 시 음수 가능)
        prvs = cls._extract_float(summary, "prvs_rcdl_excc_amt")
        dnca = cls._extract_float(summary, "dnca_tot_amt")
        cash = prvs if prvs != 0 else dnca
        return summary, cash

    @classmethod
    def _resolve_us_holdings(
        cls,
        overseas_balance: Optional[dict],
        us_by_ticker: Dict[str, HoldingSchema],
        existing_us_map: Dict[str, HoldingSchema],
        existing_sector_map: dict,
    ) -> List[HoldingSchema]:
        """해외 잔고 stale 여부에 따라 US holdings 결정. Returns list to extend main holdings."""
        if overseas_balance is None or overseas_balance.get("_stale", False):
            if overseas_balance and overseas_balance.get("_stale", False):
                logger.warning("⚠️ Overseas balance is stale (cached fallback). Keeping existing DB US holdings instead.")
            return list(existing_us_map.values())
        
        # API 성공 사례 (비어있더라도 갱신함)
        cls._apply_overseas_balance_override(overseas_balance, us_by_ticker, existing_sector_map)
        return list(us_by_ticker.values())

    @classmethod
    def _enrich_summary_with_overseas(cls, summary: dict, overseas_balance: Optional[dict]) -> None:
        """해외 잔고 요약 정보를 KR summary dict에 주입 (in-place)."""
        if not overseas_balance or not overseas_balance.get("summary"):
            return
        ovrs_summary = overseas_balance["summary"]
        if isinstance(ovrs_summary, list) and ovrs_summary:
            ovrs_summary = ovrs_summary[0]
        if not isinstance(ovrs_summary, dict):
            return
        tot_evlu_pfls = cls._extract_float(ovrs_summary, "tot_evlu_pfls_amt")
        summary["_overseas_summary"] = ovrs_summary
        if tot_evlu_pfls != 0:
            summary["_overseas_evlu_pfls_krw"] = tot_evlu_pfls

    @classmethod
    def sync_with_kis(cls, user_id: str = "sean") -> List[HoldingSchema]:
        """Sync with actual KIS balance (includes DB update)."""
        logger.info(f"🔄 Syncing portfolio with KIS for user: {user_id}")
        balance_data = KisService.get_balance()
        if not balance_data:
            return cls.load_portfolio(user_id)

        existing_holdings = cls.load_portfolio(user_id)
        existing_sector_map = {h.ticker: h.sector or DEFAULT_SECTOR for h in existing_holdings}
        existing_us_map = {
            h.ticker: h
            for h in existing_holdings
            if str(h.ticker).isalpha()
        }

        holdings, us_by_ticker = cls._parse_balance_holdings(balance_data, existing_sector_map)
        
        # 미국 시장 전략 활성화 여부 확인
        is_us_strategy_enabled = SettingsService.get_bool("STRATEGY_ENABLED_US", True)
        overseas_balance = None
        
        if is_us_strategy_enabled:
            overseas_balance = KisService.get_overseas_balance()
            holdings.extend(cls._resolve_us_holdings(overseas_balance, us_by_ticker, existing_us_map, existing_sector_map))
        else:
            logger.info("🇺🇸 US Strategy disabled. Skipping overseas balance sync.")
            # 이미 가지고 있는 미국 종목은 유지
            holdings.extend(list(existing_us_map.values()))

        summary, cash = cls._extract_kr_cash_from_summary(balance_data)

        cls._enrich_summary_with_overseas(summary, overseas_balance)

        summary["_usd_cash_balance"] = cls.get_usd_cash_balance(overseas_balance=overseas_balance)
        cls._last_balance_summary = summary

        cls.save_portfolio(user_id, holdings, cash_balance=cash)
        cls._sync_in_memory_prices(holdings)
        return holdings

    @classmethod
    def _sync_in_memory_prices(cls, holdings: List[HoldingSchema]) -> None:
        """Push current_price from each holding into MarketDataService in-memory state."""
        from services.market.market_data_service import MarketDataService
        for h in holdings:
            price = float(h.current_price or 0)
            if price > 0:
                MarketDataService.update_price_from_sync(h.ticker, price)

    @classmethod
    def get_last_balance_summary(cls) -> dict:
        return cls._last_balance_summary or {}

    @classmethod
    def get_usd_cash_balance(cls, overseas_balance: Optional[dict] = None) -> float:
        """Query US foreign cash (USD): KIS available buy amount API → overseas balance reverse-calc → settings."""
        # 1. Try direct API query
        available_usd = KisService.get_overseas_available_cash()
        if available_usd is not None and available_usd > 0:
            return available_usd

        # 2. Reverse-calculate from overseas balance summary
        if overseas_balance and overseas_balance.get("summary"):
            try:
                ovrs_summary = overseas_balance["summary"]
                if isinstance(ovrs_summary, list) and ovrs_summary:
                    ovrs_summary = ovrs_summary[0]
                if isinstance(ovrs_summary, dict):
                    # tot_aset_amt = total asset (holdings + cash) in USD
                    # frcr_pchs_amt1 = foreign currency purchase amount (holdings cost)
                    tot_aset = cls._extract_float(ovrs_summary, "tot_aset_amt")
                    frcr_evlu = cls._extract_float(ovrs_summary, "frcr_evlu_amt2", "evlu_amt_smtl_amt")
                    if tot_aset > 0 and frcr_evlu > 0:
                        usd_cash = tot_aset - frcr_evlu
                        if usd_cash > 0:
                            logger.info(f"💰 USD cash (reverse-calc from overseas summary): ${usd_cash:,.2f}")
                            SettingsService.set_setting("PORTFOLIO_USD_CASH_BALANCE", str(usd_cash))
                            return usd_cash
            except Exception as e:
                logger.warning(f"⚠️ Failed to reverse-calc USD cash from overseas summary: {e}")

        # 3. Fall back to settings
        return SettingsService.get_float("PORTFOLIO_USD_CASH_BALANCE", 0.0)

    @staticmethod
    def _calc_kr_holding_results(kr_holdings: List[dict]) -> tuple:
        """Calculate KR holdings invested amount, current value, and result list."""
        invested = current = 0.0
        results = []
        for h in kr_holdings:
            val = h.get("quantity", 0) * (h.get("current_price") or 0.0)
            inv = h.get("quantity", 0) * h.get("buy_price", 0)
            invested += inv
            current  += val
            results.append({**h, 'profit': round(val - inv, 2), 'profit_pct': profit_pct(val, inv), 'market': 'KR'})
        return invested, current, results

    @staticmethod
    def _calc_us_holding_results(us_holdings: List[dict], exchange_rate: float) -> tuple:
        """Calculate US holdings invested amount, current value, and result list."""
        invested_usd = current_usd = 0.0
        results = []
        for h in us_holdings:
            val_usd = h.get("quantity", 0) * (h.get("current_price") or 0.0)
            inv_usd = h.get("quantity", 0) * h.get("buy_price", 0)
            invested_usd += inv_usd
            current_usd  += val_usd
            results.append({
                **h,
                'profit_usd': round(val_usd - inv_usd, 2),
                'profit_krw': round((val_usd - inv_usd) * exchange_rate, 2),
                'profit_pct': profit_pct(val_usd, inv_usd),
                'market': 'US',
            })
        return invested_usd, current_usd, results

    @classmethod
    def analyze_portfolio(cls, user_id: str, price_cache: dict) -> dict:
        """Portfolio return analysis (KR/US separated)."""
        from services.market.macro_service import MacroService
        holdings     = [h.model_dump() for h in cls.load_portfolio(user_id)]
        cash         = cls.load_cash(user_id)
        usd_cash     = cls.get_usd_cash_balance()
        exchange_rate = MacroService.get_exchange_rate()

        kr_invested, kr_current, kr_results = cls._calc_kr_holding_results(filter_kr(holdings))
        us_invested_usd, us_current_usd, us_results = cls._calc_us_holding_results(filter_us(holdings), exchange_rate)

        us_invested_krw = us_invested_usd * exchange_rate
        us_current_krw  = us_current_usd * exchange_rate
        total_invested  = kr_invested + us_invested_krw
        total_current   = kr_current + us_current_krw
        all_results     = kr_results + us_results

        return {
            'holdings': all_results,
            'summary': {
                'total_invested': round(total_invested, 2),
                'total_current':  round(total_current, 2),
                'profit':         round(total_current - total_invested, 2),
                'profit_pct':     profit_pct(total_current, total_invested),
            },
            'kr': {
                'invested':   round(kr_invested, 2),
                'current':    round(kr_current, 2),
                'profit':     round(kr_current - kr_invested, 2),
                'profit_pct': profit_pct(kr_current, kr_invested),
                'cash':       round(cash, 2),
                'total':      round(kr_current + cash, 2),
            },
            'us': {
                'invested_usd': round(us_invested_usd, 2),
                'invested_krw': round(us_invested_krw, 2),
                'current_usd':  round(us_current_usd, 2),
                'current_krw':  round(us_current_krw, 2),
                'profit_usd':   round(us_current_usd - us_invested_usd, 2),
                'profit_krw':   round(us_current_krw - us_invested_krw, 2),
                'profit_pct':   profit_pct(us_current_usd, us_invested_usd),
                'cash_usd':     round(usd_cash, 2),
                'cash_krw':     round(usd_cash * exchange_rate, 2),
                'total_krw':    round(us_current_krw + usd_cash * exchange_rate, 2),
            },
            'balances': cls.calculate_balances(all_results, cash, usd_cash, exchange_rate),
        }

    @classmethod
    def calculate_balances(cls, holdings: List[dict], cash: float, usd_cash: float = 0.0, exchange_rate: float = 1350.0) -> dict:
        """Calculate KR/US assets separately."""
        from services.market.macro_service import MacroService
        
        if exchange_rate <= 0:
            exchange_rate = MacroService.get_exchange_rate()
        
        kr_holdings = filter_kr(holdings)
        us_holdings = filter_us(holdings)

        kr_value = sum(h.get('current_price', 0) * h.get('quantity', 0) for h in kr_holdings)
        us_value_usd = sum(h.get('current_price', 0) * h.get('quantity', 0) for h in us_holdings)
        us_value_krw = us_value_usd * exchange_rate
        
        kr_cash = cash
        us_cash_krw = usd_cash * exchange_rate
        
        total_value = kr_value + us_value_krw + kr_cash + us_cash_krw
        
        if total_value == 0:
            return {
                'market': {'KR': 0, 'US': 0, 'Cash_KR': 0, 'Cash_US': 0},
                'kr': {'holdings': 0, 'cash': 0, 'total': 0},
                'us': {'holdings_usd': 0, 'holdings_krw': 0, 'cash_usd': 0, 'cash_krw': 0, 'total_krw': 0},
                'sector': {}
            }
        
        return {
            'market': {
                'KR': round((kr_value / total_value) * 100, 2),
                'US': round((us_value_krw / total_value) * 100, 2),
                'Cash_KR': round((kr_cash / total_value) * 100, 2),
                'Cash_US': round((us_cash_krw / total_value) * 100, 2)
            },
            'kr': {
                'holdings': round(kr_value, 2),
                'cash': round(kr_cash, 2),
                'total': round(kr_value + kr_cash, 2),
                'ratio': round(((kr_value + kr_cash) / total_value) * 100, 2)
            },
            'us': {
                'holdings_usd': round(us_value_usd, 2),
                'holdings_krw': round(us_value_krw, 2),
                'cash_usd': round(usd_cash, 2),
                'cash_krw': round(us_cash_krw, 2),
                'total_krw': round(us_value_krw + us_cash_krw, 2),
                'ratio': round(((us_value_krw + us_cash_krw) / total_value) * 100, 2)
            },
            'sector': {}
        }

    @classmethod
    def build_holding_report_row(cls, holding: dict, cached: dict) -> dict:
        """Build a single holding's analysis report row."""
        cached_price = cached.get("price")
        price = cached_price if (cached_price is not None and cached_price > 0) else holding.get("buy_price", 0)
        buy_price = holding.get("buy_price") or 0
        profit_pct = ((price - buy_price) / buy_price) * 100 if buy_price > 0 else 0
        dcf = cached.get("fair_value_dcf")
        upside = ((dcf - price) / price) * 100 if (dcf and price) else 0
        ticker = holding.get("ticker") or ""
        market = "kr" if (ticker.isdigit() and len(ticker) == 6) else "us"
        quantity = holding.get("quantity") or 0
        current_value = price * quantity if price and quantity else 0
        cost_basis = buy_price * quantity if buy_price and quantity else 0
        profit_loss = current_value - cost_basis
        return {
            "ticker": ticker,
            "name": holding.get("name"),
            "quantity": quantity,
            "buy_price": buy_price,
            "sector": holding.get("sector") or "Others",
            "market": market,
            "price": price,
            "current_value": current_value,
            "profit_loss": profit_loss,
            "change": cached.get("change", 0),
            "change_pct": cached.get("change_pct", 0),
            "pre_price": cached.get("pre_price"),
            "pre_change_pct": cached.get("pre_change_pct"),
            "return_pct": round(profit_pct, 2),
            "rsi": cached.get("rsi"),
            "ema5": cached.get("ema5"),
            "ema10": cached.get("ema10"),
            "ema20": cached.get("ema20"),
            "ema60": cached.get("ema60"),
            "ema120": cached.get("ema120"),
            "ema200": cached.get("ema200"),
            "dcf_fair": dcf,
            "dcf_upside": round(upside, 1) if dcf else None,
        }

    @classmethod
    def apply_buy(cls, holdings: list, ticker: str, quantity: float, price: float) -> list:
        """Buy: calculate average cost if already held, otherwise add new."""
        target = next((h for h in holdings if h.get("ticker") == ticker), None)
        if target:
            total_qty = target["quantity"] + quantity
            avg_price = ((target["quantity"] * target["buy_price"]) + (quantity * price)) / total_qty
            target["quantity"] = total_qty
            target["buy_price"] = avg_price
        else:
            holdings.append({
                "ticker": ticker, "name": ticker,
                "quantity": quantity, "buy_price": price,
                "current_price": price, "sector": "Unknown",
            })
        return holdings

    @classmethod
    def apply_sell(cls, holdings: list, ticker: str, quantity: float) -> list:
        """Sell: deduct quantity, remove from list if zero or below. Raises ValueError if insufficient holdings."""
        target = next((h for h in holdings if h.get("ticker") == ticker), None)
        if not target:
            raise ValueError("Ticker not found in holdings.")
        if target["quantity"] < quantity:
            raise ValueError("Sell quantity exceeds holdings.")
        target["quantity"] -= quantity
        if target["quantity"] <= 0:
            holdings = [h for h in holdings if h.get("ticker") != ticker]
        return holdings

    @classmethod
    def apply_trade_action(
        cls, holdings: list, ticker: str, action: str, quantity: float, price: float
    ) -> list:
        """Validate and execute buy/sell action. Raises ValueError for invalid action."""
        if action.lower() == "buy":
            return cls.apply_buy(holdings, ticker, quantity, price)
        elif action.lower() == "sell":
            return cls.apply_sell(holdings, ticker, quantity)
        raise ValueError(f"Invalid action: {action}. Use 'buy' or 'sell'.")

    @staticmethod
    def _normalize_ticker_for_cache(ticker: str) -> str:
        """KR 티커 zero-padding 정규화 (price_cache 키 매칭용)."""
        t = str(ticker or "").strip()
        return t.zfill(6) if t.isdigit() and len(t) < 6 else t

    @classmethod
    def build_full_report(cls, user_id: str, price_cache: dict) -> list:
        """Return detailed analysis data for all holdings (sorted by return descending)."""
        holdings = cls.load_portfolio(user_id)
        report = [
            cls.build_holding_report_row(
                h.model_dump(),
                price_cache.get(cls._normalize_ticker_for_cache(h.ticker), {})
            )
            for h in holdings if h.ticker
        ]
        report.sort(key=lambda row: row["return_pct"], reverse=True)
        return report

    @classmethod
    def add_holding_manual(
        cls, user_id: str, ticker: str, quantity: float, buy_price: float, name: Optional[str] = None
    ) -> list:
        """Manually add a holding (includes average cost calculation)."""
        holdings_raw = PortfolioRepo.load_holdings(user_id)
        holdings_raw = cls.apply_buy(holdings_raw, ticker, quantity, buy_price)
        if name:
            target = next((h for h in holdings_raw if h.get("ticker") == ticker), None)
            if target and target.get("name") == ticker:
                target["name"] = name
        cls.save_portfolio(user_id, holdings_raw)
        return holdings_raw

    @classmethod
    def update_holding_sector(cls, user_id: str, ticker: str, sector: str) -> list:
        """Manually update a holding's sector."""
        holdings_raw = PortfolioRepo.load_holdings(user_id)
        target = next((h for h in holdings_raw if h.get("ticker") == ticker), None)
        if not target:
            raise ValueError(f"Ticker {ticker} not found.")
        target["sector"] = sector
        cls.save_portfolio(user_id, holdings_raw)
        return holdings_raw

    @classmethod
    def rebalance_portfolio(cls, user_id: str = "sean") -> dict:
        """Sync portfolio with KIS and log sector deviation report."""
        return cls._rebalance_logic(user_id)

    @classmethod
    def _rebalance_logic(cls, user_id: str) -> dict:
        """Sync holdings, calculate current sector weights, and log any deviations."""
        logger.info(f"⚖️ Starting portfolio rebalance check for {user_id}")
        holdings = cls.sync_with_kis(user_id)
        if not holdings:
            logger.warning("⚠️ No holdings to rebalance.")
            return {}

        total_value = sum((h.current_price or 0) * (h.quantity or 0) for h in holdings)
        if total_value <= 0:
            logger.warning("⚠️ Total portfolio value is zero — skipping rebalance.")
            return {}

        sector_values: dict[str, float] = {}
        for h in holdings:
            sector = h.sector or DEFAULT_SECTOR
            sector_values[sector] = sector_values.get(sector, 0.0) + (h.current_price or 0) * (h.quantity or 0)

        sector_weights = {s: round(v / total_value * 100, 2) for s, v in sector_values.items()}
        logger.info(f"📊 Sector weights: {sector_weights} | Total: {total_value:,.0f} KRW | Holdings: {len(holdings)}")

        return {
            "sector_weights": sector_weights,
            "total_value": total_value,
            "holdings_count": len(holdings),
        }
