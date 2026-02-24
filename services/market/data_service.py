import pandas as pd
import numpy as np
import time
import requests
from datetime import datetime
from config import Config
from utils.logger import get_logger
from services.kis.kis_service import KisService
from services.kis.fetch.kis_fetcher import KisFetcher
from services.market.stock_meta_service import StockMetaService
from services.analysis.indicator_service import IndicatorService
from services.analysis.financial_service import FinancialService
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

    @classmethod
    def _build_us_fallback_data(cls, limit: int = 100) -> list:
        """미국 랭킹 조회 실패 시 사용할 대체 티커 목록(최대 limit)"""
        core = [
            "AAPL", "NVDA", "MSFT", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "COST", "NFLX",
            "JPM", "V", "LLY", "XOM", "UNH"
        ]
        extended = [
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
        nyse_symbols = {
            "JPM", "V", "LLY", "XOM", "UNH", "BRK", "WMT", "MA", "HD", "BAC", "PG", "JNJ",
            "ABBV", "KO", "PEP", "MRK", "CVX", "T", "VZ", "PFE", "ABT", "MCD", "NKE", "DHR",
            "WFC", "DIS", "AMGN", "UNP", "LOW", "NEE", "IBM", "PM", "RTX", "SPGI", "CAT",
            "GS", "HON", "BLK", "AXP", "PLD", "LMT", "TMO", "MDT", "SYK", "DE", "TJX",
            "GILD", "C", "SCHW", "MMC", "CB", "ETN", "SO", "CI", "DUK", "PGR", "ELV",
            "ZTS", "BDX", "UBER", "TMUS", "NDAQ", "CME", "PCAR", "XEL", "CEG", "FANG", "CSX", "AEP"
        }

        ordered = []
        seen = set()
        for sym_candidate in core + extended:
            sym = str(sym_candidate).strip().upper()
            if not sym or sym in seen:
                continue
            seen.add(sym)
            excd = "NYS" if sym in nyse_symbols else "NAS"
            ex_name = "NYSE" if excd == "NYS" else "NASD"
            if cls._is_fund_like_security(sym, sym, "US"):
                continue
            ordered.append((sym, excd, ex_name))
            if len(ordered) >= limit:
                break
        return ordered

    @classmethod
    def get_top_krx_tickers(cls, limit: int = 100) -> list:
        """KIS API를 통해 국내 주식 시가총액 상위 종목을 수집합니다."""
        try:
            token = KisService.get_access_token()
            response = KisFetcher.fetch_domestic_ranking(token)
            tickers = []
            if response.get("output"):
                for item in response["output"]:
                    ticker = item.get("mksc_shrn_iscd")
                    name = item.get("hts_kor_isnm")
                    if ticker and (not cls._is_fund_like_security(ticker, name, "KR")):
                        tickers.append(ticker)
                        tr_id, path = StockMetaService.get_api_info("주식현재가_시세")
                        StockMetaService.upsert_stock_meta(
                            ticker=ticker,
                            name_ko=name,
                            market_type="KR",
                            exchange_code="KRX",
                            api_path=path,
                            api_tr_id=tr_id,
                            api_market_code="J"
                        )
                        if len(tickers) >= limit:
                            break
            if not tickers:
                tickers = list(KR_FALLBACK_TICKERS)
                logger.info(f"⚠️ KRX ranking empty. Using fallback list: {len(tickers)} tickers.")

            # ETF/ETN 제외 후 부족분은 DB의 KR 단일종목 메타에서 보충
            if len(tickers) < limit:
                try:
                    from models.stock_meta import StockMeta
                    session = StockMetaService.get_session()
                    existing = set(tickers)
                    query = (
                        session.query(StockMeta)
                        .filter(StockMeta.market_type == "KR")
                        .all()
                    )
                    for row in query:
                        meta_ticker = str(getattr(row, "ticker", "") or "").strip()
                        name_ko = str(getattr(row, "name_ko", "") or "").strip()
                        if not (meta_ticker.isdigit() and len(meta_ticker) == 6):
                            continue
                        if meta_ticker in existing:
                            continue
                        if cls._is_fund_like_security(meta_ticker, name_ko, "KR"):
                            continue
                        existing.add(meta_ticker)
                        tickers.append(meta_ticker)
                        if len(tickers) >= limit:
                            break
                except Exception as ex:
                    logger.warning(f"⚠️ KR fallback supplement from DB failed: {ex}")
            return tickers
        except Exception as e:
            logger.error(f"Error fetching top KRX tickers via KIS: {e}")
            return list(KR_FALLBACK_MINIMAL)

    @classmethod
    def get_top_us_tickers(cls, limit: int = 100) -> list:
        """KIS API를 통해 미국 주식 시가총액 상위 종목을 수집합니다."""
        try:
            token = KisService.get_access_token()
            response_nas = KisFetcher.fetch_overseas_ranking(token, excd="NAS")
            response_nys = KisFetcher.fetch_overseas_ranking(token, excd="NYS")
            combined = []
            for response, excd in [(response_nas, "NAS"), (response_nys, "NYS")]:
                if response.get("output"):
                    for item in response["output"]:
                        ticker = item.get('symb')
                        name = item.get('hname')
                        if cls._is_fund_like_security(ticker, name, "US"):
                            continue
                        combined.append({
                            "ticker": ticker,
                            "name": name,
                            "excd": excd,
                            "mcap": float(item.get('mcap', 0))
                        })
            
            # 시총 순 정렬
            combined.sort(key=lambda x: x['mcap'], reverse=True)
            
            tickers = []
            for item in combined[:limit]:
                ticker = item['ticker']
                if ticker:
                    tickers.append(ticker)
                    tr_id, path = StockMetaService.get_api_info("해외주식_상세시세")
                    StockMetaService.upsert_stock_meta(
                        ticker=ticker,
                        name_ko=item['name'],
                        market_type="US",
                        exchange_code="NASD" if item['excd'] == "NAS" else "NYSE",
                        api_path=path,
                        api_tr_id=tr_id,
                        api_market_code=item['excd']
                    )
            if len(tickers) < limit:
                existing = set(tickers)
                tr_id, path = StockMetaService.get_api_info("해외주식_상세시세")
                for fallback_ticker, excd, ex_name in cls._build_us_fallback_data(limit=limit * 2):
                    if len(tickers) >= limit:
                        break
                    if fallback_ticker in existing:
                        continue
                    existing.add(fallback_ticker)
                    tickers.append(fallback_ticker)
                    StockMetaService.upsert_stock_meta(
                        ticker=fallback_ticker,
                        name_ko=fallback_ticker,
                        market_type="US",
                        exchange_code=ex_name,
                        api_path=path,
                        api_tr_id=tr_id,
                        api_market_code=excd
                    )

            if not tickers:
                fallback_data = cls._build_us_fallback_data(limit=limit)
                tickers = []
                tr_id, path = StockMetaService.get_api_info("해외주식_상세시세")
                for fallback_ticker, excd, ex_name in fallback_data:
                    tickers.append(fallback_ticker)
                    StockMetaService.upsert_stock_meta(
                        ticker=fallback_ticker,
                        name_ko=fallback_ticker,
                        market_type="US",
                        exchange_code=ex_name,
                        api_path=path,
                        api_tr_id=tr_id,
                        api_market_code=excd
                    )
                logger.info(f"⚠️ US ranking empty. Using fallback list: {len(tickers)} tickers with metadata.")
            return tickers
        except Exception as e:
            logger.error(f"Error fetching top US tickers via KIS: {e}")
            return [ft for ft, _, _ in cls._build_us_fallback_data(limit=limit)]

    @classmethod
    def get_price_history(cls, ticker: str, days: int = 300) -> pd.DataFrame:
        """KIS API를 통해 과거 N일간의 가격 데이터를 가져옵니다."""
        # 기존에 fetch_daily_price, fetch_overseas_daily_price를 이미 구현/정리했음을 가정
        from services.kis.fetch.kis_fetcher import KisFetcher
        # 과거 시세 조회는 시장 운영 시간과 무관하게 허용됨 (MarketHourService.can_fetch_history() 반영)
        is_kr = ticker.isdigit()
        
        logger.info(f"💾 Fetching history for {ticker} (Last {days} days)...")

        try:
            token = KisService.get_access_token()
            from datetime import timedelta
            end_date = datetime.now().strftime("%Y%m%d")
            start_date = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
            
            if ticker.isdigit():
                response = KisFetcher.fetch_daily_price(token, ticker, start_date, end_date)
                if not response or not response.get("output2"):
                    return pd.DataFrame()
                df = pd.DataFrame(response["output2"])
                df = df.rename(columns={"stck_clpr": COL_CLOSE, "stck_hgpr": COL_HIGH, "stck_lwpr": COL_LOW, "stck_oprc": COL_OPEN, "stck_bsop_date": COL_DATE})
            elif ticker in ["SPX", "NAS", "VIX", "DJI"]:
                response = KisFetcher.fetch_overseas_daily_price(token, ticker, start_date, end_date)
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
            else:
                response = KisFetcher.fetch_overseas_daily_price(token, ticker, start_date, end_date)
                if not response.get("output"):
                    return pd.DataFrame()
                df = pd.DataFrame(response["output"])
                if "clos" in df.columns:
                    df = df.rename(columns={"clos": COL_CLOSE, "high": COL_HIGH, "low": COL_LOW, "open": COL_OPEN, "xymd": COL_DATE})
                else:
                    df = df.rename(columns={"last": COL_CLOSE, "high": COL_HIGH, "low": COL_LOW, "open": COL_OPEN, "xymd": COL_DATE})

            if COL_CLOSE not in df.columns:
                logger.error(f"❌ 'Close' column missing for {ticker}. Columns: {df.columns.tolist()}")
                return pd.DataFrame()

            if len(df) >= KIS_HISTORY_BATCH_LIMIT and days > 150:
                try:
                    # 가장 오래된 날짜를 기준으로 이전 100건 추가 요청
                    df[COL_DATE] = pd.to_datetime(df[COL_DATE])
                    earliest_date = df[COL_DATE].min()
                    new_end_date = (earliest_date - timedelta(days=1)).strftime("%Y%m%d")
                    
                    logger.info(f"➕ Fetching additional 100 rows for {ticker} (End Date: {new_end_date})")
                    if ticker.isdigit():
                        response2 = KisFetcher.fetch_daily_price(token, ticker, start_date, new_end_date)
                        if response2 and response2.get("output2"):
                            df2 = pd.DataFrame(response2["output2"])
                            df2 = df2.rename(columns={"stck_clpr": COL_CLOSE, "stck_hgpr": COL_HIGH, "stck_lwpr": COL_LOW, "stck_oprc": COL_OPEN, "stck_bsop_date": COL_DATE})
                            df = pd.concat([df, df2], ignore_index=True)
                    else:
                        response2 = KisFetcher.fetch_overseas_daily_price(token, ticker, start_date, new_end_date)
                        output2 = response2.get("output") or response2.get("output2")
                        if output2:
                            df2 = pd.DataFrame(output2)
                            if "clos" in df2.columns:
                                df2 = df2.rename(columns={"clos": COL_CLOSE, "high": COL_HIGH, "low": COL_LOW, "open": COL_OPEN, "xymd": COL_DATE})
                            else:
                                df2 = df2.rename(columns={"last": COL_CLOSE, "high": COL_HIGH, "low": COL_LOW, "open": COL_OPEN, "xymd": COL_DATE})
                            df = pd.concat([df, df2], ignore_index=True)
                except Exception as ex:
                    logger.warning(f"⚠️ Failed to fetch additional rows for {ticker}: {ex}")

            df[COL_DATE] = pd.to_datetime(df[COL_DATE])
            df.set_index(COL_DATE, inplace=True)
            for col in [COL_CLOSE, COL_HIGH, COL_LOW, COL_OPEN]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce')
            
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
    def sync_daily_market_data(cls, limit: int = 100):
        """매일 1회 실행: 상위 종목 수집 -> 지표 계산 -> DB 저장"""
        logger.info(f"🚀 Starting daily market data sync (Top {limit})...")
        
        # 1. 티커 수집
        kr_tickers = cls.get_top_krx_tickers(limit=limit)
        us_tickers = cls.get_top_us_tickers(limit=limit)
        
        all_tickers = [(t, "KR") for t in kr_tickers] + [(t, "US") for t in us_tickers]
        
        for ticker, market in all_tickers:
            try:
                # 시장 시간 체크
                if not MarketHourService.should_fetch(market):
                    # logger.debug(f"😴 {market} market is closed. skipping {ticker}.")
                    continue

                logger.info(f"🔄 Processing {ticker} ({market})...")
                
                token = KisService.get_access_token()
                if market == "KR":
                    price_info = KisFetcher.fetch_domestic_price(token, ticker)
                else:
                    price_info = KisFetcher.fetch_overseas_price(token, ticker)
                if not price_info:
                    continue

                hist = cls.get_price_history(ticker, days=HISTORY_DAYS_DEFAULT)
                indicators = {}
                if not hist.empty:
                    indicators = IndicatorService.get_latest_indicators(hist[COL_CLOSE])
                dcf_data = FinancialService.get_dcf_data(ticker)
                metrics = {
                    "current_price": price_info.get("price"),
                    "market_cap": price_info.get("market_cap"),
                    "per": price_info.get("per"),
                    "pbr": price_info.get("pbr"),
                    "eps": price_info.get("eps"),
                    "bps": price_info.get("bps"),
                    "rsi": indicators.get("rsi"),
                    "ema": indicators.get("ema"),
                    "dcf_value": None,
                }
                StockMetaService.save_financials(ticker, metrics)
                time.sleep(KIS_RATE_LIMIT_SLEEP_SEC)
                
            except Exception as e:
                logger.error(f"Error syncing {ticker}: {e}")
                continue
        
        logger.info("✅ Daily market data sync completed.")
