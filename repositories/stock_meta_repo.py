"""종목 메타, 재무, API TR, DCF 오버라이드, 시장 국면 Repository."""
import json
from datetime import datetime
from typing import Optional

from sqlalchemy import func, text

from models.stock_meta import (
    StockMeta,
    Financials,
    ApiTrMeta,
    DcfOverride,
    MarketRegimeHistory,
)
from repositories.database import session_scope, session_ro
from utils.logger import get_logger

logger = get_logger("stock_meta_repo")


class StockMetaRepo:
    """StockMeta 및 관련 테이블 CRUD."""

    # ── StockMeta ─────────────────────────────────────────────────────────

    @classmethod
    def upsert_stock_meta(cls, ticker: str, **kwargs) -> Optional[StockMeta]:
        """종목 메타 정보 저장 또는 업데이트."""
        try:
            with session_scope() as session:
                stock = session.query(StockMeta).filter_by(ticker=ticker).first()
                if not stock:
                    stock = StockMeta(ticker=ticker)
                    session.add(stock)
                for key, value in kwargs.items():
                    if hasattr(stock, key) and value is not None:
                        setattr(stock, key, value)
                session.flush()
                session.refresh(stock)
                session.expunge(stock)
                return stock
        except Exception as e:
            logger.error(f"Error upserting stock meta for {ticker}: {e}")
            return None

    @classmethod
    def get_stock_meta(cls, ticker: str) -> Optional[StockMeta]:
        """종목 메타 정보 조회."""
        with session_ro() as session:
            result = session.query(StockMeta).filter_by(ticker=ticker).first()
            if result:
                session.expunge(result)
            return result

    @classmethod
    def get_stock_meta_bulk(cls, tickers: list) -> list:
        """여러 종목 메타 정보 일괄 조회."""
        if not tickers:
            return []
        with session_ro() as session:
            results = session.query(StockMeta).filter(StockMeta.ticker.in_(tickers)).all()
            for r in results:
                session.expunge(r)
            return results

    @classmethod
    def find_ticker_by_name(cls, name: str) -> Optional[str]:
        """종목명으로 티커 조회 (name_ko 또는 name_en 대소문자 무시 검색)."""
        if not name:
            return None
        with session_ro() as session:
            result = session.query(StockMeta).filter(
                (func.lower(StockMeta.name_ko) == name.lower()) |
                (func.lower(StockMeta.name_en) == name.lower())
            ).first()
            return result.ticker if result else None

    @classmethod
    def _is_valid_kr_ticker(cls, ticker: str, name: str, existing: set) -> bool:
        """KR 개별 종목 유효성 검사 (6자리 숫자, 미수집, 펀드성 아님)."""
        if not (ticker.isdigit() and len(ticker) == 6):
            return False
        if ticker in existing:
            return False
        if cls._is_kr_fund_like(ticker, name):
            return False
        return True

    @classmethod
    def get_kr_individual_stocks(cls, existing: set, limit: int) -> list:
        """KR 시장 개별 종목 티커 목록 조회 (ETF/펀드성 제외, 6자리 숫자 필터).

        Args:
            existing: 이미 수집된 티커 집합 (중복 제외용).
            limit: 최대 반환 개수.

        Returns:
            필터링된 KR 티커 문자열 리스트.
        """
        result: list[str] = []
        try:
            with session_ro() as session:
                rows = (
                    session.query(StockMeta)
                    .filter(StockMeta.market_type == "KR")
                    .all()
                )
                for row in rows:
                    meta_ticker = str(getattr(row, "ticker", "") or "").strip()
                    name_ko = str(getattr(row, "name_ko", "") or "").strip()
                    if not cls._is_valid_kr_ticker(meta_ticker, name_ko, existing):
                        continue
                    existing.add(meta_ticker)
                    result.append(meta_ticker)
                    if len(result) >= limit:
                        break
        except Exception as e:
            logger.error(f"get_kr_individual_stocks error: {e}")
        return result

    @classmethod
    def _is_kr_fund_like(cls, ticker: str, name: str) -> bool:
        """KR 종목에 대해 ETF/ETN/펀드성 상품 여부를 판별하는 내부 헬퍼."""
        n = str(name or "").strip().upper()
        kr_keywords = [
            "ETF", "ETN", "인버스", "레버리지", "TRF", "TDF",
            "KODEX", "TIGER", "KINDEX", "KBSTAR", "ARIRANG",
            "KOSEF", "HANARO", "SOL", "ACE", "RISE",
        ]
        for kw in kr_keywords:
            if kw in n:
                return True
        return False

    # ── Financials ────────────────────────────────────────────────────────

    @classmethod
    def _get_or_create_stock(cls, session, ticker: str) -> Optional[StockMeta]:
        """세션 내 StockMeta 조회, 없으면 기본 메타 생성 후 반환."""
        stock = session.query(StockMeta).filter_by(ticker=ticker).first()
        if not stock:
            logger.warning(
                f"Stock meta not found for {ticker}. Creating basic meta first."
            )
            # 순환참조 방지를 위해 직접 upsert
            from utils.market import is_kr as _is_kr
            stock = StockMeta(ticker=ticker, market_type="KR" if _is_kr(ticker) else "US")
            session.add(stock)
            session.flush()
        return stock

    @classmethod
    def _apply_metrics_to_financial(cls, financial, metrics: dict) -> None:
        """metrics dict의 값을 Financials 레코드 필드에 반영하는 내부 헬퍼."""
        metric_to_db_field = {
            "name": "name",
            "per": "per", "pbr": "pbr", "roe": "roe",
            "eps": "eps", "bps": "bps",
            "dividend_yield": "dividend_yield",
            "current_price": "current_price",
            "market_cap": "market_cap",
            "high52": "high52", "low52": "low52",
            "volume": "volume", "amount": "amount",
            "rsi": "rsi", "dcf_value": "dcf_value",
        }
        for metric_key, db_field in metric_to_db_field.items():
            if metric_key in metrics:
                setattr(financial, db_field, metrics[metric_key])
        if "ema" in metrics and isinstance(metrics["ema"], dict):
            for span, val in metrics["ema"].items():
                field_name = f"ema{span}"
                if hasattr(financial, field_name):
                    setattr(financial, field_name, val)

    @classmethod
    def _get_or_create_financial(cls, session, stock_id: int, base_date: datetime) -> Financials:
        """stock_id + base_date 기준으로 Financials 레코드 조회 또는 신규 생성."""
        existing = (
            session.query(Financials)
            .filter_by(stock_id=stock_id, base_date=base_date)
            .first()
        )
        if existing:
            return existing
        financial = Financials(stock_id=stock_id, base_date=base_date)
        session.add(financial)
        return financial

    @classmethod
    def save_financials(cls, ticker: str, metrics: dict, base_date: datetime = None) -> Optional[Financials]:
        """재무 지표 저장 (해당 날짜 기준 upsert)."""
        if not metrics:
            return None
        try:
            with session_scope() as session:
                stock = cls._get_or_create_stock(session, ticker)
                if not stock:
                    return None
                if base_date is None:
                    base_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
                financial = cls._get_or_create_financial(session, stock.id, base_date)
                cls._apply_metrics_to_financial(financial, metrics)
                financial.updated_at = datetime.now()
                session.flush()
                session.expunge(financial)
                return financial
        except Exception as e:
            logger.error(f"Error saving financials for {ticker}: {e}")
            return None

    @classmethod
    def get_latest_financials(cls, ticker: str) -> Optional[Financials]:
        """가장 최근 재무 지표 조회."""
        with session_ro() as session:
            stock = session.query(StockMeta).filter_by(ticker=ticker).first()
            if not stock:
                return None
            result = (
                session.query(Financials)
                .filter(Financials.stock_id == stock.id)
                .order_by(Financials.base_date.desc())
                .first()
            )
            if result:
                session.expunge(result)
            return result

    @classmethod
    def _map_dcf_row_to_dict(cls, r) -> dict:
        """DCF 조회 행(row)을 결과 dict로 변환하는 내부 헬퍼."""
        effective_dcf = float(r.override_fair_value) if r.override_fair_value else (
            float(r.dcf_value) if r.dcf_value else None
        )
        price = float(r.current_price) if r.current_price else None
        upside = (
            round((effective_dcf - price) / price * 100, 2)
            if (effective_dcf and price and price > 0)
            else None
        )
        return {
            "ticker": r.ticker,
            "name": r.name_ko,
            "market_type": r.market_type,
            "current_price": price,
            "dcf_value": effective_dcf,
            "upside_pct": upside,
            "is_override": (
                r.override_fair_value is not None
                or r.override_fcf_per_share is not None
            ),
            "base_date": str(r.base_date)[:10] if r.base_date else None,
        }

    @classmethod
    def get_all_latest_dcf(cls, limit: int = 1000) -> list:
        """전 종목 최신 DCF 값 및 관련 지표 일괄 조회 (dcf_overrides 병합 포함)."""
        try:
            with session_ro() as session:
                rows = session.execute(text("""
                    SELECT
                        sm.ticker,
                        sm.name_ko,
                        sm.market_type,
                        f.current_price,
                        f.dcf_value,
                        f.base_date,
                        ov.fair_value     AS override_fair_value,
                        ov.fcf_per_share  AS override_fcf_per_share
                    FROM financials f
                    JOIN stock_meta sm ON f.stock_id = sm.id
                    LEFT JOIN dcf_overrides ov ON ov.ticker = sm.ticker
                    WHERE f.base_date = (
                        SELECT MAX(f2.base_date)
                        FROM financials f2
                        WHERE f2.stock_id = f.stock_id
                    )
                    ORDER BY sm.market_type, sm.ticker
                    LIMIT :limit
                """), {"limit": limit}).fetchall()
                return [cls._map_dcf_row_to_dict(r) for r in rows]
        except Exception as e:
            logger.error(f"get_all_latest_dcf error: {e}")
            return []

    @classmethod
    def get_financials_history(cls, ticker: str, limit: int = 2500) -> list:
        """종목 재무 지표 이력 조회 (최신순)."""
        with session_ro() as session:
            stock = session.query(StockMeta).filter_by(ticker=ticker).first()
            if not stock:
                return []
            results = (
                session.query(Financials)
                .filter(Financials.stock_id == stock.id)
                .order_by(Financials.base_date.desc())
                .limit(limit)
                .all()
            )
            for record in results:
                session.expunge(record)
            return results

    @classmethod
    def get_batch_latest_financials(cls, tickers: list) -> dict:
        """여러 종목의 최신 재무 지표를 일괄 조회."""
        if not tickers:
            return {}
        with session_ro() as session:
            subquery = session.query(
                Financials.stock_id,
                func.max(Financials.base_date).label("max_date"),
            ).group_by(Financials.stock_id).subquery()

            results = (
                session.query(StockMeta.ticker, Financials)
                .join(Financials, StockMeta.id == Financials.stock_id)
                .join(
                    subquery,
                    (Financials.stock_id == subquery.c.stock_id)
                    & (Financials.base_date == subquery.c.max_date),
                )
                .filter(StockMeta.ticker.in_(tickers))
                .all()
            )

            result_dict = {}
            for ticker, financial in results:
                session.expunge(financial)
                result_dict[ticker] = financial
            return result_dict

    # ── ApiTrMeta ─────────────────────────────────────────────────────────

    @classmethod
    def upsert_api_tr_meta(cls, api_name: str, **kwargs) -> Optional[ApiTrMeta]:
        """API별 TR ID 정보 저장."""
        try:
            with session_scope() as session:
                meta = session.query(ApiTrMeta).filter_by(api_name=api_name).first()
                if not meta:
                    meta = ApiTrMeta(api_name=api_name)
                    session.add(meta)
                for key, value in kwargs.items():
                    if hasattr(meta, key):
                        setattr(meta, key, value)
                session.flush()
                session.expunge(meta)
                return meta
        except Exception as e:
            logger.error(f"Error upserting api tr meta for {api_name}: {e}")
            return None

    @classmethod
    def get_api_meta(cls, api_name: str) -> Optional[ApiTrMeta]:
        """API명으로 메타 정보 전체 조회."""
        with session_ro() as session:
            result = session.query(ApiTrMeta).filter_by(api_name=api_name).first()
            if result:
                session.expunge(result)
            return result

    # ── DcfOverride ───────────────────────────────────────────────────────

    @classmethod
    def upsert_dcf_override(
        cls,
        ticker: str,
        fcf_per_share: float = None,
        beta: float = None,
        growth_rate: float = None,
        fair_value: float = None,
    ) -> Optional[DcfOverride]:
        """사용자 지정 DCF 입력값 저장/업데이트."""
        try:
            with session_scope() as session:
                row = session.query(DcfOverride).filter_by(ticker=ticker).first()
                if not row:
                    row = DcfOverride(ticker=ticker)
                    session.add(row)
                if fcf_per_share is not None:
                    row.fcf_per_share = fcf_per_share
                if beta is not None:
                    row.beta = beta
                if growth_rate is not None:
                    row.growth_rate = growth_rate
                if fair_value is not None:
                    row.fair_value = fair_value
                row.updated_at = datetime.now()
                session.flush()
                session.refresh(row)
                session.expunge(row)
                return row
        except Exception as e:
            logger.error(f"Error upserting DCF override for {ticker}: {e}")
            return None

    @classmethod
    def get_dcf_override(cls, ticker: str) -> Optional[DcfOverride]:
        """사용자 지정 DCF 입력값 조회."""
        with session_ro() as session:
            result = session.query(DcfOverride).filter_by(ticker=ticker).first()
            if result:
                session.expunge(result)
            return result

    @classmethod
    def get_all_dcf_overrides(cls, limit: int = 1000) -> dict:
        """DcfOverride 전체 조회.

        Returns:
            {ticker: {"fcf_per_share": ..., "beta": ..., "growth_rate": ..., "updated_at": ...}}
        """
        try:
            with session_ro() as session:
                records = session.query(DcfOverride).limit(limit).all()
                return {
                    record.ticker: {
                        "fcf_per_share": record.fcf_per_share,
                        "beta": record.beta,
                        "growth_rate": record.growth_rate,
                        "updated_at": (
                            record.updated_at.isoformat() if record.updated_at else None
                        ),
                    }
                    for record in records
                }
        except Exception:
            return {}

    # ── MarketRegimeHistory ───────────────────────────────────────────────

    @classmethod
    def _apply_regime_fields(
        cls, record, regime_data: dict, vix: float, fear_greed: int
    ) -> None:
        """MarketRegimeHistory 레코드에 모든 국면 필드를 세팅하는 내부 헬퍼."""
        record.status = regime_data.get("status")
        record.regime_score = regime_data.get("regime_score")
        record.vix = vix
        record.fear_greed = fear_greed
        record.us_10y_yield = (
            ((regime_data.get("components") or {}).get("other_detail") or {}).get(
                "us_10y_yield"
            )
        )
        record.spx_price = regime_data.get("current")
        record.spx_ma200 = regime_data.get("ma200")
        record.spx_diff_pct = regime_data.get("diff_pct")
        record.components_json = json.dumps(
            regime_data.get("components", {}), ensure_ascii=False
        )

    @classmethod
    def save_market_regime(
        cls,
        date_str: str,
        regime_data: dict,
        vix: float,
        fear_greed: int,
    ) -> bool:
        """일별 시장 국면 스냅샷을 DB에 저장 (이미 있으면 업데이트)."""
        try:
            with session_scope() as session:
                record = (
                    session.query(MarketRegimeHistory).filter_by(date=date_str).first()
                )
                if not record:
                    record = MarketRegimeHistory(date=date_str)
                    session.add(record)
                cls._apply_regime_fields(record, regime_data, vix, fear_greed)
            return True
        except Exception as e:
            logger.error(f"save_market_regime error: {e}")
            return False

    @classmethod
    def get_market_regime_history(cls, days: int = 30) -> list:
        """최근 N일 레짐 이력 반환 (최신순)."""
        try:
            with session_ro() as session:
                records = (
                    session.query(MarketRegimeHistory)
                    .order_by(MarketRegimeHistory.date.desc())
                    .limit(days)
                    .all()
                )
                return [
                    {
                        "date": r.date,
                        "status": r.status,
                        "regime_score": r.regime_score,
                        "vix": r.vix,
                        "fear_greed": r.fear_greed,
                        "us_10y_yield": r.us_10y_yield,
                        "spx_price": r.spx_price,
                        "spx_ma200": r.spx_ma200,
                        "spx_diff_pct": r.spx_diff_pct,
                        "components": json.loads(r.components_json or "{}"),
                    }
                    for r in records
                ]
        except Exception as e:
            logger.error(f"get_market_regime_history error: {e}")
            return []

    @classmethod
    def get_regime_for_date(cls, date_str: str) -> Optional[dict]:
        """특정 날짜 레짐 반환."""
        try:
            with session_ro() as session:
                r = (
                    session.query(MarketRegimeHistory)
                    .filter_by(date=date_str)
                    .first()
                )
                if not r:
                    return None
                return {
                    "date": r.date,
                    "status": r.status,
                    "regime_score": r.regime_score,
                    "vix": r.vix,
                    "fear_greed": r.fear_greed,
                    "us_10y_yield": r.us_10y_yield,
                    "spx_price": r.spx_price,
                    "spx_ma200": r.spx_ma200,
                    "spx_diff_pct": r.spx_diff_pct,
                    "components": json.loads(r.components_json or "{}"),
                }
        except Exception as e:
            logger.error(f"get_regime_for_date error: {e}")
            return None
