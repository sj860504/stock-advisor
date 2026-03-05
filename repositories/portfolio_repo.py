"""포트폴리오 및 보유 종목 Repository."""
from typing import Any

from models.portfolio import Portfolio, PortfolioHolding
from repositories.database import get_session, session_scope, session_ro
from utils.logger import get_logger

logger = get_logger("portfolio_repo")


class PortfolioRepo:
    """Portfolio / PortfolioHolding 테이블 CRUD."""

    @classmethod
    def save_portfolio_holdings(cls, user_id: str, holding_dicts: list[dict[str, Any]], cash_balance: float | None = None) -> bool:
        """포트폴리오 전체 저장 (기존 holdings 삭제 후 재삽입)."""
        try:
            with session_scope() as session:
                portfolio = session.query(Portfolio).filter_by(user_id=user_id).first()
                if not portfolio:
                    portfolio = Portfolio(user_id=user_id)
                    session.add(portfolio)
                    session.flush()  # portfolio.id 확보

                if cash_balance is not None:
                    portfolio.cash_balance = cash_balance

                session.query(PortfolioHolding).filter_by(portfolio_id=portfolio.id).delete()
                for holding_dict in holding_dicts:
                    session.add(PortfolioHolding(
                        portfolio_id=portfolio.id,
                        ticker=holding_dict.get("ticker"),
                        name=holding_dict.get("name"),
                        quantity=holding_dict.get("quantity", 0),
                        buy_price=holding_dict.get("buy_price", 0.0),
                        current_price=holding_dict.get("current_price", 0.0),
                        sector=holding_dict.get("sector"),
                    ))
            return True
        except Exception as e:
            logger.error(f"Error saving portfolio for {user_id}: {e}")
            return False

    @classmethod
    def load_holdings(cls, user_id: str) -> list[dict[str, Any]]:
        """보유 종목 dict 리스트 반환."""
        with session_ro() as session:
            portfolio = session.query(Portfolio).filter_by(user_id=user_id).first()
            if not portfolio:
                return []
            return [
                {
                    "ticker": holding.ticker,
                    "name": holding.name,
                    "quantity": holding.quantity,
                    "buy_price": holding.buy_price,
                    "current_price": holding.current_price,
                    "sector": holding.sector,
                }
                for holding in portfolio.holdings
            ]

    @classmethod
    def load_cash(cls, user_id: str) -> float:
        """현금 잔고 조회."""
        with session_ro() as session:
            portfolio = session.query(Portfolio).filter_by(user_id=user_id).first()
            return portfolio.cash_balance if portfolio else 0.0