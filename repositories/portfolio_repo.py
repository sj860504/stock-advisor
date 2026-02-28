"""포트폴리오 및 보유 종목 Repository."""
from models.portfolio import Portfolio, PortfolioHolding
from repositories.database import get_session, session_scope, session_ro
from utils.logger import get_logger

logger = get_logger("portfolio_repo")


class PortfolioRepo:
    """Portfolio / PortfolioHolding 테이블 CRUD."""

    @classmethod
    def save(cls, user_id: str, holding_dicts: list, cash_balance: float = None) -> bool:
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
                for h in holding_dicts:
                    session.add(PortfolioHolding(
                        portfolio_id=portfolio.id,
                        ticker=h.get("ticker"),
                        name=h.get("name"),
                        quantity=h.get("quantity", 0),
                        buy_price=h.get("buy_price", 0.0),
                        current_price=h.get("current_price", 0.0),
                        sector=h.get("sector"),
                    ))
            return True
        except Exception as e:
            logger.error(f"Error saving portfolio for {user_id}: {e}")
            return False

    @classmethod
    def load_holdings(cls, user_id: str) -> list:
        """보유 종목 dict 리스트 반환."""
        with session_ro() as session:
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

    @classmethod
    def load_cash(cls, user_id: str) -> float:
        """현금 잔고 조회."""
        with session_ro() as session:
            portfolio = session.query(Portfolio).filter_by(user_id=user_id).first()
            return portfolio.cash_balance if portfolio else 0.0
