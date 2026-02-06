import pandas as pd
import numpy as np
from .data_service import DataService
from .financial_service import FinancialService
from .ticker_service import TickerService

class AnalysisService:
    @staticmethod
    def calculate_dcf(ticker: str, current_price: float):
        # 1. Yahoo Finance Ticker로 변환
        yahoo_ticker = TickerService.get_yahoo_ticker(ticker)
        
        # 2. 데이터 수집
        data = FinancialService.get_dcf_data(yahoo_ticker)
        fcf = data.get('fcf')
        shares = data.get('shares')
        beta = data.get('beta')
        growth_rate = data.get('growth_rate', 0.05) # 기본 5%
        
        if not fcf or not shares or fcf < 0:
            return None, "DCF 데이터 부족 또는 적자(FCF < 0)"
            
        # 3. 파라미터 설정
        risk_free_rate = 0.04  # 무위험 수익률 (4%)
        market_return = 0.10   # 시장 수익률 (10%)
        
        # CAPM: Cost of Equity = Rf + Beta * (Rm - Rf)
        if beta:
            discount_rate = risk_free_rate + beta * (market_return - risk_free_rate)
        else:
            discount_rate = 0.10 # 기본 할인율 10%
            
        terminal_growth_rate = 0.025 # 영구 성장률 2.5%
        
        # 4. DCF 계산 (2-Stage Model)
        # Stage 1: 5년 고성장
        future_fcf = []
        for i in range(1, 6):
            projected_fcf = fcf * ((1 + growth_rate) ** i)
            discounted_fcf = projected_fcf / ((1 + discount_rate) ** i)
            future_fcf.append(discounted_fcf)
            
        # Stage 2: Terminal Value
        last_fcf = fcf * ((1 + growth_rate) ** 5)
        terminal_value = (last_fcf * (1 + terminal_growth_rate)) / (discount_rate - terminal_growth_rate)
        discounted_terminal_value = terminal_value / ((1 + discount_rate) ** 5)
        
        total_enterprise_value = sum(future_fcf) + discounted_terminal_value
        
        # Equity Value per Share (부채 제외 로직은 생략 - Levered FCF 사용하므로 이미 Equity Value 근접)
        # Levered FCF는 이미 채권자 몫을 제외한 것이므로 바로 주식수로 나눔
        target_price = total_enterprise_value / shares
        
        return target_price, f"DCF(성장률 {growth_rate*100:.1f}%, 할인율 {discount_rate*100:.1f}%)"

    @staticmethod
    def calculate_rsi(series, period=14):
        delta = series.diff(1)
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        return 100 - (100 / (1 + rs))

    @staticmethod
    def evaluate_stock(ticker: str):
        # 1. 가격 데이터 조회
        df = DataService.get_price_data(ticker, start_date="2024-01-01")
        if df is None or df.empty:
            return {"error": "No price data found"}

        current_price = float(df['Close'].iloc[-1])
        
        # 2. 기술적 분석
        df['MA20'] = df['Close'].rolling(window=20).mean()
        df['MA60'] = df['Close'].rolling(window=60).mean()
        rsi = AnalysisService.calculate_rsi(df['Close']).iloc[-1]
        
        tech_score = 50 # 기본 50점
        tech_logic = []
        
        # 이평선
        ma20 = df['MA20'].iloc[-1]
        ma60 = df['MA60'].iloc[-1]
        
        if current_price > ma20 and ma20 > ma60:
            tech_score += 20
            tech_logic.append("상승 추세(정배열)")
        elif current_price < ma20 and ma20 < ma60:
            tech_score -= 20
            tech_logic.append("하락 추세(역배열)")
            
        # RSI
        if rsi < 30:
            tech_score += 10
            tech_logic.append("과매도(반등 기대)")
        elif rsi > 70:
            tech_score -= 10
            tech_logic.append("과매수(조정 주의)")
            
        # 3. 기본적(재무) 분석
        metrics = FinancialService.get_metrics(ticker)
        fund_score = 50
        fund_logic = []
        
        per = metrics.get('per')
        pbr = metrics.get('pbr')
        
        if per:
            if per < 10:
                fund_score += 20
                fund_logic.append("저PER(저평가)")
            elif per > 50:
                fund_score -= 10
                fund_logic.append("고PER(고성장 기대/과열)")
        
        if pbr:
            if pbr < 1:
                fund_score += 10
                fund_logic.append("저PBR(자산가치 우수)")
                
        # 4. 종합 평가
        
        # 적정 주가 계산 (Analyst Target -> Graham Number -> None)
        target_price = metrics.get('target_price')
        calc_method = "Analyst Consensus"
        
        # Analyst Target이 없으면 그레이엄 공식 시도 (보수적 가치평가)
        if target_price is None and per and pbr and per > 0 and pbr > 0:
            # EPS = Price / PER, BPS = Price / PBR
            eps = current_price / per
            bps = current_price / pbr
            # Graham Number = Sqrt(22.5 * EPS * BPS)
            target_price = (22.5 * eps * bps) ** 0.5
            calc_method = "Graham Number (Intrinsic)"
            
        # DCF 평가 추가 (현금흐름 기반)
        dcf_price, dcf_method = AnalysisService.calculate_dcf(ticker, current_price)
        dcf_gap = 0
        if dcf_price:
            dcf_gap = ((dcf_price - current_price) / current_price) * 100
            # 만약 기존 목표가가 없으면 DCF를 메인으로 사용
            if target_price is None:
                target_price = dcf_price
                calc_method = dcf_method
        
        total_score = int((tech_score + fund_score) / 2)
        total_score = max(0, min(100, total_score)) # 0~100 클램핑
        
        rating = "Hold"
        if total_score >= 70: rating = "Buy"
        elif total_score <= 30: rating = "Sell"
        
        final_logic = f"기술적: {', '.join(tech_logic) if tech_logic else '중립'}, 기본적: {', '.join(fund_logic) if fund_logic else '중립'}"
        if target_price:
             gap = ((target_price - current_price) / current_price) * 100
             final_logic += f", 목표가 괴리율: {gap:.1f}% ({calc_method})"
        
        if dcf_price:
            final_logic += f" | DCF 적정가: {round(dcf_price, 0)} ({dcf_gap:.1f}%)"

        return {
            "ticker": ticker,
            "current_price": current_price,
            "target_price": round(target_price, 2) if target_price else None,
            "rating": rating,
            "score": total_score,
            "logic": final_logic,
            "technical": {
                "rsi": round(rsi, 2),
                "ma_trend": "Bullish" if ma20 > ma60 else "Bearish"
            },
            "fundamental": metrics
        }

    @staticmethod
    def analyze_returns(ticker: str):
        df = DataService.get_price_data(ticker, start_date="2024-01-01")
        if df is None or df.empty:
            return None
            
        start_price = df['Close'].iloc[0]
        end_price = df['Close'].iloc[-1]
        
        total_return = ((end_price - start_price) / start_price) * 100
        
        # Max Drawdown (MDD)
        rolling_max = df['Close'].cummax()
        drawdown = df['Close'] / rolling_max - 1.0
        mdd = drawdown.min() * 100

        return {
            "ticker": ticker,
            "period": "Since 2024-01-01",
            "return_percentage": round(total_return, 2),
            "max_drawdown": round(mdd, 2)
        }
