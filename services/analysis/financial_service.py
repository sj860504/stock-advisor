import os
import time
from datetime import datetime
from typing import Optional
import pandas as pd
import math
from config import Config
from utils.logger import get_logger
from utils.market import is_kr
from services.kis.kis_service import KisService
from services.analysis.analyzer.financial_analyzer import FinancialAnalyzer
from services.market.stock_meta_service import StockMetaService
from services.analysis.yfinance_service import YFinanceService
from models.schemas import (
    KisFinancialsMeta,
    KisFinancialsResponse,
    AnalyzedFinancialMetrics,
    DcfInputData,
)

logger = get_logger("financial_service")

class FinancialService:
    """
    종목별 재무 지표 및 DCF 데이터 제공 서비스
    - KisService를 통해 원시 데이터를 가져오고 FinancialAnalyzer를 통해 가공합니다.
    """
    _recent_metrics_by_ticker = {}
    _dcf_input_by_ticker = {}
    _dcf_overrides_file_path = os.path.join(os.path.dirname(__file__), "..", "data", "dcf_settings.json")

    @classmethod
    def get_metrics(cls, ticker: str) -> Optional[AnalyzedFinancialMetrics]:
        """종목별 핵심 재무 지표 반환 (KIS API 기반). DB·메모리 캐시 우선, 없으면 KIS 조회 후 분석·저장."""
        if ticker in cls._recent_metrics_by_ticker:
            cached = cls._recent_metrics_by_ticker[ticker]
            if time.time() - cached.get("_timestamp", 0) < 600:
                return cls._dict_to_metrics(cached)

        try:
            # 1. DB에 저장된 최신 재무 지표 확인 (1일 이내 유효)
            latest_financials = StockMetaService.get_latest_financials(ticker)
            if latest_financials and (datetime.now() - latest_financials.base_date).total_seconds() < 86400:
                return cls._financials_row_to_metrics(latest_financials)

            # 2. 종목 메타 조회 후 KIS 요청용 메타 DTO 생성
            stock_meta = StockMetaService.get_stock_meta(ticker)
            if not stock_meta:
                stock_meta = StockMetaService.initialize_default_meta(ticker)

            kis_request_meta = KisFinancialsMeta(
                api_path=stock_meta.api_path or "",
                api_tr_id=stock_meta.api_tr_id or "",
                api_market_code=stock_meta.api_market_code or "",
            )

            # 3. KIS 원시 데이터 수집 → 응답 DTO로 수신 후 분석
            if is_kr(ticker):
                kis_payload = KisService.get_financials(ticker, meta=kis_request_meta)
                kis_financials_response = KisFinancialsResponse(
                    output=kis_payload.get("raw") or kis_payload.get("output") or {}
                )
                analyzed_metrics = FinancialAnalyzer.analyze_domestic_metrics(kis_financials_response)
            else:
                kis_payload = KisService.get_overseas_financials(ticker, meta=kis_request_meta)
                kis_financials_response = KisFinancialsResponse(
                    output=kis_payload.get("raw") or kis_payload.get("output") or {}
                )
                analyzed_metrics = FinancialAnalyzer.analyze_overseas_metrics(kis_financials_response)

            if not analyzed_metrics:
                if latest_financials:
                    return cls._financials_row_to_metrics(latest_financials)
                return None

            # 4. DB 저장 및 캐시( dict ) 후 모델 반환
            metrics_snapshot = analyzed_metrics.model_dump()
            StockMetaService.save_financials(ticker, metrics_snapshot)
            metrics_snapshot["_timestamp"] = time.time()
            cls._recent_metrics_by_ticker[ticker] = metrics_snapshot
            return analyzed_metrics

        except Exception as e:
            logger.error(f"Error getting metrics for {ticker}: {e}")
            return None

    @staticmethod
    def _dict_to_metrics(data: dict) -> AnalyzedFinancialMetrics:
        """캐시/저장용 dict를 AnalyzedFinancialMetrics로 변환."""
        return AnalyzedFinancialMetrics(
            per=float(data.get("per") or 0),
            pbr=float(data.get("pbr") or 0),
            roe=float(data.get("roe") or 0),
            eps=float(data.get("eps") or 0),
            bps=float(data.get("bps") or 0),
            dividend_yield=float(data.get("dividend_yield") or 0),
            current_price=float(data.get("current_price") or 0),
            market_cap=float(data.get("market_cap") or 0),
        )

    @staticmethod
    def _financials_row_to_metrics(row) -> AnalyzedFinancialMetrics:
        """DB Financials 행을 AnalyzedFinancialMetrics로 변환."""
        return AnalyzedFinancialMetrics(
            per=float(row.per or 0),
            pbr=float(row.pbr or 0),
            roe=float(row.roe or 0),
            eps=float(row.eps or 0),
            bps=float(row.bps or 0),
            dividend_yield=float(row.dividend_yield or 0),
            current_price=float(row.current_price or 0),
            market_cap=float(row.market_cap or 0),
        )

    @classmethod
    def _dcf_from_eps_history(cls, ticker: str) -> Optional[DcfInputData]:
        """1. 재무 이력 5년 EPS → CAGR + 할인율 → DcfInputData (없으면 None)"""
        yearly_eps_points = cls._build_yearly_eps_as_cashflow(ticker, years=5)
        if len(yearly_eps_points) < 5:
            return None
        cashflow_series = [p["cashflow"] for p in yearly_eps_points]
        return DcfInputData(
            fcf_per_share=cashflow_series[-1],
            beta=1.0,
            growth_rate=cls._calc_cagr(cashflow_series),
            discount_rate=cls._calc_discount_rate_from_volatility(cashflow_series),
            timestamp=time.time(),
            source="five_year_cashflow",
            years_used=[p["year"] for p in yearly_eps_points],
        )

    @classmethod
    def _dcf_from_yfinance(cls, ticker: str) -> Optional[DcfInputData]:
        """2. yfinance FCF → DcfInputData (없으면 None)"""
        market_type = "KR" if is_kr(ticker) else "US"
        yf_data = YFinanceService.get_fundamentals(ticker, market_type=market_type)
        if not (yf_data and yf_data.fcf_per_share and yf_data.fcf_per_share > 0):
            return None
        return DcfInputData(
            fcf_per_share=yf_data.fcf_per_share,
            beta=yf_data.beta,
            growth_rate=yf_data.growth_rate,
            discount_rate=None,
            timestamp=time.time(),
            source="yfinance",
        )

    @classmethod
    def _dcf_from_eps_per_fallback(cls, ticker: str) -> Optional[DcfInputData]:
        """3. DB 최신 EPS * PER → fallback_fair_value (없으면 None)"""
        latest = StockMetaService.get_latest_financials(ticker)
        if not (latest and latest.eps and latest.per and latest.eps > 0 and latest.per > 0):
            return None
        return DcfInputData(
            fcf_per_share=None,
            beta=1.0,
            growth_rate=0.0,
            discount_rate=None,
            fallback_fair_value=round(float(latest.eps) * float(latest.per), 2),
            timestamp=latest.base_date.timestamp() if latest.base_date else time.time(),
            source="eps_per_fallback",
        )

    @classmethod
    def _dcf_from_kis_api(cls, ticker: str) -> DcfInputData:
        """4. KIS API 원시 조회 → DcfInputData (최후 폴백, 항상 반환)"""
        if is_kr(ticker):
            kis_payload = KisService.get_financials(ticker)
            inputs = FinancialAnalyzer.analyze_dcf_inputs(domestic_data=kis_payload)
        else:
            kis_payload = KisService.get_overseas_financials(ticker)
            inputs = FinancialAnalyzer.analyze_dcf_inputs(overseas_data=kis_payload)

        eps_ttm = float(inputs.get("fcf_per_share") or 0)
        per_ttm = 0.0
        current_metrics = cls.get_metrics(ticker)
        if current_metrics:
            per_ttm = current_metrics.per

        if eps_ttm > 0 and per_ttm > 0:
            return DcfInputData(
                fcf_per_share=None,
                beta=inputs.get("beta", 1.0),
                growth_rate=0.0,
                discount_rate=None,
                fallback_fair_value=round(eps_ttm * per_ttm, 2),
                timestamp=time.time(),
                source="eps_per_fallback_api",
            )
        return DcfInputData(
            fcf_per_share=inputs.get("fcf_per_share"),
            beta=inputs.get("beta", 1.0),
            growth_rate=inputs.get("growth_rate", 0.05),
            discount_rate=None,
            timestamp=time.time(),
            source="kis",
        )

    @classmethod
    def get_dcf_data(cls, ticker: str) -> Optional[DcfInputData]:
        """DCF 계산 입력 데이터 반환.
        우선순위: 사용자 오버라이드 → 5년 EPS CAGR → yfinance FCF → EPS*PER → KIS API."""
        user_override = StockMetaService.get_dcf_override(ticker)
        if user_override:
            fv = getattr(user_override, "fair_value", None)
            dcf_input = DcfInputData(
                fcf_per_share=user_override.fcf_per_share,
                beta=user_override.beta or 1.0,
                growth_rate=user_override.growth_rate or 0.0,
                fallback_fair_value=float(fv) if fv else None,
                timestamp=user_override.updated_at.timestamp() if user_override.updated_at else time.time(),
                source="override",
            )
            cls._dcf_input_by_ticker[ticker] = dcf_input.model_dump()
            return dcf_input

        if ticker in cls._dcf_input_by_ticker:
            cached = cls._dcf_input_by_ticker[ticker]
            if time.time() - cached.get("timestamp", 0) < 1800:
                return cls._dict_to_dcf_input(cached)

        try:
            for method in (
                cls._dcf_from_eps_history,
                cls._dcf_from_yfinance,
                cls._dcf_from_eps_per_fallback,
                cls._dcf_from_kis_api,
            ):
                dcf_input = method(ticker)
                if dcf_input is not None:
                    cls._dcf_input_by_ticker[ticker] = dcf_input.model_dump()
                    return dcf_input
        except Exception as e:
            logger.error(f"Error getting DCF data for {ticker}: {e}")
        return None

    @staticmethod
    def _dict_to_dcf_input(data: dict) -> DcfInputData:
        """캐시/저장용 dict를 DcfInputData로 변환."""
        return DcfInputData(
            fcf_per_share=data.get("fcf_per_share"),
            beta=float(data.get("beta", 1.0)),
            growth_rate=float(data.get("growth_rate", 0.0)),
            discount_rate=data.get("discount_rate"),
            timestamp=float(data.get("timestamp", 0)),
            source=str(data.get("source", "")),
            years_used=data.get("years_used"),
            fallback_fair_value=data.get("fallback_fair_value"),
        )

    @classmethod
    def _build_yearly_eps_as_cashflow(cls, ticker: str, years: int = 5) -> list:
        """재무 이력에서 연도별 최근 EPS를 현금흐름 대용치로 추출. 과거→최신 순 리스트 반환."""
        financials_history = StockMetaService.get_financials_history(ticker, limit=3000)
        yearly_eps_points = []
        years_added = set()
        for history_row in financials_history:
            if not history_row or not history_row.base_date:
                continue
            base_year = history_row.base_date.year
            if base_year in years_added:
                continue
            eps_value = float(history_row.eps or 0)
            if eps_value <= 0:
                continue
            yearly_eps_points.append({"year": base_year, "cashflow": eps_value})
            years_added.add(base_year)
            if len(yearly_eps_points) >= years:
                break
        yearly_eps_points = list(reversed(yearly_eps_points))
        return yearly_eps_points

    @staticmethod
    def _calc_cagr(cashflow_series: list) -> float:
        """연복리 성장률(CAGR) 계산. 시계열 첫 값·끝 값 기준."""
        if not cashflow_series or len(cashflow_series) < 2 or cashflow_series[0] <= 0:
            return 0.05
        period_count = len(cashflow_series) - 1
        cagr = (cashflow_series[-1] / cashflow_series[0]) ** (1 / period_count) - 1
        return max(-0.15, min(0.25, cagr))

    @staticmethod
    def _calc_discount_rate_from_volatility(cashflow_series: list) -> float:
        """현금흐름 시계열의 변동성을 반영한 할인율 계산 (기본 9% + 변동성)."""
        if not cashflow_series or len(cashflow_series) < 2:
            return 0.10
        period_growth_rates = []
        for i in range(1, len(cashflow_series)):
            prev_value = cashflow_series[i - 1]
            curr_value = cashflow_series[i]
            if prev_value > 0:
                period_growth_rates.append((curr_value / prev_value) - 1)
        if not period_growth_rates:
            return 0.10
        avg_growth = sum(period_growth_rates) / len(period_growth_rates)
        variance = sum((g - avg_growth) ** 2 for g in period_growth_rates) / len(period_growth_rates)
        volatility = math.sqrt(max(0.0, variance))
        discount_rate = 0.09 + min(0.06, volatility)
        return max(0.06, min(0.15, discount_rate))

    @classmethod
    def get_overrides(cls) -> dict:
        """DB에 저장된 종목별 DCF 사용자 오버라이드 설정을 ticker → 설정 dict 로 반환."""
        try:
            from models.stock_meta import DcfOverride
            with StockMetaService.session_ro() as session:
                override_records = session.query(DcfOverride).all()
                return {
                    record.ticker: {
                        "fcf_per_share": record.fcf_per_share,
                        "beta": record.beta,
                        "growth_rate": record.growth_rate,
                        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
                    }
                    for record in override_records
                }
        except Exception:
            return {}

    @classmethod
    def save_override(cls, ticker: str, override_params: dict) -> dict:
        """종목별 DCF 사용자 오버라이드 저장. 저장 결과 dict 반환."""
        saved_override = StockMetaService.upsert_dcf_override(
            ticker=ticker,
            fcf_per_share=override_params.get("fcf_per_share"),
            beta=override_params.get("beta"),
            growth_rate=override_params.get("growth_rate"),
            fair_value=override_params.get("fair_value"),
        )
        if not saved_override:
            return {}
        override_snapshot = {
            "fcf_per_share": saved_override.fcf_per_share,
            "beta": saved_override.beta,
            "growth_rate": saved_override.growth_rate,
            "fair_value": saved_override.fair_value,
            "updated_at": saved_override.updated_at.isoformat() if saved_override.updated_at else None,
        }
        cls._dcf_input_by_ticker[ticker] = {
            **override_snapshot,
            "timestamp": time.time(),
            "source": "override",
        }
        return override_snapshot

    @classmethod
    def update_dcf_override(
        cls,
        ticker: str,
        fcf_per_share: float = None,
        beta: float = None,
        growth_rate: float = None,
        fair_value: float = None,
    ) -> dict:
        """사용자 지정 DCF 입력값 저장."""
        return cls.save_override(
            ticker,
            {"fcf_per_share": fcf_per_share, "beta": beta, "growth_rate": growth_rate, "fair_value": fair_value},
        )
