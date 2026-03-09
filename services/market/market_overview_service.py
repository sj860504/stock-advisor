"""Market index and exchange rate overview service."""
import logging
from datetime import datetime
from typing import Tuple

import pandas as pd

from services.kis.fetch.kis_fetcher import KisFetcher
from services.kis.kis_service import KisService
from utils.logger import get_logger

logger = get_logger("market_overview_service")

# (symbol, exchange_code)
INDEX_TICKERS: dict[str, Tuple[str, str]] = {
    "KOSPI": ("0001", "KRX"),
    "KOSDAQ": ("1001", "KRX"),
    "S&P 500": ("SPX", "IDX"),
    "NASDAQ 100": ("NAS", "IDX"),
    "USD/KRW": ("FX@KRW", "IDX"),
}
EXCHANGE_KRX = "KRX"
COL_INDEX = "Index"
COL_PRICE = "Price"
COL_CHANGE = "Change"
COL_CHANGE_RATE = "ChangeRate"


class MarketOverviewService:
    """Summary view of major indices (KOSPI, KOSDAQ, S&P500, etc.) and exchange rates."""

    INDEX_TICKERS = INDEX_TICKERS

    @classmethod
    def get_market_summary(cls) -> pd.DataFrame:
        """Return DataFrame with current price, change, and change rate for major indices."""
        token = KisService.get_access_token()
        index_rows = []
        for name, (symb, excd) in cls.INDEX_TICKERS.items():
            try:
                if excd == EXCHANGE_KRX:
                    response = KisFetcher.fetch_domestic_price(token, symb)
                else:
                    response = KisFetcher.fetch_overseas_price(
                        token, symb, meta={"api_market_code": excd}
                    )
                price = response.get("price", 0)
                change = response.get("change", 0)
                pct_change = response.get("change_rate", 0)
                index_rows.append({
                    COL_INDEX: name,
                    COL_PRICE: price,
                    COL_CHANGE: change,
                    COL_CHANGE_RATE: pct_change,
                })
            except Exception as e:
                logger.error(f"Error fetching {name}: {e}")
                index_rows.append({COL_INDEX: name, COL_PRICE: 0, COL_CHANGE: 0, COL_CHANGE_RATE: 0})
        return pd.DataFrame(index_rows)

    @classmethod
    def print_summary(cls) -> None:
        """Print market summary to console."""
        df = cls.get_market_summary()
        print(f"=== MARKET OVERVIEW ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')}) ===")
        display_data = [
            {
                COL_INDEX: row[COL_INDEX],
                "Price": f"{row[COL_PRICE]:,.2f}",
                "Change": f"{row[COL_CHANGE]:+,.2f}",
                "Change (%)": f"{row[COL_CHANGE_RATE]:+.2f}%",
            }
            for _, row in df.iterrows()
        ]
        print(pd.DataFrame(display_data).to_markdown(index=False))
