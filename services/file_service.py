import pandas as pd
import io
import json
from typing import List, Dict

class FileService:
    """
    파일 처리 서비스 (엑셀/CSV 파싱)
    """

    @staticmethod
    def parse_portfolio_file(file_content: bytes, filename: str) -> List[Dict]:
        """엑셀/CSV 파일을 파싱하여 표준 포트폴리오 포맷으로 변환"""
        try:
            if filename.endswith('.xlsx') or filename.endswith('.xls'):
                df = pd.read_excel(io.BytesIO(file_content))
            elif filename.endswith('.csv'):
                df = pd.read_csv(io.BytesIO(file_content))
            else:
                return []
                
            # 필수 컬럼 확인 (유연하게 처리)
            # 예상 컬럼: 종목명(name), 종목코드(ticker), 수량(quantity), 매수단가(buy_price), 섹터(sector)
            
            # 컬럼명 정규화 (소문자, 공백제거)
            df.columns = [str(c).lower().strip().replace(' ', '_') for c in df.columns]
            
            holdings = []
            for _, row in df.iterrows():
                # ticker가 없으면 종목명으로라도 처리
                ticker = str(row.get('ticker', '')).strip()
                name = str(row.get('name', '')).strip()
                
                # 수량과 가격은 숫자로 변환
                try:
                    qty = float(row.get('quantity', 0))
                    price = float(row.get('buy_price', 0))
                except:
                    qty = 0
                    price = 0
                    
                if qty > 0:
                    holdings.append({
                        "ticker": ticker if ticker != 'nan' else None,
                        "name": name if name != 'nan' else "Unknown",
                        "quantity": qty,
                        "buy_price": price,
                        "sector": str(row.get('sector', 'Others'))
                    })
                    
            return holdings
            
        except Exception as e:
            print(f"Error parsing file: {e}")
            return []
