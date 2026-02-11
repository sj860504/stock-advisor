from typing import Optional
from services.financial_service import FinancialService

class DcfService:
    """
    DCF (현금흐름할인법) 계산 전담 서비스
    """
    
    @classmethod
    def calculate_dcf(cls, ticker: str) -> float:
        """티커 기반 DCF 자동 계산"""
        try:
            # 재무 데이터 가져오기
            f_data = FinancialService.get_financial_data(ticker)
            if not f_data or f_data.get('fcf_per_share') is None:
                return 0.0
                
            fcf = f_data['fcf_per_share']
            growth = f_data.get('growth_rate', 0.05) # 기본 5%
            beta = f_data.get('beta', 1.0)
            
            result = cls.calculate_fair_value(fcf, growth, beta)
            return result.get('value', 0.0) or 0.0
            
        except Exception:
            return 0.0

    @staticmethod
    def calculate_fair_value(fcf_per_share: float, growth_rate: float, beta: float, risk_free_rate: float = 0.04, terminal_growth: float = 0.03, manual_discount: Optional[float] = None) -> dict:
        """
        2단계 성장 모델을 사용하여 적정 주가를 계산합니다.
        """
        if not fcf_per_share or fcf_per_share <= 0:
            return {"value": None, "error": "Invalid FCF"}
            
        equity_risk_premium = 0.055  # 5.5%
        
        # CAPM: 할인율 계산
        if manual_discount:
            discount_rate = manual_discount
        elif beta:
            discount_rate = risk_free_rate + beta * equity_risk_premium
        else:
            discount_rate = 0.10
        
        # 할인율 안전장치 (6% ~ 15%)
        discount_rate = max(0.06, min(0.15, discount_rate))
            
        terminal_growth_rate = terminal_growth
        
        # Stage 1: 10년 고성장
        future_fcf = []
        current_fcf = fcf_per_share
        for i in range(1, 11):
            # 성장률 점진적 감소 (10년차에 terminal로 수렴)
            year_growth = growth_rate - (growth_rate - terminal_growth_rate) * (i / 10)
            current_fcf = current_fcf * (1 + year_growth)
            discounted_fcf = current_fcf / ((1 + discount_rate) ** i)
            future_fcf.append(discounted_fcf)
            
        # Stage 2: Terminal Value
        terminal_value = (current_fcf * (1 + terminal_growth_rate)) / (discount_rate - terminal_growth_rate)
        discounted_terminal_value = terminal_value / ((1 + discount_rate) ** 10)
        
        fair_value = sum(future_fcf) + discounted_terminal_value
        
        return {
            "value": round(fair_value, 2),
            "discount_rate": round(discount_rate, 4),
            "growth_rate": round(growth_rate, 4)
        }
