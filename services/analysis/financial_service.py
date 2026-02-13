import os
import time
from datetime import datetime
import pandas as pd
from config import Config
from utils.logger import get_logger
from services.kis.kis_service import KisService
from services.analysis.analyzer.financial_analyzer import FinancialAnalyzer
from services.market.stock_meta_service import StockMetaService

logger = get_logger("financial_service")

class FinancialService:
    """
    종목별 재무 지표 및 DCF 데이터 제공 서비스
    - KisService를 통해 원시 데이터를 가져오고 FinancialAnalyzer를 통해 가공합니다.
    """
    _metrics_cache = {}
    _dcf_cache = {}
    _overrides_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'dcf_settings.json')

    @classmethod
    def get_metrics(cls, ticker: str) -> dict:
        """종목별 핵심 재무 지표 반환 (KIS API 기반)"""
        if ticker in cls._metrics_cache:
            cached = cls._metrics_cache[ticker]
            if time.time() - cached.get('_timestamp', 0) < 600:
                return cached
        
        try:
            # 1. DB 캐시 먼저 확인
            latest = StockMetaService.get_latest_financials(ticker)
            if latest and (datetime.now() - latest.base_date).total_seconds() < 86400: # 1일간 유효
                return {
                    "per": latest.per,
                    "pbr": latest.pbr,
                    "roe": latest.roe,
                    "eps": latest.eps,
                    "bps": latest.bps,
                    "dividend_yield": latest.dividend_yield,
                    "current_price": latest.current_price,
                    "market_cap": latest.market_cap,
                    "_timestamp": latest.base_date.timestamp()
                }

            # 2. 메타데이터 조회
            meta_obj = StockMetaService.get_stock_meta(ticker)
            if not meta_obj:
                meta_obj = StockMetaService.initialize_default_meta(ticker)
            
            meta_dict = {
                "api_path": meta_obj.api_path,
                "api_tr_id": meta_obj.api_tr_id,
                "api_market_code": meta_obj.api_market_code
            }

            # 3. 원시 데이터 수집 (KisService 활용)
            if ticker.isdigit():
                raw_data = KisService.get_financials(ticker, meta=meta_dict)
                metrics = FinancialAnalyzer.analyze_domestic_metrics(raw_data)
            else:
                raw_data = KisService.get_overseas_financials(ticker, meta=meta_dict)
                metrics = FinancialAnalyzer.analyze_overseas_metrics(raw_data)
            
            if not metrics:
                # API 호출 실패 시 (404 등 발생) 메타 정보 수정 시도 로직 등을 넣을 수 있음
                if latest:
                    return {
                        "per": latest.per, "pbr": latest.pbr, "roe": latest.roe,
                        "eps": latest.eps, "bps": latest.bps,
                        "dividend_yield": latest.dividend_yield,
                        "current_price": latest.current_price,
                        "market_cap": latest.market_cap,
                        "_timestamp": latest.base_date.timestamp(),
                        "_stale": True
                    }
                return {}
                
            # 4. DB에 저장
            StockMetaService.save_financials(ticker, metrics)
            
            metrics['_timestamp'] = time.time()
            cls._metrics_cache[ticker] = metrics
            return metrics
            
        except Exception as e:
            logger.error(f"Error getting metrics for {ticker}: {e}")
            return {}

    @classmethod
    def get_dcf_data(cls, ticker: str) -> dict:
        """DCF 계산에 필요한 데이터 반환 (KIS API 기반)"""
        # 0. 사용자 지정 값 우선 적용
        override = StockMetaService.get_dcf_override(ticker)
        if override:
            result = {
                "fcf_per_share": override.fcf_per_share,
                "beta": override.beta,
                "growth_rate": override.growth_rate,
                "timestamp": override.updated_at.timestamp() if override.updated_at else time.time(),
                "source": "override"
            }
            cls._dcf_cache[ticker] = result
            return result

        if ticker in cls._dcf_cache:
            cached = cls._dcf_cache[ticker]
            if time.time() - cached.get('timestamp', 0) < 1800:
                return cached

        try:
            # 1. DB 캐시 확인
            latest = StockMetaService.get_latest_financials(ticker)
            if latest and latest.eps and latest.bps:
                # DB에 유효한 재무 데이터가 있다면 사용
                return {
                    "fcf_per_share": latest.eps * 0.8, # FCF 추정 (EPS의 80%로 단순화)
                    "beta": 1.0,
                    "growth_rate": 0.05,
                    "timestamp": latest.base_date.timestamp()
                }

            # 2. API 호출 (DB에 없거나 부족할 경우)
            if ticker.isdigit():
                raw_data = KisService.get_financials(ticker)
                metrics = FinancialAnalyzer.analyze_dcf_inputs(domestic_data=raw_data)
            else:
                # 상세 시세를 위해 fetch_overseas_detail을 사용하는 메서드로 호출 권장 (나중에 KisService 보완 필요)
                raw_data = KisService.get_overseas_financials(ticker)
                metrics = FinancialAnalyzer.analyze_dcf_inputs(overseas_data=raw_data)
            
            result = {
                "fcf_per_share": metrics.get('fcf_per_share'),
                "beta": metrics.get('beta', 1.0),
                "growth_rate": metrics.get('growth_rate', 0.05),
                "timestamp": time.time(),
                "source": "kis"
            }
            
            cls._dcf_cache[ticker] = result
            return result
        except Exception as e:
            logger.error(f"Error getting DCF data for {ticker}: {e}")
            return {}

    @classmethod
    def get_overrides(cls) -> dict:
        # 호환용: DB에 저장된 사용자 설정을 dict로 반환
        try:
            session = StockMetaService.get_session()
            from models.stock_meta import DcfOverride
            rows = session.query(DcfOverride).all()
            return {
                row.ticker: {
                    "fcf_per_share": row.fcf_per_share,
                    "beta": row.beta,
                    "growth_rate": row.growth_rate,
                    "updated_at": row.updated_at.isoformat() if row.updated_at else None
                }
                for row in rows
            }
        except Exception:
            return {}

    @classmethod
    def save_override(cls, ticker: str, params: dict):
        row = StockMetaService.upsert_dcf_override(
            ticker=ticker,
            fcf_per_share=params.get("fcf_per_share"),
            beta=params.get("beta"),
            growth_rate=params.get("growth_rate")
        )
        if not row:
            return {}
        result = {
            "fcf_per_share": row.fcf_per_share,
            "beta": row.beta,
            "growth_rate": row.growth_rate,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None
        }
        cls._dcf_cache[ticker] = {
            **result,
            "timestamp": time.time(),
            "source": "override"
        }
        return result

    @classmethod
    def update_dcf_override(cls, ticker: str, fcf_per_share: float, beta: float, growth_rate: float) -> dict:
        """사용자 지정 DCF 입력값을 간단히 저장"""
        return cls.save_override(
            ticker,
            {
                "fcf_per_share": fcf_per_share,
                "beta": beta,
                "growth_rate": growth_rate
            }
        )
