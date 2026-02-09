import pandas as pd
import io
from typing import List

class FileService:
    """
    파일(Excel, CSV) 파싱 및 데이터 처리 전담 서비스
    """
    
    @staticmethod
    def parse_portfolio_file(file_content: bytes, filename: str) -> List[dict]:
        """엑셀/CSV 파일을 파싱하여 표준 포트폴리오 포맷으로 변환"""
        if filename.endswith('.xlsx'):
            # account 시트 우선 확인
            xlsx = pd.ExcelFile(io.BytesIO(file_content), engine='openpyxl')
            if 'account' in xlsx.sheet_names:
                df = pd.read_excel(xlsx, sheet_name='account')
            else:
                df = pd.read_excel(xlsx, sheet_name=0)
        elif filename.endswith('.xls'):
            df = pd.read_excel(io.BytesIO(file_content))
        elif filename.endswith('.csv'):
            df = pd.read_csv(io.BytesIO(file_content))
        else:
            raise ValueError("지원하지 않는 파일 형식입니다. xlsx, xls, csv만 지원합니다.")
        
        # 컬럼명 정규화
        df.columns = df.columns.str.strip().str.lower()
        
        # 컬럼 매핑
        col_map = {
            'ticker': ['ticker', '티커', '종목코드', 'symbol', 'code'],
            'name': ['name', '종목명', '종목', 'stock_name', '이름'],
            'quantity': ['quantity', '수량', 'shares', 'qty', '보유수량'],
            'buy_price': ['buy_price', '매수가', 'price', 'avg_price', '평균매수가', '매수단가', 'avg'],
            'sector': ['sector', '섹터', '업종']
        }
        
        def find_col(candidates):
            for c in candidates:
                if c in df.columns: return c
            return None
            
        ticker_col = find_col(col_map['ticker'])
        name_col = find_col(col_map['name'])
        qty_col = find_col(col_map['quantity'])
        price_col = find_col(col_map['buy_price'])
        sector_col = find_col(col_map['sector'])
        
        if not (qty_col and price_col):
            raise ValueError("필수 컬럼(수량, 매수가)을 찾을 수 없습니다.")
            
        holdings = []
        for _, row in df.iterrows():
            if pd.isna(row.get(ticker_col or name_col)): continue
            
            try:
                qty = float(row[qty_col])
                price = float(row[price_col])
                if qty <= 0 or price <= 0: continue
                
                ticker = str(row[ticker_col]).strip() if ticker_col else None
                name = str(row[name_col]).strip() if name_col else None
                
                holdings.append({
                    'ticker': ticker,
                    'name': name or ticker,
                    'quantity': qty,
                    'buy_price': price,
                    'sector': str(row[sector_col]).strip() if sector_col and pd.notnull(row.get(sector_col)) else None
                })
            except:
                continue
                
        return holdings
