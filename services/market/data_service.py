import pandas as pd
import numpy as np
import time
import requests
from datetime import datetime
from config import Config
from utils.logger import get_logger
from utils.market import is_kr
from services.kis.kis_service import KisService
from services.kis.fetch.kis_fetcher import KisFetcher
from services.market.stock_meta_service import StockMetaService
from services.analysis.indicator_service import IndicatorService
from services.analysis.financial_service import FinancialService
from services.analysis.dcf_service import DcfService
from services.market.market_hour_service import MarketHourService

logger = get_logger("data_service")

# Constants: column names
COL_CLOSE = "Close"
COL_HIGH = "High"
COL_LOW = "Low"
COL_OPEN = "Open"
COL_DATE = "Date"
# KIS API limits
KIS_RATE_LIMIT_SLEEP_SEC = 0.5
KIS_HISTORY_BATCH_LIMIT = 100
HISTORY_DAYS_DEFAULT = 365
# Fallback tickers when domestic ranking fails
KR_FALLBACK_TICKERS = ["005930", "000660", "373220", "207940", "005380", "005490", "035420", "000270", "051910", "105560"]
KR_FALLBACK_MINIMAL = ["005930", "000660", "373220", "207940", "005380"]
# FDR index symbol mapping
FDR_INDEX_SYMBOL_MAP = {"SPX": "US500", "NAS": "IXIC", "DJI": "DJI", "VIX": "VIX"}


class DataService:
    """KIS API-based data collection and indicator calculation service.
    - Index data falls back to FinanceDataReader when KIS fails.
    """

    @classmethod
    def _is_fund_like_security(cls, ticker: str, name: str, market: str) -> bool:
        """Check if ticker is an ETF/ETN/fund-like product."""
        t = str(ticker or "").strip().upper()
        n = str(name or "").strip().upper()
        m = str(market or "").strip().upper()

        if m == "KR":
            kr_keywords = [
                "ETF", "ETN", "인버스", "레버리지", "TRF", "TDF",
                "KODEX", "TIGER", "KINDEX", "KBSTAR", "ARIRANG",
                "KOSEF", "HANARO", "SOL", "ACE", "RISE"
            ]
            return any(k.upper() in n for k in kr_keywords)

        # US
        us_name_keywords = [
            " ETF", " ETN", " FUND", " TRUST", " INDEX FUND",
            " ULTRASHORT", " ULTRA ", " BULL ", " BEAR "
        ]
        if any(k in n for k in us_name_keywords):
            return True

        # Blocklist of known US ETF tickers for empty/uncertain names
        us_etf_tickers = {
            "SPY", "IVV", "VOO", "VTI", "QQQ", "QQQM", "DIA", "IWM", "EFA", "EEM",
            "TLT", "IEF", "BND", "BNDX", "VCIT", "SMH", "VXUS", "IXUS", "IBIT"
        }
        return t in us_etf_tickers

    # US fallback ticker list constants
    _US_FALLBACK_CORE = [
        "AAPL", "NVDA", "MSFT", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "COST", "NFLX",
        "JPM", "V", "LLY", "XOM", "UNH"
    ]
    _US_FALLBACK_EXTENDED = [
        "GOOG", "BRK/B", "WMT", "MA", "ORCL", "HD", "BAC", "PG", "JNJ", "ABBV",
        "KO", "PEP", "MRK", "CVX", "AMD", "ADBE", "CRM", "CSCO", "INTC", "T",
        "VZ", "PFE", "ABT", "CMCSA", "QCOM", "MCD", "NKE", "TXN", "DHR", "WFC",
        "DIS", "AMGN", "UNP", "LOW", "NEE", "IBM", "PM", "RTX", "SPGI", "CAT",
        "GS", "HON", "INTU", "BKNG", "BLK", "AXP", "PLD", "LMT", "TMO", "MDT",
        "SYK", "DE", "TJX", "GILD", "ADP", "ISRG", "C", "SCHW", "CB",
        "ETN", "SO", "CI", "DUK", "PGR", "ELV", "ZTS", "BDX", "MU", "KLAC",
        "SNPS", "PANW", "AMAT", "LRCX", "MELI", "SBUX", "REGN", "VRTX", "NOW", "UBER",
        "SHOP", "CRWD", "DASH", "PYPL", "XYZ", "TTD", "ROKU", "BIDU", "PDD", "NTES",
        "ASML", "TMUS", "NDAQ", "EA", "ADSK", "ORLY", "MAR", "CEG", "FANG", "CSX",
        "AEP", "MNST", "MRVL", "NXPI", "IDXX", "FTNT", "ABNB", "WBD", "CME", "PCAR",
        "XEL", "MCHP", "CTAS", "FAST", "ARGX", "ALNY", "STX", "HOOD", "SNY", "ARM"
    ]
    _US_NYSE_SYMBOLS = {
        "JPM", "V", "LLY", "XOM", "UNH", "BRK/B", "WMT", "MA", "HD", "BAC", "PG", "JNJ",
        "ABBV", "KO", "PEP", "MRK", "CVX", "T", "VZ", "PFE", "ABT", "MCD", "NKE", "DHR",
        "WFC", "DIS", "AMGN", "UNP", "LOW", "NEE", "IBM", "PM", "RTX", "SPGI", "CAT",
        "GS", "HON", "BLK", "AXP", "PLD", "LMT", "TMO", "MDT", "SYK", "DE", "TJX",
        "GILD", "C", "SCHW", "CB", "ETN", "SO", "CI", "DUK", "PGR", "ELV",
        "ZTS", "BDX", "UBER", "TMUS", "NDAQ", "CME", "PCAR", "XEL", "CEG", "FANG", "CSX", "AEP"
    }

    @classmethod
    def _parse_us_fallback_ticker_row(cls, sym_candidate: str, seen: set) -> tuple:
        """Convert a single fallback symbol candidate to (sym, excd, ex_name) tuple. Returns None if invalid."""
        sym = str(sym_candidate).strip().upper()
        if not sym or sym in seen:
            return None
        seen.add(sym)
        if cls._is_fund_like_security(sym, sym, "US"):
            return None
        excd = "NYS" if sym in cls._US_NYSE_SYMBOLS else "NAS"
        ex_name = "NYSE" if excd == "NYS" else "NASD"
        return (sym, excd, ex_name)

    @classmethod
    def _build_us_fallback_data(cls, limit: int = 100) -> list:
        """Build fallback US ticker list for ranking API failure (up to limit)."""
        ordered = []
        seen = set()
        for sym_candidate in cls._US_FALLBACK_CORE + cls._US_FALLBACK_EXTENDED:
            row = cls._parse_us_fallback_ticker_row(sym_candidate, seen)
            if row is not None:
                ordered.append(row)
            if len(ordered) >= limit:
                break
        return ordered

    @classmethod
    def _parse_krx_ticker_from_row(cls, item: dict, tr_id: str, path: str) -> str:
        """Extract ticker from KRX ranking response item + upsert StockMeta. Returns None if invalid."""
        ticker = item.get("mksc_shrn_iscd")
        name = item.get("hts_kor_isnm")
        if not ticker or cls._is_fund_like_security(ticker, name, "KR"):
            return None
        StockMetaService.upsert_stock_meta(
            ticker=ticker, name_ko=name, market_type="KR",
            exchange_code="KRX", api_path=path, api_tr_id=tr_id, api_market_code="J",
        )
        return ticker

    @classmethod
    def _supplement_kr_tickers(cls, tickers: list, limit: int) -> list:
        """Supplement KRX tickers from DB meta if below limit."""
        if len(tickers) >= limit:
            return tickers
        try:
            existing = set(tickers)
            supplement = StockMetaService.get_kr_individual_stocks(
                existing=existing, limit=limit - len(tickers),
            )
            tickers.extend(supplement)
        except Exception as ex:
            logger.warning(f"⚠️ KR fallback supplement from DB failed: {ex}")
        return tickers

    @classmethod
    def get_top_krx_tickers(cls, limit: int = 100) -> list:
        """Get top KRX stocks by market cap via KIS API."""
        try:
            token = KisService.get_access_token()
            response = KisFetcher.fetch_domestic_ranking(token)
            tickers = []
            if response.get("output"):
                tr_id, path = StockMetaService.get_api_info("주식현재가_시세")
                for item in response["output"]:
                    ticker = cls._parse_krx_ticker_from_row(item, tr_id, path)
                    if ticker:
                        tickers.append(ticker)
                    if len(tickers) >= limit:
                        break
            if not tickers:
                tickers = list(KR_FALLBACK_TICKERS)
                logger.info(f"⚠️ KRX ranking empty. Using fallback list: {len(tickers)} tickers.")
            return cls._supplement_kr_tickers(tickers, limit)
        except Exception as e:
            logger.error(f"Error fetching top KRX tickers via KIS: {e}")
            return list(KR_FALLBACK_MINIMAL)

    @classmethod
    def _fetch_us_tickers_from_kis(cls, limit: int) -> list:
        """Fetch US tickers+meta from KIS NAS+NYS ranking API, return top by market cap."""
        token = KisService.get_access_token()
        response_nas = KisFetcher.fetch_overseas_ranking(token, excd="NAS")
        response_nys = KisFetcher.fetch_overseas_ranking(token, excd="NYS")
        combined = []
        for response, excd in [(response_nas, "NAS"), (response_nys, "NYS")]:
            if response.get("output"):
                for item in response["output"]:
                    ticker = item.get("symb")
                    name = item.get("hname")
                    if cls._is_fund_like_security(ticker, name, "US"):
                        continue
                    combined.append({"ticker": ticker, "name": name, "excd": excd, "mcap": float(item.get("mcap", 0))})
        combined.sort(key=lambda x: x["mcap"], reverse=True)
        tr_id, path = StockMetaService.get_api_info("해외주식_상세시세")
        tickers = []
        for item in combined[:limit]:
            ticker = item["ticker"]
            if ticker:
                tickers.append(ticker)
                StockMetaService.upsert_stock_meta(
                    ticker=ticker, name_ko=item["name"], market_type="US",
                    exchange_code="NASD" if item["excd"] == "NAS" else "NYSE",
                    api_path=path, api_tr_id=tr_id, api_market_code=item["excd"],
                )
        return tickers

    @classmethod
    def _apply_us_ticker_supplements(cls, tickers: list, limit: int) -> list:
        """Supplement with fallback list or fill entirely if tickers is empty."""
        tr_id, path = StockMetaService.get_api_info("해외주식_상세시세")
        if not tickers:
            fallback_data = cls._build_us_fallback_data(limit=limit)
            for fallback_ticker, excd, ex_name in fallback_data:
                tickers.append(fallback_ticker)
                StockMetaService.upsert_stock_meta(
                    ticker=fallback_ticker, name_ko=fallback_ticker, market_type="US",
                    exchange_code=ex_name, api_path=path, api_tr_id=tr_id, api_market_code=excd,
                )
            logger.info(f"⚠️ US ranking empty. Using fallback list: {len(tickers)} tickers with metadata.")
            return tickers
        if len(tickers) < limit:
            existing = set(tickers)
            for fallback_ticker, excd, ex_name in cls._build_us_fallback_data(limit=limit * 2):
                if len(tickers) >= limit:
                    break
                if fallback_ticker in existing:
                    continue
                existing.add(fallback_ticker)
                tickers.append(fallback_ticker)
                StockMetaService.upsert_stock_meta(
                    ticker=fallback_ticker, name_ko=fallback_ticker, market_type="US",
                    exchange_code=ex_name, api_path=path, api_tr_id=tr_id, api_market_code=excd,
                )
        return tickers

    @classmethod
    def get_top_us_tickers(cls, limit: int = 100) -> list:
        """Get top US stocks by market cap via KIS API."""
        try:
            tickers = cls._fetch_us_tickers_from_kis(limit)
            return cls._apply_us_ticker_supplements(tickers, limit)
        except Exception as e:
            logger.error(f"Error fetching top US tickers via KIS: {e}")
            return [ft for ft, _, _ in cls._build_us_fallback_data(limit=limit)]

    @classmethod
    def _fetch_kr_price_history(cls, ticker: str, token: str, start_date: str, end_date: str) -> pd.DataFrame:
        """Fetch domestic stock daily OHLCV via KIS API and return DataFrame."""
        response = KisFetcher.fetch_daily_price(token, ticker, start_date, end_date)
        if not response or not response.get("output2"):
            return pd.DataFrame()
        df = pd.DataFrame(response["output2"])
        return df.rename(columns={
            "stck_clpr": COL_CLOSE, "stck_hgpr": COL_HIGH,
            "stck_lwpr": COL_LOW, "stck_oprc": COL_OPEN, "stck_bsop_date": COL_DATE,
        })

    @classmethod
    def _fetch_us_price_history(cls, ticker: str, token: str, start_date: str, end_date: str, days: int) -> pd.DataFrame:
        """Fetch overseas stock/index daily OHLCV via KIS API and return DataFrame. Includes FDR fallback for indices."""
        response = KisFetcher.fetch_overseas_daily_price(token, ticker, start_date, end_date)
        if ticker in ["SPX", "NAS", "VIX", "DJI"]:
            rows = response.get("output2") or response.get("output") or []
            df = pd.DataFrame(rows) if rows else pd.DataFrame()
            if not df.empty:
                if "stck_clpr" in df.columns:
                    df = df.rename(columns={"stck_clpr": COL_CLOSE, "stck_hgpr": COL_HIGH, "stck_lwpr": COL_LOW, "stck_oprc": COL_OPEN, "stck_bsop_date": COL_DATE})
                elif "clos" in df.columns:
                    df = df.rename(columns={"clos": COL_CLOSE, "high": COL_HIGH, "low": COL_LOW, "open": COL_OPEN, "xymd": COL_DATE})
                else:
                    df = df.rename(columns={"last": COL_CLOSE, "high": COL_HIGH, "low": COL_LOW, "open": COL_OPEN, "xymd": COL_DATE})
            if df.empty or COL_CLOSE not in df.columns:
                df = cls._fallback_index_history_fdr(ticker, days)
            return df
        if not response.get("output"):
            return pd.DataFrame()
        df = pd.DataFrame(response["output"])
        if "clos" in df.columns:
            return df.rename(columns={"clos": COL_CLOSE, "high": COL_HIGH, "low": COL_LOW, "open": COL_OPEN, "xymd": COL_DATE})
        return df.rename(columns={"last": COL_CLOSE, "high": COL_HIGH, "low": COL_LOW, "open": COL_OPEN, "xymd": COL_DATE})

    @classmethod
    def _extend_price_history_batch(
        cls, df: pd.DataFrame, ticker: str, token: str, start_date: str, days: int
    ) -> pd.DataFrame:
        """Extend df by fetching additional data before oldest date when batch limit (100) is reached."""
        from datetime import timedelta
        df[COL_DATE] = pd.to_datetime(df[COL_DATE])
        new_end_date = (df[COL_DATE].min() - timedelta(days=1)).strftime("%Y%m%d")
        logger.info(f"Fetching additional 100 rows for {ticker} (End Date: {new_end_date})")
        if is_kr(ticker):
            df2 = cls._fetch_kr_price_history(ticker, token, start_date, new_end_date)
        else:
            df2 = cls._fetch_us_price_history(ticker, token, start_date, new_end_date, days)
        if not df2.empty and COL_CLOSE in df2.columns:
            df = pd.concat([df, df2], ignore_index=True)
        return df

    @classmethod
    def get_price_history(cls, ticker: str, days: int = 300) -> pd.DataFrame:
        """Fetch past N days of price data via KIS API."""
        # Historical price queries are allowed regardless of market hours
        logger.info(f"Fetching history for {ticker} (Last {days} days)...")
        try:
            from datetime import timedelta
            token = KisService.get_access_token()
            end_date = datetime.now().strftime("%Y%m%d")
            start_date = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
            if is_kr(ticker):
                df = cls._fetch_kr_price_history(ticker, token, start_date, end_date)
            else:
                df = cls._fetch_us_price_history(ticker, token, start_date, end_date, days)
            if COL_CLOSE not in df.columns:
                logger.error(f"'Close' column missing for {ticker}. Columns: {df.columns.tolist()}")
                return pd.DataFrame()
            if len(df) >= KIS_HISTORY_BATCH_LIMIT and days > 150:
                try:
                    df = cls._extend_price_history_batch(df, ticker, token, start_date, days)
                except Exception as ex:
                    logger.warning(f"Failed to fetch additional rows for {ticker}: {ex}")
            df[COL_DATE] = pd.to_datetime(df[COL_DATE])
            df.set_index(COL_DATE, inplace=True)
            for col in [COL_CLOSE, COL_HIGH, COL_LOW, COL_OPEN]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            return df.sort_index()
        except Exception as e:
            logger.error(f"Error fetching history for {ticker} via KIS: {e}")
            return pd.DataFrame()

    @classmethod
    def _fallback_index_history_fdr(cls, ticker: str, days: int = 300) -> pd.DataFrame:
        """Fallback to FinanceDataReader when KIS index data fails."""
        try:
            import FinanceDataReader as fdr
        except Exception as e:
            logger.warning(f"⚠️ FinanceDataReader not available: {e}")
            return pd.DataFrame()
        
        symbol = FDR_INDEX_SYMBOL_MAP.get(ticker, ticker)
        
        try:
            from datetime import timedelta
            start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            df = fdr.DataReader(symbol, start_date)
            if df is None or df.empty:
                return pd.DataFrame()
            df = df.reset_index()
            if "Date" not in df.columns and len(df.columns) > 0:
                df = df.rename(columns={df.columns[0]: "Date"})
            return df.rename(columns={"Open": "Open", "High": "High", "Low": "Low", "Close": "Close"})
        except Exception as e:
            logger.warning(f"⚠️ FDR index fetch failed for {ticker}: {e}")
            return pd.DataFrame()

    @classmethod
    def _sync_ticker_market_data(cls, ticker: str, market: str, token: str) -> bool:
        """Fetch price/indicators/DCF for a single ticker and save to DB. True on success, False on skip/failure."""
        logger.info(f"Processing {ticker} ({market})...")
        if market == "KR":
            price_info = KisFetcher.fetch_domestic_price(token, ticker)
        else:
            price_info = KisFetcher.fetch_overseas_price(token, ticker)
        if not price_info:
            return False
        hist = cls.get_price_history(ticker, days=HISTORY_DAYS_DEFAULT)
        indicators = {}
        if not hist.empty:
            indicators = IndicatorService.get_latest_indicators(hist[COL_CLOSE])
        dcf_val = DcfService.calculate_dcf(ticker)
        metrics = {
            "current_price": price_info.get("price"),
            "market_cap": price_info.get("market_cap"),
            "per": price_info.get("per"),
            "pbr": price_info.get("pbr"),
            "eps": price_info.get("eps"),
            "bps": price_info.get("bps"),
            "rsi": indicators.get("rsi"),
            "ema": indicators.get("ema"),
            "dcf_value": dcf_val,
        }
        StockMetaService.save_financials(ticker, metrics)
        return True

    @classmethod
    def _get_holding_tickers(cls) -> list[tuple[str, str]]:
        """보유종목 ticker 목록을 (ticker, market) tuple 리스트로 반환."""
        try:
            from repositories.portfolio_repo import PortfolioRepo
            holdings = PortfolioRepo.load_holdings("sean")
            return [
                (h["ticker"], "KR" if is_kr(h["ticker"]) else "US")
                for h in holdings if h.get("ticker")
            ]
        except Exception as e:
            logger.warning(f"보유종목 조회 실패: {e}")
            return []

    @classmethod
    def sync_daily_market_data(cls, limit: int = 100) -> None:
        """Daily sync: collect top tickers + 보유종목 -> calculate indicators -> save to DB."""
        from services.market.market_hour_service import MarketHourService
        if MarketHourService.is_weekend():
            logger.info("🏖️ Weekend — skipping daily market data sync.")
            return
        logger.info(f"Starting daily market data sync (Top {limit} + holdings)...")
        kr_tickers = cls.get_top_krx_tickers(limit=limit)
        us_tickers = cls.get_top_us_tickers(limit=limit)
        base_tickers = [(t, "KR") for t in kr_tickers] + [(t, "US") for t in us_tickers]
        base_set = {t for t, _ in base_tickers}
        holding_pairs = [(t, m) for t, m in cls._get_holding_tickers() if t not in base_set]
        all_tickers = base_tickers + holding_pairs
        logger.info(f"  Top {limit} KR/US + {len(holding_pairs)} holding tickers = {len(all_tickers)} total")
        token = KisService.get_access_token()  # Fetch once outside the loop
        markets = {market for _, market in all_tickers}
        open_markets = {m for m in markets if MarketHourService.should_fetch(m)}
        for ticker, market in all_tickers:
            if market not in open_markets:
                continue
            try:
                cls._sync_ticker_market_data(ticker, market, token)
                time.sleep(KIS_RATE_LIMIT_SLEEP_SEC)
            except Exception as e:
                logger.error(f"Error syncing {ticker}: {e}")
        logger.info("Daily market data sync completed.")
