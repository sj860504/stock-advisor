from dataclasses import dataclass, field
from typing import Dict, List, Optional
from collections import deque
from datetime import datetime
import logging

from utils.logger import get_logger

logger = get_logger("ticker_state")

@dataclass
class TickerState:
    ticker: str
    name: str = ""                # Stock name
    current_price: float = 0.0
    open_price: float = 0.0
    high_price: float = 0.0
    low_price: float = 0.0
    prev_close: float = 0.0  # Previous day close
    volume: int = 0
    change_rate: float = 0.0 # Change rate (%)
    
    # Indicators
    ema: Dict[int, float] = field(default_factory=dict) # {5: 1000, 20: 950, ...}
    rsi: float = 0.0             # RSI (14)
    bollinger: Dict[str, float] = field(default_factory=dict) # {upper, middle, lower}
    dcf_value: float = 0.0       # Fair value (DCF)
    
    # Strategy target prices
    target_buy_price: float = 0.0  # Target entry price
    target_sell_price: float = 0.0 # Target sell price

    # Price last-updated timestamp
    last_updated: Optional[datetime] = None
    # RSI last-calculated timestamp (warm-up or periodic refresh)
    rsi_updated_at: Optional[datetime] = None

    # Data buffer (recent N closing prices for real-time EMA calculation)
    # In practice, after loading daily candle data, the current price is treated as today's close
    # and EMA is recalculated. For minute candles, values are finalized at candle close.
    # Here we estimate daily-basis real-time EMA.
    
    def __post_init__(self) -> None:
        # Using field(default_factory=dict), so explicit initialization is unnecessary
        pass

    def update_from_socket(self, data_dict: dict) -> None:
        """
        Update state from WebSocket received data.
        data_dict: Parsed result of KIS WebSocket H0STCNT0 format.
        """
        try:
            # KIS real-time execution data mapping
            # H0STCNT0: stock code(0), time(1), current price(2), change sign(3), change(4), change rate(5)... open(7), high(8), low(9)...

            # Parsing logic should be handled externally and passed as a clean dict
            # e.g.: {'price': 70000, 'rate': 1.5, 'open': 69000, ...}
            
            new_price = float(data_dict.get('mksc_shrn_iscd', 0)) # Use parsed dict key, not raw position
            # Note: WebSocket raw data parsing is performed at the Service level; only values are passed here
            
            self.current_price = float(data_dict.get('stck_prpr', self.current_price)) # Current price
            self.open_price = float(data_dict.get('stck_oprc', self.open_price))       # Open price
            self.high_price = float(data_dict.get('stck_hgpr', self.high_price))       # High price
            self.low_price = float(data_dict.get('stck_lwpr', self.low_price))         # Low price

            # Day-over-day change rate
            self.change_rate = float(data_dict.get('rt_cd', 0.0)) 
            
            # Volume (cumulative)
            self.volume = int(data_dict.get('acml_vol', self.volume))
            
            # Previous close usually requires a separate lookup (may be in real-time data, but pre-setting recommended)
            
            # Real-time indicator update
            self.recalculate_indicators()
            
        except Exception as e:
            logger.error(f"Error updating ticker state: {e}")

    def recalculate_indicators(self) -> None:
        """Recalculate real-time indicators (EMA, etc.) based on current price."""
        if not self.ema or self.current_price <= 0:
            return
            
        # Daily-basis real-time EMA estimation
        # EMA_today = (Price_today * alpha) + (EMA_yesterday * (1 - alpha))
        for n, prev_ema_val in list(self.ema.items()):
            try:
                # Only process integer keys (e.g.: 5, 20, 100...)
                period = int(n)
                alpha = 2 / (period + 1)
                self.ema[period] = round((self.current_price * alpha) + (prev_ema_val * (1 - alpha)), 2)
            except (ValueError, TypeError):
                continue
            
    def update_indicators(self, emas: Dict[int, float], dcf: Optional[float] = None, rsi: Optional[float] = None) -> None:
        """Inject externally calculated indicators (warm-up or periodic refresh)."""
        if emas:
            # Convert all keys to int before storing
            processed_emas = {}
            for k, v in emas.items():
                if v is None: continue # Skip None values
                try:
                    processed_emas[int(k)] = float(v)
                except:
                    continue
            self.ema.update(processed_emas)
        if dcf is not None:
            self.dcf_value = dcf
        if rsi is not None:
            self.rsi = rsi
            self.rsi_updated_at = datetime.now()

    @property
    def is_undervalued(self) -> bool:
        """Whether undervalued relative to DCF fair value."""
        return self.current_price < self.dcf_value if self.dcf_value > 0 else False

    @property
    def is_ready(self) -> bool:
        """Whether all base data required for trade score calculation is ready."""
        # 1. Price data check
        if self.current_price <= 0: return False
        
        # 2. Required technical indicator check (RSI is mandatory)
        if self.rsi <= 0: return False
        
        # 3. EMA check (analysis possible with EMA120 or EMA60 even if EMA200 is missing)
        # Handles cases where EMA200 is unavailable due to KIS API default limit (100 records)
        ema_val = self.ema.get(200) or self.ema.get(120) or self.ema.get(60)
        if not ema_val or ema_val <= 0: return False
        
        return True
