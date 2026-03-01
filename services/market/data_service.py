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

# 상수: 컬럼명
COL_CLOSE = "Close"
COL_HIGH = "High"
COL_LOW = "Low"
COL_OPEN = "Open"
COL_DATE = "Date"
# KIS API 제한
KIS_RATE_LIMIT_SLEEP_SEC = 0.5
KIS_HISTORY_BATCH_LIMIT = 100
HISTORY_DAYS_DEFAULT = 365
# 국내 랭킹 실패 시 기본 종목
KR_FALLBACK_TICKERS = ["005930", "000660", "373220", "207940", "005380", "005490", "035420", "000270", "051910", "105560"]
KR_FALLBACK_MINIMAL = ["005930", "000660", "373220", "207940", "005380"]
# FDR 지수 심볼 매핑
FDR_INDEX_SYMBOL_MAP = {"SPX": "US500", "NAS": "IXIC", "DJI": "DJI", "VIX": "VIX"}


class DataService:
    """
        KIS API 기반 데이터 수집 및 지표 계산 서비스
        - 지수 데이터는 KIS 실패 시 FinanceDataReader로 보완합니다.
    """

    @classmethod
    def _is_fund_like_security(cls, ticker: str, name: str, market: str) -> bool:
        """ETF/ETN/펀드성 상품 여부 판별"""
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

        # 이름이 비어있거나 불확실할 때를 대비해 대표 ETF 티커 블록리스트
        us_etf_tickers = {
            "SPY", "IVV", "VOO", "VTI", "QQQ", "QQQM", "DIA", "IWM", "EFA", "EEM",
            "TLT", "IEF", "BND", "BNDX", "VCIT", "SMH", "VXUS", "IXUS", "IBIT"
        }
        return t in us_etf_tickers

    # 미국 폴백 티커 목록 상수
    _US_FALLBACK_CORE = [
        "AAPL", "NVDA", "MSFT", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "COST", "NFLX",
        "JPM", "V", "LLY", "XOM", "UNH"
    ]
    _US_FALLBACK_EXTENDED = [
        "GOOG", "BRK", "WMT", "MA", "ORCL", "HD", "BAC", "PG", "JNJ", "ABBV",
        "KO", "PEP", "MRK", "CVX", "AMD", "ADBE", "CRM", "CSCO", "INTC", "T",
        "VZ", "PFE", "ABT", "CMCSA", "QCOM", "MCD", "NKE", "TXN", "DHR", "WFC",
        "DIS", "AMGN", "UNP", "LOW", "NEE", "IBM", "PM", "RTX", "SPGI", "CAT",
        "GS", "HON", "INTU", "BKNG", "BLK", "AXP", "PLD", "LMT", "TMO", "MDT",
        "SYK", "DE", "TJX", "GILD", "ADP", "ISRG", "C", "SCHW", "MMC", "CB",
        "ETN", "SO", "CI", "DUK", "PGR", "ELV", "ZTS", "BDX", "MU", "KLAC",
        "SNPS", "PANW", "AMAT", "LRCX", "MELI", "SBUX", "REGN", "VRTX", "NOW", "UBER",
        "SHOP", "CRWD", "DASH", "PYPL", "SQ", "TTD", "ROKU", "BIDU", "PDD", "NTES",
        "ASML", "TMUS", "NDAQ", "EA", "ADSK", "ORLY", "MAR", "CEG", "FANG", "CSX",
        "AEP", "MNST", "MRVL", "NXPI", "IDXX", "FTNT", "ABNB", "WBD", "CME", "PCAR",
        "XEL", "MCHP", "CTAS", "FAST", "ARGX", "ALNY", "STX", "HOOD", "SNY", "ARM"
    ]
    _US_NYSE_SYMBOLS = {
        "JPM", "V", "LLY", "XOM", "UNH", "BRK", "WMT", "MA", "HD", "BAC", "PG", "JNJ",
        "ABBV", "KO", "PEP", "MRK", "CVX", "T", "VZ", "PFE", "ABT", "MCD", "NKE", "DHR",
        "WFC", "DIS", "AMGN", "UNP", "LOW", "NEE", "IBM", "PM", "RTX", "SPGI", "CAT",
        "GS", "HON", "BLK", "AXP", "PLD", "LMT", "TMO", "MDT", "SYK", "DE", "TJX",
        "GILD", "C", "SCHW", "MMC", "CB", "ETN", "SO", "CI", "DUK", "PGR", "ELV",
        "ZTS", "BDX", "UBER", "TMUS", "NDAQ", "CME", "PCAR", "XEL", "CEG", "FANG", "CSX", "AEP"
    }

    @classmethod
    def _parse_us_fallback_ticker_row(cls, sym_candidate: str, seen: set) -> tuple:
        """단일 폴백 심볼 후보를 (sym, excd, ex_name) 튜플로 변환. 유효하지 않으면 None 반환."""
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
        """미국 랭킹 조회 실패 시 사용할 대체 티커 목록(최대 limit)"""
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
        """KRX 랭킹 응답의 단일 항목에서 ticker 추출 + StockMeta upsert. 유효하지 않으면 None."""
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
        """KRX 티커가 limit 미만일 때 DB 메타에서 KR 개별 종목으로 부족분 보충."""
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
        """KIS API를 통해 국내 주식 시가총액 상위 종목을 수집합니다."""
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
        """KIS NAS+NYS 랭킹 API에서 미국 주식 티커+메타 목록 조회 후 시총순 상위 limit개 반환."""
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
        """폴백 목록으로 부족분 보충하거나 tickers가 비어있으면 전체를 폴백으로 채운다."""
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
        """KIS API를 통해 미국 주식 시가총액 상위 종목을 수집합니다."""
        try:
            tickers = cls._fetch_us_tickers_from_kis(limit)
            return cls._apply_us_ticker_supplements(tickers, limit)
        except Exception as e:
            logger.error(f"Error fetching top US tickers via KIS: {e}")
            return [ft for ft, _, _ in cls._build_us_fallback_data(limit=limit)]

    @classmethod
    def _fetch_kr_price_history(cls, ticker: str, token: str, start_date: str, end_date: str) -> pd.DataFrame:
        """KIS API로 국내 주식 일봉 데이터 조회 후 DataFrame 반환."""
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
        """KIS API로 해외 주식/지수 일봉 데이터 조회 후 DataFrame 반환. 지수는 FDR 폴백 포함."""
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
        """배치 상한(100건)에 달했을 때 가장 오래된 날짜 이전 구간을 추가 조회해 df를 확장."""
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
        """KIS API를 통해 과거 N일간의 가격 데이터를 가져옵니다."""
        # 과거 시세 조회는 시장 운영 시간과 무관하게 허용됨
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
        """KIS 지수 데이터 실패 시 FinanceDataReader로 보완"""
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
        """단일 티커 시세/지표/DCF 수집 후 DB 저장. 성공 시 True, 건너뜀·실패 시 False."""
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
    def sync_daily_market_data(cls, limit: int = 100) -> None:
        """매일 1회 실행: 상위 종목 수집 -> 지표 계산 -> DB 저장"""
        logger.info(f"Starting daily market data sync (Top {limit})...")
        kr_tickers = cls.get_top_krx_tickers(limit=limit)
        us_tickers = cls.get_top_us_tickers(limit=limit)
        all_tickers = [(t, "KR") for t in kr_tickers] + [(t, "US") for t in us_tickers]
        token = KisService.get_access_token()  # 루프 밖에서 1회만 조회
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
