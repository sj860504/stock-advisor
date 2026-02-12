import logging
from utils.logger import get_logger

logger = get_logger("financial_analyzer")

class FinancialAnalyzer:
    """
    수집된 원시 데이터를 분석하여 지표를 산출하는 헬퍼 클래스
    """
    
    @staticmethod
    def analyze_domestic_metrics(raw_data: dict) -> dict:
        """
        국내 주식 원시 데이터를 분석하여 표준 지표 반환 (FHKST01010100 기준)
        """
        output = raw_data.get('output', {})
        if not output:
            return {}
            
        try:
            return {
                "per": float(output.get('per', 0) or 0),
                "pbr": float(output.get('pbr', 0) or 0),
                "eps": float(output.get('eps', 0) or 0),
                "bps": float(output.get('bps', 0) or 0),
                "current_price": float(output.get('stck_prpr', 0) or 0),
                "market_cap": float(output.get('lstn_stkn', 0) or 0) * float(output.get('stck_prpr', 0) or 0) # 단순 계산
            }
        except (ValueError, TypeError):
            return {}

    @staticmethod
    def analyze_overseas_metrics(raw_data: dict) -> dict:
        """
        해외 주식 원시 데이터를 분석하여 표준 지표 반환 (HHDFS70200200 기준)
        """
        output = raw_data.get('output', {})
        if not output:
            return {}
            
        try:
            return {
                "per": float(output.get('per', 0) or 0),
                "pbr": float(output.get('pbr', 0) or 0),
                "roe": float(output.get('roe', 0) or 0),
                "eps": float(output.get('eps', 0) or 0),
                "bps": float(output.get('bps', 0) or 0),
                "dividend_yield": float(output.get('yield', 0) or 0),
                "current_price": float(output.get('last', 0) or 0),
                "market_cap": float(output.get('tomv', 0) or 0)
            }
        except (ValueError, TypeError):
            return {}

    @staticmethod
    def analyze_dcf_inputs(domestic_data: dict = None, overseas_data: dict = None) -> dict:
        """
        DCF 계산을 위한 입력 데이터 추출
        - KIS quotation 데이터에서 최대한 추출
        """
        result = {
            "fcf_per_share": None,
            "beta": 1.0,
            "growth_rate": 0.05
        }
        
        if domestic_data:
            output = domestic_data.get('output', {})
            # 국내의 경우 EPS를 FCF의 대용치로 사용하거나 (단순화), 
            # 실제 재무제표 API가 작동하지 않을 경우를 대비한 Fallback
            result["fcf_per_share"] = float(output.get('eps', 0) or 0) 
            
        if overseas_data:
            output = overseas_data.get('output', {})
            result["fcf_per_share"] = float(output.get('eps', 0) or 0)
            
        return result
