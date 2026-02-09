import json
import os
from typing import List, Dict
from datetime import datetime
from stock_advisor.services.file_service import FileService
from stock_advisor.services.data_service import DataService
from stock_advisor.services.ticker_service import TickerService

class PortfolioService:
    """
    포트폴리오 관리 서비스 (Refactored)
    """
    _portfolios: Dict[str, List[dict]] = {}
    _data_dir = os.path.join(os.path.dirname(__file__), '..', 'data')
    
    @classmethod
    def _ensure_data_dir(cls):
        if not os.path.exists(cls._data_dir):
            os.makedirs(cls._data_dir)
            
    @classmethod
    def upload_portfolio(cls, file_content: bytes, filename: str, user_id: str = "sean") -> List[dict]:
        """엑셀 파일 업로드 및 저장"""
        # FileService에 파싱 위임
        holdings = FileService.parse_portfolio_file(file_content, filename)
        
        # 티커 변환 처리
        for h in holdings:
            if not h['ticker'] and h['name']:
                h['ticker'] = TickerService.resolve_ticker(h['name'])
                
        # 유효한 데이터만 필터링
        valid_holdings = [h for h in holdings if h['ticker'] and h['quantity'] > 0]
        
        cls.save_portfolio(user_id, valid_holdings)
        return valid_holdings

    @classmethod
    def save_portfolio(cls, user_id: str, holdings: List[dict]):
        cls._portfolios[user_id] = holdings
        cls._ensure_data_dir()
        filepath = os.path.join(cls._data_dir, f'portfolio_{user_id}.json')
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(holdings, f, ensure_ascii=False, indent=2)
    
    @classmethod
    def load_portfolio(cls, user_id: str) -> List[dict]:
        if user_id in cls._portfolios:
            return cls._portfolios[user_id]
        
        filepath = os.path.join(cls._data_dir, f'portfolio_{user_id}.json')
        if os.path.exists(filepath):
            with open(filepath, 'r', encoding='utf-8') as f:
                holdings = json.load(f)
                cls._portfolios[user_id] = holdings
                return holdings
        return []

    @classmethod
    def analyze_portfolio(cls, user_id: str, price_cache: dict) -> dict:
        """포트폴리오 수익률 분석"""
        holdings = cls.load_portfolio(user_id)
        results = []
        total_invested = 0
        total_current = 0
        
        for h in holdings:
            ticker = h['ticker']
            qty = h['quantity']
            buy_price = h['buy_price']
            
            # 현재가 조회 (캐시 우선)
            curr = h.get('current_price') # 엑셀값
            if not curr:
                if ticker in price_cache:
                    curr = price_cache[ticker].get('price')
                else:
                    curr = DataService.get_current_price(ticker) or buy_price
            
            val = qty * curr
            inv = qty * buy_price
            
            total_invested += inv
            total_current += val
            
            results.append({
                'ticker': ticker,
                'name': h.get('name'),
                'quantity': qty,
                'buy_price': buy_price,
                'current_price': round(curr, 2),
                'profit': round(val - inv, 2),
                'profit_pct': round(((val - inv)/inv)*100, 2)
            })
            
        return {
            'holdings': results,
            'summary': {
                'total_invested': round(total_invested, 2),
                'total_current': round(total_current, 2),
                'profit': round(total_current - total_invested, 2),
                'profit_pct': round(((total_current-total_invested)/total_invested)*100, 2)
            }
        }

    @classmethod
    def add_holding(cls, user_id: str, ticker: str, quantity: float, price: float, name: str = None) -> List[dict]:
        """종목 매수 (추가)"""
        holdings = cls.load_portfolio(user_id)
        
        # 기존 보유 종목 찾기
        existing = next((h for h in holdings if h['ticker'] == ticker), None)
        
        if existing:
            # 평단가 재계산 (이동평균법)
            total_cost = (existing['quantity'] * existing['buy_price']) + (quantity * price)
            total_qty = existing['quantity'] + quantity
            new_avg_price = total_cost / total_qty
            
            existing['quantity'] = total_qty
            existing['buy_price'] = new_avg_price
            # 이름 업데이트 (선택)
            if name: existing['name'] = name
        else:
            # 신규 추가
            holdings.append({
                'ticker': ticker,
                'name': name or ticker,
                'quantity': quantity,
                'buy_price': price,
                'date': datetime.now().strftime('%Y-%m-%d')
            })
            
        cls.save_portfolio(user_id, holdings)
        return holdings

    @classmethod
    def sell_holding(cls, user_id: str, ticker: str, quantity: float, price: float) -> List[dict]:
        """종목 매도 (감소)"""
        holdings = cls.load_portfolio(user_id)
        existing = next((h for h in holdings if h['ticker'] == ticker), None)
        
        if not existing:
            raise ValueError(f"보유하지 않은 종목입니다: {ticker}")
            
        if existing['quantity'] < quantity:
            raise ValueError(f"매도 수량이 보유 수량보다 많습니다. (보유: {existing['quantity']})")
            
        # 수량 차감
        existing['quantity'] -= quantity
        
        # 전량 매도 시 목록에서 제거
        if existing['quantity'] <= 0:
            holdings.remove(existing)
            
        cls.save_portfolio(user_id, holdings)
        return holdings

    @classmethod
    def remove_holding(cls, user_id: str, ticker: str) -> List[dict]:
        """종목 삭제 (강제 제거)"""
        holdings = cls.load_portfolio(user_id)
        holdings = [h for h in holdings if h['ticker'] != ticker]
        cls.save_portfolio(user_id, holdings)
        return holdings
