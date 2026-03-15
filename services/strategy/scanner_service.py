"""Market scanning service. Detects oversold, trend breakout, and analyst strong-buy opportunities focused on US stocks."""
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

# Scan condition constants
SCAN_RSI_OVERSOLD_MAX = 35
SCAN_MIN_MCAP_USD = 50_000_000_000
SCAN_MAX_PBR_BLUECHIP = 8
SCAN_ANALYST_UPSIDE_RATIO = 1.3
SCAN_HISTORY_DAYS = 365
SCAN_REQUEST_DELAY_SEC = 0.5


class ScannerService:
    """Major stock scanning and buy/trend opportunity detection (KIS API + technical/fundamental indicators)."""

    @classmethod
    def _fetch_ticker_scan_data(cls, ticker: str, token: str) -> Optional[dict]:
        """가격/역사/지표 일괄 조회. 조회 실패 시 None 반환."""
        price_info = KisFetcher.fetch_overseas_price(token, ticker)
        if not price_info:
            return None
        current_price = price_info.get("price", 0)
        if not current_price:
            return None
        hist = DataService.get_price_history(ticker, days=SCAN_HISTORY_DAYS)
        if hist.empty:
            return None
        indicators_snapshot = IndicatorService.compute_latest_indicators_snapshot(hist["Close"])
        return {
            "current_price": current_price,
            "rsi": indicators_snapshot.rsi if indicators_snapshot else 50,
            "ema200": (indicators_snapshot.ema.get(200) if indicators_snapshot else None) or 0,
            "prev_close": hist["Close"].iloc[-2] if len(hist) > 1 else current_price,
            "pbr": price_info.get("pbr", 0),
            "market_cap": price_info.get("market_cap", 0),
            "analyst_target_price": (price_info.get("raw") or {}).get("target_mean_price"),
            "name": price_info.get("name", ticker),
        }

    @classmethod
    def _check_oversold_candidate(cls, ticker: str, data: dict, oversold: list) -> None:
        """RSI 과매도 + 대형주 + PBR 조건 충족 시 oversold 리스트에 추가."""
        if data["rsi"] >= SCAN_RSI_OVERSOLD_MAX:
            return
        pbr = data["pbr"]
        if data["market_cap"] > SCAN_MIN_MCAP_USD and pbr and pbr < SCAN_MAX_PBR_BLUECHIP:
            oversold.append(OversoldCandidate(
                ticker=ticker,
                price=data["current_price"],
                rsi=round(data["rsi"], 1),
                pbr=round(pbr, 2),
                name=data["name"],
            ))

    @classmethod
    def _check_trend_breakout_candidate(cls, ticker: str, data: dict, trend_breakout: list) -> None:
        """EMA200 상향 돌파 조건 충족 시 trend_breakout 리스트에 추가."""
        ema200 = data["ema200"]
        current_price = data["current_price"]
        prev_close = data["prev_close"]
        if ema200 > 0 and prev_close < ema200 and current_price > ema200:
            change_pct = round((current_price - prev_close) / prev_close * 100, 1)
            trend_breakout.append(TrendBreakoutCandidate(
                ticker=ticker,
                price=current_price,
                ema200=round(ema200, 2),
                change=change_pct,
            ))

    @classmethod
    def _check_analyst_candidate(cls, ticker: str, data: dict, analyst_strong_buy: list) -> None:
        """애널리스트 목표가 > 현재가 * 기준 배율 충족 시 analyst_strong_buy 리스트에 추가."""
        target = data["analyst_target_price"]
        current_price = data["current_price"]
        if target and target > current_price * SCAN_ANALYST_UPSIDE_RATIO:
            upside = (target - current_price) / current_price * 100
            analyst_strong_buy.append(AnalystStrongBuyCandidate(
                ticker=ticker,
                price=current_price,
                target=target,
                upside=round(upside, 1),
                name=data["name"],
            ))

    @classmethod
    def _evaluate_single_ticker(cls, ticker: str, token: str, oversold: list, trend_breakout: list, analyst_strong_buy: list):
        """Evaluate a single ticker and append candidates to the respective lists."""
        data = cls._fetch_ticker_scan_data(ticker, token)
        if data is None:
            return
        cls._check_oversold_candidate(ticker, data, oversold)
        cls._check_trend_breakout_candidate(ticker, data, trend_breakout)
        cls._check_analyst_candidate(ticker, data, analyst_strong_buy)

    @classmethod
    def scan_market(cls, limit: int = 20) -> ScanOpportunitiesResult:
        """Scan top US stocks and return oversold blue-chips, trend breakouts, and analyst strong-buy candidates."""
        tickers = DataService.get_top_us_tickers(limit=limit)
        oversold: list = []
        trend_breakout: list = []
        analyst_strong_buy: list = []

        token = KisService.get_access_token()
        for ticker in tickers:
            try:
                cls._evaluate_single_ticker(ticker, token, oversold, trend_breakout, analyst_strong_buy)
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
