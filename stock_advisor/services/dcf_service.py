class DcfService:
    """
    DCF (현금흐름할인법) 계산 전담 서비스
    """
    
    @staticmethod
    def calculate_fair_value(
        fcf_per_share: float, 
        growth_rate: float, 
        beta: float, 
        risk_free_rate: float,
        terminal_growth: float = 0.03,
        equity_premium: float = 0.055,
        manual_discount: float = None
    ) -> dict:
        """
        2단계 성장 모델을 사용하여 적정 주가를 계산합니다.
        """
        if not fcf_per_share or fcf_per_share <= 0:
            return {"value": None, "error": "Invalid FCF"}
            
        # CAPM: 할인율 계산 (사용자가 직접 입력하지 않은 경우)
        if manual_discount is not None:
            discount_rate = manual_discount
        elif beta:
            discount_rate = risk_free_rate + beta * equity_premium
        else:
            discount_rate = 0.10
        
        # 할인율 안전장치 (최소 4% 이상 유지)
        discount_rate = max(0.04, discount_rate)
            
        # Stage 1: 10년 고성장
        future_fcf = []
        current_fcf = fcf_per_share
        for i in range(1, 11):
            # 성장률 점진적 감소 (10년차에 영구 성장률에 수렴)
            year_growth = growth_rate - (growth_rate - terminal_growth) * (i / 10)
            current_fcf = current_fcf * (1 + year_growth)
            discounted_fcf = current_fcf / ((1 + discount_rate) ** i)
            future_fcf.append(discounted_fcf)
            
        # Stage 2: Terminal Value
        # 분모가 0이 되는 것을 방지 (discount_rate > terminal_growth 필수)
        if discount_rate <= terminal_growth:
            return {"value": None, "error": "Discount rate must be higher than terminal growth"}
            
        terminal_value = (current_fcf * (1 + terminal_growth)) / (discount_rate - terminal_growth)
        discounted_terminal_value = terminal_value / ((1 + discount_rate) ** 10)
        
        fair_value = sum(future_fcf) + discounted_terminal_value
        
        return {
            "value": round(fair_value, 2),
            "discount_rate": round(discount_rate, 4),
            "growth_rate": round(growth_rate, 4),
            "terminal_growth": round(terminal_growth, 4)
        }
