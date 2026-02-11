import pandas as pd
import io
from typing import List

class FileService:
    """
    ?뚯씪(Excel, CSV) ?뚯떛 諛??곗씠??泥섎━ ?꾨떞 ?쒕퉬??
    """
    
    @staticmethod
    def parse_portfolio_file(file_content: bytes, filename: str) -> List[dict]:
        """?묒?/CSV ?뚯씪???뚯떛?섏뿬 ?쒖? ?ы듃?대━???щ㎎?쇰줈 蹂??""
        if filename.endswith('.xlsx'):
            # account ?쒗듃 ?곗꽑 ?뺤씤
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
            raise ValueError("吏?먰븯吏 ?딅뒗 ?뚯씪 ?뺤떇?낅땲?? xlsx, xls, csv留?吏?먰빀?덈떎.")
        
        # 而щ읆紐??뺢퇋??
        df.columns = df.columns.str.strip().str.lower()
        
        # 而щ읆 留ㅽ븨
        col_map = {
            'ticker': ['ticker', '?곗빱', '醫낅ぉ肄붾뱶', 'symbol', 'code'],
            'name': ['name', '醫낅ぉ紐?, '醫낅ぉ', 'stock_name', '?대쫫'],
            'quantity': ['quantity', '?섎웾', 'shares', 'qty', '蹂댁쑀?섎웾'],
            'buy_price': ['buy_price', '留ㅼ닔媛', 'price', 'avg_price', '?됯퇏留ㅼ닔媛', '留ㅼ닔?④?', 'avg'],
            'sector': ['sector', '?뱁꽣', '?낆쥌']
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
            raise ValueError("?꾩닔 而щ읆(?섎웾, 留ㅼ닔媛)??李얠쓣 ???놁뒿?덈떎.")
            
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
