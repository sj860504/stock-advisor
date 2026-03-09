import pandas as pd
import io
import json
from typing import List, Dict

class FileService:
    """
    File processing service (Excel/CSV parsing)
    """

    @staticmethod
    def parse_portfolio_file(file_content: bytes, filename: str) -> List[Dict]:
        """Parse Excel/CSV file and convert to standard portfolio format."""
        try:
            if filename.endswith('.xlsx') or filename.endswith('.xls'):
                df = pd.read_excel(io.BytesIO(file_content))
            elif filename.endswith('.csv'):
                df = pd.read_csv(io.BytesIO(file_content))
            else:
                return []
                
            # Check required columns (flexible handling)
            # Expected columns: name, ticker, quantity, buy_price, sector
            
            df.columns = [str(col).lower().strip().replace(" ", "_") for col in df.columns]
            
            holdings = []
            for _, row in df.iterrows():
                ticker = str(row.get("ticker", "") or "").strip()
                name = str(row.get("name", "") or "").strip()
                try:
                    qty = float(row.get("quantity", 0))
                    price = float(row.get("buy_price", 0))
                except (TypeError, ValueError):
                    qty = 0
                    price = 0
                if qty > 0:
                    holdings.append({
                        "ticker": ticker if ticker != "nan" else None,
                        "name": name if name != "nan" else "Unknown",
                        "quantity": qty,
                        "buy_price": price,
                        "sector": str(row.get("sector", "Others"))
                    })
                    
            return holdings
            
        except Exception as e:
            print(f"Error parsing file: {e}")
            return []
