import json
import os
from typing import List, Dict
from datetime import datetime
from .file_service import FileService
from .data_service import DataService
from .ticker_service import TickerService

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
