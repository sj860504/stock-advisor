from services.analysis.analyzer.dcf_analyzer import DcfAnalyzer
from services.analysis.financial_service import FinancialService

# DcfAnalyzer.calculate_fair_value 반환 dict 키
DCF_RESULT_KEY_VALUE = "value"


class DcfService:
    """
    DCF (현금흐름할인법) 계산 서비스
    - DcfAnalyzer 헬퍼를 사용하여 실제 계산을 수행합니다.
    """
    
    @classmethod
    def calculate_dcf(cls, ticker: str) -> float:
        """티커 기반 DCF 자동 계산 (KIS 데이터 기반)"""
        try:
            # FinancialService를 통해 가공된 재무 데이터 가져오기
            dcf_input = FinancialService.get_dcf_data(ticker)
            if not dcf_input:
                return 0.0

            # 데이터 부족 시 fallback: EPS(1Y) * PER
            if dcf_input.fallback_fair_value is not None:
                return float(dcf_input.fallback_fair_value) or 0.0

            if dcf_input.fcf_per_share is None:
                return 0.0

            growth = dcf_input.growth_rate
            beta = dcf_input.beta
            discount_rate = dcf_input.discount_rate
            
            # 헬퍼 메서드 호출
            result = DcfAnalyzer.calculate_fair_value(
                fcf_per_share=dcf_input.fcf_per_share,
                growth_rate=growth,
                beta=beta,
                manual_discount=discount_rate
            )
            return float(result.get(DCF_RESULT_KEY_VALUE, 0.0) or 0.0)
            
        except Exception:
            return 0.0
