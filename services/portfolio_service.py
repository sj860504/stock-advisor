import pandas as pd
from typing import List, Dict, Optional
from datetime import datetime
import json
import os

class PortfolioService:
    """
    포트폴리오 관리 서비스
    엑셀 업로드 → 보유 종목 관리 → 수익률 분석
    """
    _portfolios: Dict[str, List[dict]] = {}  # user_id -> holdings
    _data_dir = os.path.join(os.path.dirname(__file__), '..', 'data')
    
    @classmethod
    def _ensure_data_dir(cls):
        if not os.path.exists(cls._data_dir):
            os.makedirs(cls._data_dir)
    
    @classmethod
    def parse_excel(cls, file_content: bytes, filename: str) -> List[dict]:
        """
        엑셀 파일을 파싱하여 보유 종목 리스트를 반환합니다.
        예상 컬럼: 종목명/티커, 수량, 매수가, (매수일)
        """
        import io
        
        if filename.endswith('.xlsx'):
            df = pd.read_excel(io.BytesIO(file_content), engine='openpyxl')
        elif filename.endswith('.xls'):
            df = pd.read_excel(io.BytesIO(file_content))
        elif filename.endswith('.csv'):
            df = pd.read_csv(io.BytesIO(file_content))
        else:
            raise ValueError("지원하지 않는 파일 형식입니다. xlsx, xls, csv만 지원합니다.")
        
        # 컬럼명 정규화
        df.columns = df.columns.str.strip().str.lower()
        
        # 컬럼 매핑 (다양한 이름 지원)
        column_mapping = {
            'ticker': ['ticker', '티커', '종목코드', 'symbol', 'code'],
            'name': ['name', '종목명', '종목', 'stock_name', '이름'],
            'quantity': ['quantity', '수량', 'shares', 'qty', '보유수량'],
            'buy_price': ['buy_price', '매수가', 'price', 'avg_price', '평균매수가', '매수단가'],
            'buy_date': ['buy_date', '매수일', 'date', '매수날짜']
        }
        
        def find_column(candidates):
            for col in candidates:
                if col in df.columns:
                    return col
            return None
        
        ticker_col = find_column(column_mapping['ticker'])
        name_col = find_column(column_mapping['name'])
        qty_col = find_column(column_mapping['quantity'])
        price_col = find_column(column_mapping['buy_price'])
        date_col = find_column(column_mapping['buy_date'])
        
        if not qty_col or not price_col:
            raise ValueError("필수 컬럼(수량, 매수가)을 찾을 수 없습니다.")
        
        if not ticker_col and not name_col:
            raise ValueError("종목 정보(티커 또는 종목명) 컬럼을 찾을 수 없습니다.")
        
        holdings = []
        for _, row in df.iterrows():
            holding = {
                'ticker': str(row[ticker_col]).strip() if ticker_col else None,
                'name': str(row[name_col]).strip() if name_col else None,
                'quantity': float(row[qty_col]),
                'buy_price': float(row[price_col]),
                'buy_date': str(row[date_col]) if date_col and pd.notnull(row.get(date_col)) else None
            }
            
            # 티커가 없으면 종목명으로 변환 시도
            if not holding['ticker'] and holding['name']:
                from .ticker_service import TickerService
                holding['ticker'] = TickerService.resolve_ticker(holding['name'])
            
            if holding['ticker'] and holding['quantity'] > 0:
                holdings.append(holding)
        
        return holdings
    
    @classmethod
    def save_portfolio(cls, user_id: str, holdings: List[dict]):
        """포트폴리오를 저장합니다."""
        cls._portfolios[user_id] = holdings
        
        # 파일로도 저장
        cls._ensure_data_dir()
        filepath = os.path.join(cls._data_dir, f'portfolio_{user_id}.json')
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(holdings, f, ensure_ascii=False, indent=2)
    
    @classmethod
    def load_portfolio(cls, user_id: str) -> List[dict]:
        """저장된 포트폴리오를 불러옵니다."""
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
        """
        포트폴리오 수익률 분석
        """
        holdings = cls.load_portfolio(user_id)
        if not holdings:
            return {"error": "포트폴리오가 없습니다. 먼저 엑셀을 업로드하세요."}
        
        total_invested = 0
        total_current = 0
        results = []
        
        for h in holdings:
            ticker = h['ticker']
            quantity = h['quantity']
            buy_price = h['buy_price']
            
            # 현재가 조회
            current_price = None
            if ticker in price_cache:
                current_price = price_cache[ticker].get('price')
            
            if current_price is None:
                from .data_service import DataService
                current_price = DataService.get_current_price(ticker)
            
            invested = quantity * buy_price
            current_value = quantity * current_price if current_price else 0
            profit = current_value - invested
            profit_pct = (profit / invested * 100) if invested > 0 else 0
            
            total_invested += invested
            total_current += current_value
            
            results.append({
                'ticker': ticker,
                'name': h.get('name'),
                'quantity': quantity,
                'buy_price': buy_price,
                'current_price': round(current_price, 2) if current_price else None,
                'invested': round(invested, 2),
                'current_value': round(current_value, 2),
                'profit': round(profit, 2),
                'profit_pct': round(profit_pct, 2)
            })
        
        total_profit = total_current - total_invested
        total_profit_pct = (total_profit / total_invested * 100) if total_invested > 0 else 0
        
        return {
            'holdings': results,
            'summary': {
                'total_invested': round(total_invested, 2),
                'total_current': round(total_current, 2),
                'total_profit': round(total_profit, 2),
                'total_profit_pct': round(total_profit_pct, 2),
                'holding_count': len(results)
            },
            'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
    
    @classmethod
    def add_holding(cls, user_id: str, ticker: str, quantity: float, buy_price: float, name: str = None):
        """수동으로 보유 종목을 추가합니다."""
        holdings = cls.load_portfolio(user_id)
        
        # 기존 종목이 있으면 평균 매수가 계산
        existing = next((h for h in holdings if h['ticker'] == ticker), None)
        if existing:
            total_qty = existing['quantity'] + quantity
            total_cost = (existing['quantity'] * existing['buy_price']) + (quantity * buy_price)
            existing['quantity'] = total_qty
            existing['buy_price'] = total_cost / total_qty
        else:
            holdings.append({
                'ticker': ticker,
                'name': name,
                'quantity': quantity,
                'buy_price': buy_price,
                'buy_date': datetime.now().strftime('%Y-%m-%d')
            })
        
        cls.save_portfolio(user_id, holdings)
        return holdings
    
    @classmethod
    def remove_holding(cls, user_id: str, ticker: str):
        """보유 종목을 제거합니다."""
        holdings = cls.load_portfolio(user_id)
        holdings = [h for h in holdings if h['ticker'] != ticker]
        cls.save_portfolio(user_id, holdings)
        return holdings
