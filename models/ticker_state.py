from dataclasses import dataclass
from typing import Dict, List, Optional
from collections import deque
import logging

from utils.logger import get_logger

logger = get_logger("ticker_state")

@dataclass
class TickerState:
    ticker: str
    current_price: float = 0.0
    open_price: float = 0.0
    high_price: float = 0.0
    low_price: float = 0.0
    prev_close: float = 0.0  # ?꾩씪 醫낃?
    volume: int = 0
    change_rate: float = 0.0 # ?깅씫瑜?(%)
    
    # 吏?쒕뱾
    ema: Dict[int, float] = None # {5: 1000, 20: 950, ...}
    rsi: float = 0.0             # RSI (14)
    bollinger: Dict[str, float] = None # {upper, middle, lower}
    dcf_value: float = 0.0       # ?곸젙二쇨? (DCF)
    
    # ?곗씠??踰꾪띁 (理쒓렐 N媛쒖쓽 醫낃?, ?ㅼ떆媛?EMA 怨꾩궛??
    # ?ㅼ젣濡쒕뒗 ?쇰큺 ?곗씠??濡쒕뵫 ?? ?ㅼ떆媛?媛寃⑹씠 蹂????'?ㅻ뒛??醫낃?(?꾩옱媛)'濡?媛?뺥븯怨?EMA瑜??ш퀎?고븯??諛⑹떇???쇰컲??
    # ?먮뒗 遺꾨큺 湲곗??대씪硫?遺꾨큺 ?꾩꽦 ?쒖젏???뺤젙. ?ш린?쒕뒗 '?쇰큺 湲곗? ?ㅼ떆媛?EMA'瑜?異붿젙?쒕떎怨?媛??
    
    def __post_init__(self):
        if self.ema is None:
            self.ema = {}
        if self.bollinger is None:
            self.bollinger = {}

    def update_from_socket(self, data_dict: dict):
        """
        WebSocket ?섏떊 ?곗씠?곕줈 ?곹깭 ?낅뜲?댄듃
        data_dict: KIS Websocket H0STCNT0 ?щ㎎ ?뚯떛 寃곌낵
        """
        try:
            # KIS ?ㅼ떆媛?泥닿껐媛 ?곗씠??留ㅽ븨
            # H0STCNT0: ?좉?利앷텒 ?⑥텞 醫낅ぉ肄붾뱶(0), ?곸뾽?쒓컙(1), ?꾩옱媛(2), ?꾩씪?鍮꾧뎄遺?3), ?꾩씪?鍮?4), ?꾩씪?鍮꾩쑉(5)... ?쒓?(7), 怨좉?(8), ?媛(9)...
            
            # ?뚯떛 濡쒖쭅? ?몃??먯꽌 泥섎━?댁꽌 源붾걫??dict濡??섍꺼二쇰뒗 寃?醫뗭쓬
            # ?? {'price': 70000, 'rate': 1.5, 'open': 69000, ...}
            
            new_price = float(data_dict.get('mksc_shrn_iscd', 0)) # ?ㅼ떆媛?泥닿껐媛??2踰덉㎏媛 ?꾨땲???뚯떛??dict ???ъ슜
            # 二쇱쓽: WebSocket raw data ?뚯떛? Service ?덈꺼?먯꽌 ?섑뻾?섍퀬 ?ш린??媛믩쭔 ?꾨떖
            
            self.current_price = float(data_dict.get('stck_prpr', self.current_price)) # ?꾩옱媛
            self.open_price = float(data_dict.get('stck_oprc', self.open_price))       # ?쒓?
            self.high_price = float(data_dict.get('stck_hgpr', self.high_price))       # 怨좉?
            self.low_price = float(data_dict.get('stck_lwpr', self.low_price))         # ?媛
            
            # ?꾩씪 ?鍮꾩쑉
            self.change_rate = float(data_dict.get('rt_cd', 0.0)) 
            
            # 嫄곕옒??(?꾩쟻 嫄곕옒??
            self.volume = int(data_dict.get('acml_vol', self.volume))
            
            # ?꾩씪 醫낃???蹂댄넻 蹂꾨룄 議고쉶 ?꾩슂 (?ㅼ떆媛??곗씠?곗뿉???ы븿?????덉쑝??怨꾩궛?⑹쑝濡?誘몃━ ?명똿 沅뚯옣)
            
            # 吏???ㅼ떆媛??낅뜲?댄듃
            self.recalculate_indicators()
            
        except Exception as e:
            logger.error(f"Error updating ticker state: {e}")

    def recalculate_indicators(self):
        """?꾩옱媛瑜?湲곗??쇰줈 ?ㅼ떆媛?吏??EMA ?? ?ш퀎??""
        if not self.ema or self.current_price <= 0:
            return
            
        # ?쇰큺 湲곗? ?ㅼ떆媛?EMA 異붿젙
        # EMA_today = (Price_today * alpha) + (EMA_yesterday * (1 - alpha))
        for n, prev_ema_val in list(self.ema.items()):
            try:
                # ?ㅺ? ?뺤닔??寃쎌슦?먮쭔 ?섑뻾 (?? 5, 20, 100...)
                period = int(n)
                alpha = 2 / (period + 1)
                self.ema[period] = round((self.current_price * alpha) + (prev_ema_val * (1 - alpha)), 2)
            except (ValueError, TypeError):
                continue
            
    def update_indicators(self, emas: Dict[int, float], dcf: float = None, rsi: float = None):
        """?몃??먯꽌 怨꾩궛??吏??二쇱엯 (Warm-up ?먮뒗 ?뺢린 媛깆떊??"""
        if emas:
            # 紐⑤뱺 ?ㅻ? ?뺤닔濡?蹂?섑븯?????
            processed_emas = {}
            for k, v in emas.items():
                try:
                    processed_emas[int(k)] = v
                except:
                    continue
            self.ema.update(processed_emas)
        if dcf is not None:
            self.dcf_value = dcf
        if rsi is not None:
            self.rsi = rsi

    @property
    def is_undervalued(self) -> bool:
        """DCF ?鍮???됯? ?щ?"""
        return self.current_price < self.dcf_value if self.dcf_value > 0 else False
