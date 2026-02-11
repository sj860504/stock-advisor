from typing import Optional

class DcfService:
    """
    DCF (?꾧툑?먮쫫?좎씤踰? 怨꾩궛 ?꾨떞 ?쒕퉬??
    """
    
    @staticmethod
    def calculate_fair_value(fcf_per_share: float, growth_rate: float, beta: float, risk_free_rate: float = 0.04, terminal_growth: float = 0.03, manual_discount: Optional[float] = None) -> dict:
        """
        2?④퀎 ?깆옣 紐⑤뜽???ъ슜?섏뿬 ?곸젙 二쇨?瑜?怨꾩궛?⑸땲??
        """
        if not fcf_per_share or fcf_per_share <= 0:
            return {"value": None, "error": "Invalid FCF"}
            
        equity_risk_premium = 0.055  # 5.5%
        
        # CAPM: ?좎씤??怨꾩궛
        if manual_discount:
            discount_rate = manual_discount
        elif beta:
            discount_rate = risk_free_rate + beta * equity_risk_premium
        else:
            discount_rate = 0.10
        
        # ?좎씤???덉쟾?μ튂 (6% ~ 15%)
        discount_rate = max(0.06, min(0.15, discount_rate))
            
        terminal_growth_rate = terminal_growth
        
        # Stage 1: 10??怨좎꽦??
        future_fcf = []
        current_fcf = fcf_per_share
        for i in range(1, 11):
            # ?깆옣瑜??먯쭊??媛먯냼 (10?꾩감??terminal???섎졃)
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
        """?곗빱 湲곕컲 ?⑥닚 DCF ?꾨줉??(?곗씠???섏쭛 濡쒖쭅 蹂댁셿 ?꾩슂)"""
        # ?꾩옱???щТ ?곗씠???섏쭛 ?명꽣?섏씠?ㅺ? 蹂듭옟?섎?濡?0.0??諛섑솚
        return 0.0
