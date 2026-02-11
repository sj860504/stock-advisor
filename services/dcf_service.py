from services.analyzer.dcf_analyzer import DcfAnalyzer
from services.financial_service import FinancialService

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
            f_data = FinancialService.get_dcf_data(ticker)
            if not f_data or f_data.get('fcf_per_share') is None:
                return 0.0
                
            fcf = f_data['fcf_per_share']
            growth = f_data.get('growth_rate', 0.05)
            beta = f_data.get('beta', 1.0)
            
            # 헬퍼 메서드 호출
            result = DcfAnalyzer.calculate_fair_value(fcf, growth, beta)
            return result.get('value', 0.0) or 0.0
            
        except Exception:
            return 0.0
