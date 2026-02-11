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
        # FileService로 파싱 위임
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
    def sync_with_kis(cls, user_id: str = "sean") -> List[dict]:
        """KIS 실제 잔고와 동기화"""
        balance_data = KisService.get_balance()
        if not balance_data:
            return []
            
        holdings = []
        for item in balance_data.get('holdings', []):
            ticker = item.get('pdno')
            if not ticker or not ticker.isdigit(): # 종목번호가 숫자가 아니면 (합계 등) 제외
                continue
                
            holdings.append({
                "ticker": ticker,
                "name": item.get('prdt_name', 'Unknown'),
                "quantity": int(item.get('hldg_qty', 0)),
                "buy_price": float(item.get('pavg_unit_amt', 0)),
                "current_price": float(item.get('prpr', 0)),
                "sector": "Others"
            })
            
        # 총 예수금 저장 (output2에서 가져옴)
        summary_list = balance_data.get('summary', [])
        summary = summary_list[0] if summary_list else {}
        cash = float(summary.get('dnca_tot_amt', 0)) # 주문 가능 금액
        
        # 포트폴리오 저장 (캐시 필드 추가)
        cls.save_portfolio(user_id, holdings)
        
        # 현금 정보 별도 저장 (간소화를 위해 파일에 직접 기록)
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
        """목표 비중 설정 (예: {"005930": 0.3, "AAPL": 0.2, "Cash": 0.5})"""
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
        """목표 비중에 따른 자동 리밸런싱 실행"""
        # 1. 현재 상태 로드
        holdings = cls.sync_with_kis(user_id)
        cash = cls.load_cash(user_id)
        targets = cls.load_target_weights(user_id)
        
        if not targets:
            return {"status": "error", "msg": "목표 비중이 설정되지 않았습니다."}
            
        # 2. 현재 총 자산 가치 계산
        total_value = sum(h['current_price'] * h['quantity'] for h in holdings) + cash
        
        signals = []
        for ticker, target_ratio in targets.items():
            if ticker == "Cash": continue
            
            target_value = total_value * target_ratio
            current_holding = next((h for h in holdings if h['ticker'] == ticker), None)
            current_value = (current_holding['current_price'] * current_holding['quantity']) if current_holding else 0
            
            diff_value = target_value - current_value
            
            # 최소 거래 금액 설정 (예: 10,000원 이상 차이 날 때만)
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

        # 3. 신호 전송 및 알림
        if not signals:
            return {"status": "success", "msg": "리밸런싱이 필요하지 않습니다."}
            
        for s in signals:
            side_kr = "매수" if s['side'] == "buy" else "매도"
            msg = f"🔔 **[리밸런싱 시그널] {s['ticker']}**\n- 작업: {side_kr}\n- 수량: {s['quantity']}주\n- 사유: 비중 조절 (차액: {s['diff_value']:,.0f}원)"
            AlertService.send_slack_alert(msg)
            # KisService.send_order(...) # 실제 주문은 사용자 확인 후 수행하거나 자동으로 가능
            
        return {"status": "success", "signals": signals}

    @classmethod
    def analyze_portfolio(cls, user_id: str, price_cache: dict) -> dict:
        """포트폴리오 수익률 분석"""
        holdings = cls.load_portfolio(user_id)
        results = []
        total_invested = 0
        total_current = 0
        
        # 현금 비중 (portfolio_{user_id}.json 에 'cash' 필드가 있다고 가정하거나 0으로 시작)
        # TODO: 실제 현금 관리 로직 추가 필요
        cash = 0 
        
        for h in holdings:
            ticker = h['ticker']
            qty = h['quantity']
            buy_price = h['buy_price']
            
            # 현재가 조회 (캐시 우선)
            curr = h.get('current_price') # 잔고값
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
        
        # 밸런스 분석 추가
        analysis['balances'] = cls.calculate_balances(results, cash)
        
        return analysis

    @classmethod
    def calculate_balances(cls, holdings: List[dict], cash: float) -> dict:
        """마켓 및 섹터별 비중 계산"""
        total_value = sum(h['current_price'] * h['quantity'] for h in holdings) + cash
        if total_value == 0:
            return {}

        # 1. 마켓 별 밸런스 (KR/US/Cash)
        market_vals = {'KR': 0, 'US': 0, 'Cash': cash}
        for h in holdings:
            market_vals[h['market']] += h['current_price'] * h['quantity']
            
        market_balance = {k: round((v / total_value) * 100, 2) for k, v in market_vals.items()}

        # 2. 섹터 별 밸런스 (Tech/Semiconductor/Value)
        # 'sector' 정보가 없으면 'Others'로 분류
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
