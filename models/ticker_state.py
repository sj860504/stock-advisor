from dataclasses import dataclass, field
from typing import Dict, List, Optional
from collections import deque
import logging

from utils.logger import get_logger

logger = get_logger("ticker_state")

@dataclass
class TickerState:
    ticker: str
    name: str = ""                # 종목명 추가
    current_price: float = 0.0
    open_price: float = 0.0
    high_price: float = 0.0
    low_price: float = 0.0
    prev_close: float = 0.0  # 전일 종가
    volume: int = 0
    change_rate: float = 0.0 # 등락률 (%)
    
    # 지표들
    ema: Dict[int, float] = field(default_factory=dict) # {5: 1000, 20: 950, ...}
    rsi: float = 0.0             # RSI (14)
    bollinger: Dict[str, float] = field(default_factory=dict) # {upper, middle, lower}
    dcf_value: float = 0.0       # 적정주가 (DCF)
    
    # 전략 타겟가
    target_buy_price: float = 0.0  # 목표 진입가
    target_sell_price: float = 0.0 # 목표 매도가
    
    # 데이터 버퍼 (최근 N개의 종가, 실시간 EMA 계산용)
    # 실제로는 일봉 데이터 로딩 후, 실시간 가격이 변할 때 '오늘의 종가(현재가)'로 가정하고 EMA를 재계산하는 방식이 일반적
    # 또는 분봉 기준이라면 분봉 완성 시점에 확정. 여기서는 '일봉 기준 실시간 EMA'를 추정한다고 가정.
    
    def __post_init__(self) -> None:
        # field(default_factory=dict) 를 사용하므로 명시적 초기화 불필요
        pass

    def update_from_socket(self, data_dict: dict) -> None:
        """
        WebSocket 수신 데이터로 상태 업데이트
        data_dict: KIS Websocket H0STCNT0 포맷 파싱 결과
        """
        try:
            # KIS 실시간 체결가 데이터 매핑
            # H0STCNT0: 유가증권 단축 종목코드(0), 영업시간(1), 현재가(2), 전일대비구분(3), 전일대비(4), 전일대비율(5)... 시가(7), 고가(8), 저가(9)...
            
            # 파싱 로직은 외부에서 처리해서 깔끔한 dict로 넘겨주는 게 좋음
            # 예: {'price': 70000, 'rate': 1.5, 'open': 69000, ...}
            
            new_price = float(data_dict.get('mksc_shrn_iscd', 0)) # 실시간 체결가는 2번째가 아니라 파싱된 dict 키 사용
            # 주의: WebSocket raw data 파싱은 Service 레벨에서 수행하고 여기엔 값만 전달
            
            self.current_price = float(data_dict.get('stck_prpr', self.current_price)) # 현재가
            self.open_price = float(data_dict.get('stck_oprc', self.open_price))       # 시가
            self.high_price = float(data_dict.get('stck_hgpr', self.high_price))       # 고가
            self.low_price = float(data_dict.get('stck_lwpr', self.low_price))         # 저가
            
            # 전일 대비율
            self.change_rate = float(data_dict.get('rt_cd', 0.0)) 
            
            # 거래량 (누적 거래량)
            self.volume = int(data_dict.get('acml_vol', self.volume))
            
            # 전일 종가는 보통 별도 조회 필요 (실시간 데이터에도 포함될 수 있으나 계산용으로 미리 세팅 권장)
            
            # 지표 실시간 업데이트
            self.recalculate_indicators()
            
        except Exception as e:
            logger.error(f"Error updating ticker state: {e}")

    def recalculate_indicators(self) -> None:
        """현재가를 기준으로 실시간 지표(EMA 등) 재계산"""
        if not self.ema or self.current_price <= 0:
            return
            
        # 일봉 기준 실시간 EMA 추정
        # EMA_today = (Price_today * alpha) + (EMA_yesterday * (1 - alpha))
        for n, prev_ema_val in list(self.ema.items()):
            try:
                # 키가 정수인 경우만 수행 (예: 5, 20, 100...)
                period = int(n)
                alpha = 2 / (period + 1)
                self.ema[period] = round((self.current_price * alpha) + (prev_ema_val * (1 - alpha)), 2)
            except (ValueError, TypeError):
                continue
            
    def update_indicators(self, emas: Dict[int, float], dcf: Optional[float] = None, rsi: Optional[float] = None) -> None:
        """외부에서 계산된 지표 주입 (Warm-up 또는 정기 갱신)"""
        if emas:
            # 모든 키를 정수로 변환하여 저장
            processed_emas = {}
            for k, v in emas.items():
                if v is None: continue # None 값 건너뛰기
                try:
                    processed_emas[int(k)] = float(v)
                except:
                    continue
            self.ema.update(processed_emas)
        if dcf is not None:
            self.dcf_value = dcf
        if rsi is not None:
            self.rsi = rsi

    @property
    def is_undervalued(self) -> bool:
        """DCF 대비 저평가 여부"""
        return self.current_price < self.dcf_value if self.dcf_value > 0 else False

    @property
    def is_ready(self) -> bool:
        """매매 점수 계산을 위한 기초 데이터가 모두 준비되었는지 여부"""
        # 1. 시세 데이터 체크
        if self.current_price <= 0: return False
        
        # 2. 필수 기술적 지표 체크 (RSI 필수)
        if self.rsi <= 0: return False
        
        # 3. EMA 체크 (200일선이 없더라도 120선이나 60선이 있으면 분석 가능하다고 판단)
        # KIS API 기본 반환 건수(100건) 제한으로 인해 EMA 200이 누락되는 상황 대응
        ema_val = self.ema.get(200) or self.ema.get(120) or self.ema.get(60)
        if not ema_val or ema_val <= 0: return False
        
        return True
