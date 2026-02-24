"""시장 스캔 서비스. 미국 주식 중심으로 과매도·추세돌파·기관매수 기회를 탐지합니다."""
import time
from typing import Optional

from models.schemas import (
    AnalystStrongBuyCandidate,
    OversoldCandidate,
    ScanOpportunitiesResult,
    TrendBreakoutCandidate,
)
from services.analysis.indicator_service import IndicatorService
from services.kis.fetch.kis_fetcher import KisFetcher
from services.kis.kis_service import KisService
from services.market.data_service import DataService
from utils.logger import get_logger

logger = get_logger("scanner_service")

# 스캔 조건 상수
SCAN_RSI_OVERSOLD_MAX = 35
SCAN_MIN_MCAP_USD = 50_000_000_000
SCAN_MAX_PBR_BLUECHIP = 8
SCAN_ANALYST_UPSIDE_RATIO = 1.3
SCAN_HISTORY_DAYS = 365
SCAN_REQUEST_DELAY_SEC = 0.5


class ScannerService:
    """주요 종목 스캔 및 매수/추세 기회 탐지 (KIS API + 기술·기본 지표)."""

    @classmethod
    def scan_market(cls, limit: int = 20) -> ScanOpportunitiesResult:
        """미국 상위 종목을 스캔해 과매도 우량주·추세 돌파·기관 강매 후보를 반환합니다."""
        tickers = DataService.get_top_us_tickers(limit=limit)
        oversold: list = []
        trend_breakout: list = []
        analyst_strong_buy: list = []

        token = KisService.get_access_token()
        for ticker in tickers:
            try:
                price_info = KisFetcher.fetch_overseas_price(token, ticker)
                if not price_info:
                    continue
                current_price = price_info.get("price", 0)
                if not current_price:
                    continue

                hist = DataService.get_price_history(ticker, days=SCAN_HISTORY_DAYS)
                if hist.empty:
                    continue
                indicators_snapshot = IndicatorService.compute_latest_indicators_snapshot(hist["Close"])
                rsi = indicators_snapshot.rsi if indicators_snapshot else 50
                ema200 = (indicators_snapshot.ema.get(200) if indicators_snapshot else None) or 0
                prev_close = hist["Close"].iloc[-2] if len(hist) > 1 else current_price

                pbr = price_info.get("pbr", 0)
                market_cap = price_info.get("market_cap", 0)
                analyst_target_price = (price_info.get("raw") or {}).get("target_mean_price")
                name = price_info.get("name", ticker)

                if rsi < SCAN_RSI_OVERSOLD_MAX:
                    if market_cap > SCAN_MIN_MCAP_USD and pbr and pbr < SCAN_MAX_PBR_BLUECHIP:
                        oversold.append(
                            OversoldCandidate(
                                ticker=ticker,
                                price=current_price,
                                rsi=round(rsi, 1),
                                pbr=round(pbr, 2),
                                name=name,
                            )
                        )
                if ema200 > 0 and prev_close < ema200 and current_price > ema200:
                    change_pct = round((current_price - prev_close) / prev_close * 100, 1)
                    trend_breakout.append(
                        TrendBreakoutCandidate(
                            ticker=ticker,
                            price=current_price,
                            ema200=round(ema200, 2),
                            change=change_pct,
                        )
                    )
                if analyst_target_price and analyst_target_price > current_price * SCAN_ANALYST_UPSIDE_RATIO:
                    upside = (analyst_target_price - current_price) / current_price * 100
                    analyst_strong_buy.append(
                        AnalystStrongBuyCandidate(
                            ticker=ticker,
                            price=current_price,
                            target=analyst_target_price,
                            upside=round(upside, 1),
                            name=name,
                        )
                    )

                print(".", end="", flush=True)
                time.sleep(SCAN_REQUEST_DELAY_SEC)
            except Exception:
                continue

        print("\n✅ Scan complete.")
        return ScanOpportunitiesResult(
            oversold_bluechip=oversold,
            trend_breakout=trend_breakout,
            analyst_strong_buy=analyst_strong_buy,
        )
