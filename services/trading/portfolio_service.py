"""포트폴리오 관리 서비스 (DB + KIS 동기화)."""
import json
import os
from datetime import datetime
from typing import Dict, List, Optional

from models.portfolio import Portfolio, PortfolioHolding
from models.schemas import PortfolioHoldingDto, HoldingSchema, PortfolioSchema
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
    """포트폴리오 및 보유 종목 관리 (DB 저장, KIS 잔고 동기화)."""
    _last_balance_summary: dict = {}

    @staticmethod
    def _extract_float(data: dict, *keys) -> float:
        """딕셔너리에서 첫 번째 유효한 float 값을 반환합니다. 음수 허용, "0" 문자열 truthy 버그 방지."""
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
        """HoldingSchema 또는 dict에서 공통 필드를 추출합니다."""
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
        """포트폴리오 및 보유 종목 정보를 DB에 저장합니다. HoldingSchema 또는 dict 모두 허용합니다."""
        session = StockMetaService.get_session()
        try:
            portfolio = session.query(Portfolio).filter_by(user_id=user_id).first()
            if not portfolio:
                portfolio = Portfolio(user_id=user_id)
                session.add(portfolio)
            if cash_balance is not None:
                portfolio.cash_balance = cash_balance

            session.query(PortfolioHolding).filter_by(portfolio_id=portfolio.id).delete()
            for holding_input in holdings:
                ticker, name, quantity, buy_price, current_price, sector = cls._extract_holding_fields(holding_input)
                holding_entity = PortfolioHolding(
                    portfolio_id=portfolio.id,
                    ticker=ticker,
                    name=name,
                    quantity=quantity,
                    buy_price=buy_price,
                    current_price=current_price,
                    sector=sector,
                )
                session.add(holding_entity)
            session.commit()
            return True
        except Exception as e:
            session.rollback()
            logger.error(f"Error saving portfolio for {user_id}: {e}")
            return False
        finally:
            session.close()

    @classmethod
    def load_portfolio(cls, user_id: str) -> List[dict]:
        """DB에서 포트폴리오 보유 종목을 조회해 dict 리스트로 반환합니다."""
        session = StockMetaService.get_session()
        try:
            portfolio = session.query(Portfolio).filter_by(user_id=user_id).first()
            if not portfolio:
                return []
            return [
                {
                    "ticker": h.ticker,
                    "name": h.name,
                    "quantity": h.quantity,
                    "buy_price": h.buy_price,
                    "current_price": h.current_price,
                    "sector": h.sector,
                }
                for h in portfolio.holdings
            ]
        finally:
            session.close()

    @classmethod
    def load_portfolio_dtos(cls, user_id: str) -> List[PortfolioHoldingDto]:
        """DB에서 포트폴리오 보유 종목을 조회해 DTO 리스트로 반환합니다."""
        return [PortfolioHoldingDto(**h) for h in cls.load_portfolio(user_id)]

    @classmethod
    def load_cash(cls, user_id: str) -> float:
        """DB에서 현금 잔고 조회"""
        session = StockMetaService.get_session()
        try:
            portfolio = session.query(Portfolio).filter_by(user_id=user_id).first()
            return portfolio.cash_balance if portfolio else 0.0
        finally:
            session.close()

    @classmethod
    def sync_with_kis(cls, user_id: str = "sean") -> List[HoldingSchema]:
        """KIS 실제 잔고와 동기화 (DB 업데이트 포함)"""
        logger.info(f"🔄 Syncing portfolio with KIS for user: {user_id}")
        balance_data = KisService.get_balance()
        if not balance_data:
            return cls.load_portfolio(user_id) # 실패 시 로컬(DB) 데이터 반환
        overseas_balance = KisService.get_overseas_balance()

        existing_holdings = cls.load_portfolio(user_id)  # List[dict]
        existing_us_map = {
            h["ticker"]: HoldingSchema(**h)
            for h in existing_holdings
            if str(h.get("ticker", "")).isalpha()
        }

        holdings: List[HoldingSchema] = []
        us_holdings_by_ticker: Dict[str, HoldingSchema] = {}
        for item in balance_data.get("holdings", []):
            ticker = str(item.get('pdno') or item.get('symb') or "").strip().upper()
            if not ticker:
                continue

            qty = int(float(item.get('hldg_qty') or item.get('ovrs_cblc_qty') or item.get('ord_psbl_qty') or 0))
            if qty <= 0:
                continue

            is_kr_ticker = is_kr(ticker)
            parsed = HoldingSchema(
                ticker=ticker,
                name=item.get('prdt_name') or item.get('ovrs_item_name') or item.get('hldg_pdno_name') or ticker,
                quantity=qty,
                buy_price=float(item.get('pchs_avg_pric') or item.get('pavg_unit_amt') or 0),
                current_price=float(item.get('prpr') or item.get('ovrs_now_pric') or item.get('now_pric') or 0),
                sector=DEFAULT_SECTOR,
            )
            if is_kr_ticker:
                holdings.append(parsed)
            else:
                us_holdings_by_ticker[ticker] = parsed

        # 해외 잔고 조회가 가능하면 미국 보유를 최신값으로 덮어씀
        if overseas_balance and overseas_balance.get("holdings"):
            for item in overseas_balance.get("holdings", []):
                ticker = str(
                    item.get("ovrs_pdno") or item.get("pdno") or item.get("symb") or item.get("ovrs_item_cd") or ""
                ).strip().upper()
                if not ticker or is_kr(ticker):
                    continue
                qty = int(float(item.get("ovrs_cblc_qty") or item.get("hldg_qty") or item.get("ord_psbl_qty") or 0))
                if qty <= 0:
                    continue
                current_price = float(
                    item.get("now_pric2") or 
                    item.get("ovrs_now_pric") or 
                    item.get("prpr") or 
                    item.get("now_pric") or 0
                )
                us_holdings_by_ticker[ticker] = HoldingSchema(
                    ticker=ticker,
                    name=item.get("ovrs_item_name") or item.get("prdt_name") or ticker,
                    quantity=qty,
                    buy_price=float(item.get("pchs_avg_pric") or item.get("avg_unpr") or item.get("pavg_unit_amt") or 0),
                    current_price=current_price,
                    sector=DEFAULT_SECTOR,
                )

        if us_holdings_by_ticker:
            holdings.extend(us_holdings_by_ticker.values())
        else:
            holdings.extend(existing_us_map.values())

        summary_items = balance_data.get("summary", [])
        if len(summary_items) > 1:
            def _summary_sort_key(row):
                try:
                    return float(row.get("tot_evlu_amt") or row.get("dnca_tot_amt") or 0)
                except (TypeError, ValueError):
                    return 0
            summary = max(summary_items, key=_summary_sort_key)
        else:
            summary = summary_items[0] if summary_items else {}
        usd_cash = cls.get_usd_cash_balance()
        summary["_usd_cash_balance"] = usd_cash
        cls._last_balance_summary = summary
        # dnca_tot_amt=예수금(당일 미결제 매수 차감, D+2 매도금 미반영으로 음수 가능)
        # prvs_rcdl_excc_amt=실제 주문가능금액(D+2 결제 대기 매도금 포함)
        # 두 값 중 큰 값이 실제 매수가능금액에 가장 가까움.
        dnca = cls._extract_float(summary, "dnca_tot_amt")
        prvs = cls._extract_float(summary, "prvs_rcdl_excc_amt")
        cash = max(0.0, dnca, prvs)

        cls.save_portfolio(user_id, holdings, cash_balance=cash)

        # 인-메모리 state 가격 동기화 (VTS WebSocket이 해외 실시간 미지원 대응)
        from services.market.market_data_service import MarketDataService
        for h in holdings:
            price = float(h.current_price or 0)
            if price > 0:
                MarketDataService.update_price_from_sync(h.ticker, price)

        return [h.model_dump() for h in holdings]

    @classmethod
    def get_last_balance_summary(cls) -> dict:
        return cls._last_balance_summary or {}

    @classmethod
    def get_usd_cash_balance(cls) -> float:
        """미국 외화 현금(USD) 조회: KIS 매수가능금액 API 또는 설정값"""
        available_usd = KisService.get_overseas_available_cash()
        if available_usd is not None and available_usd > 0:
            return available_usd
        
        return SettingsService.get_float("PORTFOLIO_USD_CASH_BALANCE", 0.0)

    @classmethod
    def analyze_portfolio(cls, user_id: str, price_cache: dict) -> dict:
        """포트폴리오 수익률 분석 (한국/미국 분리)"""
        from services.market.macro_service import MacroService
        
        holdings = cls.load_portfolio(user_id)  # List[dict]
        cash = cls.load_cash(user_id)
        usd_cash = cls.get_usd_cash_balance()
        exchange_rate = MacroService.get_exchange_rate()

        kr_holdings = filter_kr(holdings)
        us_holdings = filter_us(holdings)

        results = []
        kr_invested = 0
        kr_current = 0
        us_invested_usd = 0
        us_current_usd = 0

        for h in kr_holdings:
            val = h.get("quantity", 0) * (h.get("current_price") or 0.0)
            inv = h.get("quantity", 0) * h.get("buy_price", 0)
            kr_invested += inv
            kr_current += val

            results.append({
                **h,
                'profit': round(val - inv, 2),
                'profit_pct': profit_pct(val, inv),
                'market': 'KR'
            })

        for h in us_holdings:
            val_usd = h.get("quantity", 0) * (h.get("current_price") or 0.0)
            inv_usd = h.get("quantity", 0) * h.get("buy_price", 0)
            us_invested_usd += inv_usd
            us_current_usd += val_usd

            results.append({
                **h,
                'profit_usd': round(val_usd - inv_usd, 2),
                'profit_krw': round((val_usd - inv_usd) * exchange_rate, 2),
                'profit_pct': profit_pct(val_usd, inv_usd),
                'market': 'US'
            })
        
        us_invested_krw = us_invested_usd * exchange_rate
        us_current_krw = us_current_usd * exchange_rate
        total_invested = kr_invested + us_invested_krw
        total_current = kr_current + us_current_krw
        
        return {
            'holdings': results,
            'summary': {
                'total_invested': round(total_invested, 2),
                'total_current': round(total_current, 2),
                'profit': round(total_current - total_invested, 2),
                'profit_pct': profit_pct(total_current, total_invested)
            },
            'kr': {
                'invested': round(kr_invested, 2),
                'current': round(kr_current, 2),
                'profit': round(kr_current - kr_invested, 2),
                'profit_pct': profit_pct(kr_current, kr_invested),
                'cash': round(cash, 2),
                'total': round(kr_current + cash, 2)
            },
            'us': {
                'invested_usd': round(us_invested_usd, 2),
                'invested_krw': round(us_invested_krw, 2),
                'current_usd': round(us_current_usd, 2),
                'current_krw': round(us_current_krw, 2),
                'profit_usd': round(us_current_usd - us_invested_usd, 2),
                'profit_krw': round(us_current_krw - us_invested_krw, 2),
                'profit_pct': profit_pct(us_current_usd, us_invested_usd),
                'cash_usd': round(usd_cash, 2),
                'cash_krw': round(usd_cash * exchange_rate, 2),
                'total_krw': round(us_current_krw + usd_cash * exchange_rate, 2)
            },
            'balances': cls.calculate_balances(results, cash, usd_cash, exchange_rate)
        }

    @classmethod
    def calculate_balances(cls, holdings: List[dict], cash: float, usd_cash: float = 0.0, exchange_rate: float = 1350.0) -> dict:
        """한국/미국 자산을 분리해서 계산"""
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
        """보유 종목 한 건의 분석 리포트 row를 생성합니다."""
        price = cached.get("price") or holding.get("buy_price")
        buy_price = holding.get("buy_price") or 0
        profit_pct = ((price - buy_price) / buy_price) * 100 if buy_price > 0 else 0
        dcf = cached.get("fair_value_dcf")
        upside = ((dcf - price) / price) * 100 if (dcf and price) else 0
        return {
            "ticker": holding.get("ticker"),
            "name": holding.get("name"),
            "price": price,
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
        """매수: 기존 보유 시 평단가 계산, 없으면 신규 추가."""
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
        """매도: 수량 차감 후 0 이하이면 목록에서 제거. 잔고 부족 시 ValueError."""
        target = next((h for h in holdings if h.get("ticker") == ticker), None)
        if not target:
            raise ValueError("보유하지 않은 종목입니다.")
        if target["quantity"] < quantity:
            raise ValueError("매도 수량이 보유 수량보다 많습니다.")
        target["quantity"] -= quantity
        if target["quantity"] <= 0:
            holdings = [h for h in holdings if h.get("ticker") != ticker]
        return holdings

    @classmethod
    def apply_trade_action(
        cls, holdings: list, ticker: str, action: str, quantity: float, price: float
    ) -> list:
        """매수/매도 액션을 검증하고 실행합니다. 잘못된 action이면 ValueError."""
        if action.lower() == "buy":
            return cls.apply_buy(holdings, ticker, quantity, price)
        elif action.lower() == "sell":
            return cls.apply_sell(holdings, ticker, quantity)
        raise ValueError(f"Invalid action: {action}. Use 'buy' or 'sell'.")

    @classmethod
    def build_full_report(cls, user_id: str, price_cache: dict) -> list:
        """보유 종목 전체 상세 분석 데이터 반환 (수익률 내림차순)."""
        holdings = cls.load_portfolio(user_id)
        report = [
            cls.build_holding_report_row(h, price_cache.get(h.get("ticker"), {}))
            for h in holdings if h.get("ticker")
        ]
        report.sort(key=lambda row: row["return_pct"], reverse=True)
        return report

    @classmethod
    def add_holding_manual(
        cls, user_id: str, ticker: str, quantity: float, buy_price: float, name: Optional[str] = None
    ) -> list:
        """수동으로 보유 종목을 추가합니다 (평단가 계산 포함)."""
        holdings = cls.load_portfolio(user_id)
        holdings = cls.apply_buy(holdings, ticker, quantity, buy_price)
        if name:
            target = next((h for h in holdings if h.get("ticker") == ticker), None)
            if target and target.get("name") == ticker:
                target["name"] = name
        cls.save_portfolio(user_id, holdings)
        return holdings

    @classmethod
    def rebalance_portfolio(cls, user_id: str = "sean"):
        # 기존 로직과 동일하되 sync_with_kis가 DB를 업데이트하므로 이를 활용
        return cls._rebalance_logic(user_id)

    @classmethod
    def _rebalance_logic(cls, user_id: str):
        # (기존 rebalance_portfolio 내부 로직 추출 및 유지)
        pass # 실제 구현 시 위 analyze 및 sync 결과 바탕으로 수행
