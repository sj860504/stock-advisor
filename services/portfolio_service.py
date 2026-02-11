import json
import os
from typing import List, Dict
from datetime import datetime
from services.file_service import FileService
from services.data_service import DataService
from services.ticker_service import TickerService
from services.kis_service import KisService
from services.alert_service import AlertService

class PortfolioService:
    """
    ?ы듃?대━??愿由??쒕퉬??(Refactored)
    """
    _portfolios: Dict[str, List[dict]] = {}
    _data_dir = os.path.join(os.path.dirname(__file__), '..', 'data')
    
    @classmethod
    def _ensure_data_dir(cls):
        if not os.path.exists(cls._data_dir):
            os.makedirs(cls._data_dir)
            
    @classmethod
    def upload_portfolio(cls, file_content: bytes, filename: str, user_id: str = "sean") -> List[dict]:
        """?묒? ?뚯씪 ?낅줈??諛????""
        # FileService???뚯떛 ?꾩엫
        holdings = FileService.parse_portfolio_file(file_content, filename)
        
        # ?곗빱 蹂??泥섎━
        for h in holdings:
            if not h['ticker'] and h['name']:
                h['ticker'] = TickerService.resolve_ticker(h['name'])
                
        # ?좏슚???곗씠?곕쭔 ?꾪꽣留?
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
    def sync_with_kis(cls, user_id: str = "sean") -> List[dict]:
        """KIS ?ㅼ젣 ?붽퀬? ?숆린??""
        balance_data = KisService.get_balance()
        if not balance_data:
            return []
            
        holdings = []
        for item in balance_data.get('holdings', []):
            ticker = item.get('pdno')
            if not ticker or not ticker.isdigit(): # 醫낅ぉ踰덊샇媛 ?レ옄媛 ?꾨땲硫?(?⑷퀎 ???? ?쒖쇅
                continue
                
            holdings.append({
                "ticker": ticker,
                "name": item.get('prdt_name', 'Unknown'),
                "quantity": int(item.get('hldg_qty', 0)),
                "buy_price": float(item.get('pavg_unit_amt', 0)),
                "current_price": float(item.get('prpr', 0)),
                "sector": "Others"
            })
            
        # 珥??덉닔湲????(output2?먯꽌 媛?몄샂)
        summary_list = balance_data.get('summary', [])
        summary = summary_list[0] if summary_list else {}
        cash = float(summary.get('dnca_tot_amt', 0)) # 二쇰Ц 媛??湲덉븸
        
        # ?ы듃?대━?????(罹먯떆 ?꾨뱶 異붽?)
        cls.save_portfolio(user_id, holdings)
        
        # ?꾧툑 ?뺣낫 蹂꾨룄 ???(媛꾩냼?붾? ?꾪빐 ?뚯씪??吏곸젒 湲곕줉)
        cash_path = os.path.join(cls._data_dir, f'cash_{user_id}.json')
        with open(cash_path, 'w') as f:
            json.dump({"cash": cash, "updated_at": datetime.now().isoformat()}, f)
            
        return holdings

    @classmethod
    def load_cash(cls, user_id: str) -> float:
        cash_path = os.path.join(cls._data_dir, f'cash_{user_id}.json')
        if os.path.exists(cash_path):
            with open(cash_path, 'r') as f:
                return json.load(f).get('cash', 0.0)
        return 0.0

    @classmethod
    def set_target_weights(cls, user_id: str, weights: Dict[str, float]):
        """紐⑺몴 鍮꾩쨷 ?ㅼ젙 (?? {"005930": 0.3, "AAPL": 0.2, "Cash": 0.5})"""
        target_path = os.path.join(cls._data_dir, f'target_{user_id}.json')
        with open(target_path, 'w') as f:
            json.dump(weights, f, ensure_ascii=False, indent=2)

    @classmethod
    def load_target_weights(cls, user_id: str) -> Dict[str, float]:
        target_path = os.path.join(cls._data_dir, f'target_{user_id}.json')
        if os.path.exists(target_path):
            with open(target_path, 'r') as f:
                return json.load(f)
        return {}

    @classmethod
    def rebalance_portfolio(cls, user_id: str = "sean"):
        """紐⑺몴 鍮꾩쨷???곕Ⅸ ?먮룞 由щ갭?곗떛 ?ㅽ뻾"""
        # 1. ?꾩옱 ?곹깭 濡쒕뱶
        holdings = cls.sync_with_kis(user_id)
        cash = cls.load_cash(user_id)
        targets = cls.load_target_weights(user_id)
        
        if not targets:
            return {"status": "error", "msg": "紐⑺몴 鍮꾩쨷???ㅼ젙?섏? ?딆븯?듬땲??"}
            
        # 2. ?꾩옱 珥??먯궛 媛移?怨꾩궛
        total_value = sum(h['current_price'] * h['quantity'] for h in holdings) + cash
        
        signals = []
        for ticker, target_ratio in targets.items():
            if ticker == "Cash": continue
            
            target_value = total_value * target_ratio
            current_holding = next((h for h in holdings if h['ticker'] == ticker), None)
            current_value = (current_holding['current_price'] * current_holding['quantity']) if current_holding else 0
            
            diff_value = target_value - current_value
            
            # 理쒖냼 嫄곕옒 湲덉븸 ?ㅼ젙 (?? 10,000???댁긽 李⑥씠 ???뚮쭔)
            if abs(diff_value) > 10000:
                price = current_holding['current_price'] if current_holding else DataService.get_current_price(ticker)
                if not price: continue
                
                qty = int(diff_value / price)
                if qty != 0:
                    side = "buy" if qty > 0 else "sell"
                    signals.append({
                        "ticker": ticker,
                        "side": side,
                        "quantity": abs(qty),
                        "price": price,
                        "diff_value": diff_value
                    })

        # 3. ?좊낫 ?꾩넚 諛??뚮┝
        if not signals:
            return {"status": "success", "msg": "由щ갭?곗떛???꾩슂?섏? ?딆뒿?덈떎."}
            
        for s in signals:
            side_kr = "留ㅼ닔" if s['side'] == "buy" else "留ㅻ룄"
            msg = f"?뽳툘 **[由щ갭?곗떛 ?쒓렇?? {s['ticker']}**\n- ?묒뾽: {side_kr}\n- ?섎웾: {s['quantity']}二?n- ?ъ쑀: 鍮꾩쨷 議곗젅 (李⑥븸: {s['diff_value']:,.0f}??"
            AlertService.send_slack_alert(msg)
            # KisService.send_order(...) # ?ㅼ젣 二쇰Ц? ?ъ슜???뺤씤 ???섑뻾?섍굅???먮룞??媛??
            
        return {"status": "success", "signals": signals}

    @classmethod
    def analyze_portfolio(cls, user_id: str, price_cache: dict) -> dict:
        """?ы듃?대━???섏씡瑜?遺꾩꽍"""
        holdings = cls.load_portfolio(user_id)
        results = []
        total_invested = 0
        total_current = 0
        
        # ?꾧툑 鍮꾩쨷 (portfolio_{user_id}.json ??'cash' ?꾨뱶媛 ?덈떎怨?媛?뺥븯嫄곕굹 0?쇰줈 ?쒖옉)
        # TODO: ?ㅼ젣 ?꾧툑 愿由?濡쒖쭅 異붽? ?꾩슂
        cash = 0 
        
        for h in holdings:
            ticker = h['ticker']
            qty = h['quantity']
            buy_price = h['buy_price']
            
            # ?꾩옱媛 議고쉶 (罹먯떆 ?곗꽑)
            curr = h.get('current_price') # ?묒?媛?
            if not curr:
                if ticker and ticker in price_cache:
                    curr = price_cache[ticker].get('price')
                elif ticker:
                    curr = DataService.get_current_price(ticker) or buy_price
                else:
                    curr = buy_price
            
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
                'profit_pct': round(((val - inv)/inv)*100, 2),
                'sector': h.get('sector'),
                'market': 'KR' if ticker and (ticker.isdigit() or ticker.endswith(('.KS', '.KQ'))) else 'US'
            })
            
        analysis = {
            'holdings': results,
            'summary': {
                'total_invested': round(total_invested, 2),
                'total_current': round(total_current, 2),
                'profit': round(total_current - total_invested, 2),
                'profit_pct': round(((total_current-total_invested)/total_invested)*100, 2) if total_invested > 0 else 0
            }
        }
        
        # 諛몃윴??遺꾩꽍 異붽?
        analysis['balances'] = cls.calculate_balances(results, cash)
        
        return analysis

    @classmethod
    def calculate_balances(cls, holdings: List[dict], cash: float) -> dict:
        """留덉폆 諛??뱁꽣蹂?鍮꾩쨷 怨꾩궛"""
        total_value = sum(h['current_price'] * h['quantity'] for h in holdings) + cash
        if total_value == 0:
            return {}

        # 1. 留덉폆 蹂?諛몃윴??(KR/US/Cash)
        market_vals = {'KR': 0, 'US': 0, 'Cash': cash}
        for h in holdings:
            market_vals[h['market']] += h['current_price'] * h['quantity']
            
        market_balance = {k: round((v / total_value) * 100, 2) for k, v in market_vals.items()}

        # 2. ?뱁꽣 蹂?諛몃윴??(Tech/Semiconductor/Value)
        # 'sector' ?뺣낫媛 ?놁쑝硫?'Others'濡?遺꾨쪟
        sector_vals = {'Technology': 0, 'Semiconductor': 0, 'Value': 0, 'Others': 0}
        ticker_total_val = total_value - cash
        
        for h in holdings:
            s = h.get('sector')
            val = h['current_price'] * h['quantity']
            
            if s == 'Technology':
                sector_vals['Technology'] += val
            elif s == 'Semiconductor':
                sector_vals['Semiconductor'] += val
            elif s in ['Value', 'Financials', 'Industrials', 'Energy', 'Utilities']:
                sector_vals['Value'] += val
            else:
                sector_vals['Others'] += val
        
        sector_balance = {k: round((v / ticker_total_val) * 100, 2) for k, v in sector_vals.items()} if ticker_total_val > 0 else {}

        return {
            'market': market_balance,
            'sector': sector_balance
        }
