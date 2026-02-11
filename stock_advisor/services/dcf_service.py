from typing import Optional

class DcfService:
    """
    DCF (현금흐름할인법) 계산 전담 서비스
    """
    
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
            # 성장률 점진적 감소 (10년차에 terminal에 수렴)
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

    @staticmethod
    def calculate_dcf(ticker: str) -> float:
        """티커 기반 단순 DCF 프록시 (데이터 수집 로직 보완 필요)"""
        # 현재는 재무 데이터 수집 인터페이스가 복잡하므로 0.0을 반환
        return 0.0
