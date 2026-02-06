import FinanceDataReader as fdr
import pandas as pd

class TickerService:
    _krx_listing = None
    _us_map = {
        "테슬라": "TSLA",
        "애플": "AAPL",
        "마이크로소프트": "MSFT",
        "마소": "MSFT",
        "엔비디아": "NVDA",
        "구글": "GOOGL",
        "알파벳": "GOOGL",
        "아마존": "AMZN",
        "메타": "META",
        "페이스북": "META",
        "넷플릭스": "NFLX",
        "스타벅스": "SBUX",
        "코카콜라": "KO",
        "나이키": "NKE",
        "AMD": "AMD",
        "인텔": "INTC",
        "TSMC": "TSM",
        "티에스엠씨": "TSM"
    }

    @classmethod
    def _load_krx(cls):
        if cls._krx_listing is None:
            try:
                # KRX 전체 종목 리스트 (코스피, 코스닥, 코넥스)
                cls._krx_listing = fdr.StockListing('KRX')
            except Exception as e:
                print(f"Failed to load KRX listing: {e}")
                cls._krx_listing = pd.DataFrame()
    
    @classmethod
    def get_yahoo_ticker(cls, ticker: str) -> str:
        """
        Yahoo Finance에서 사용하는 티커로 변환합니다.
        KRX 종목의 경우 .KS(코스피), .KQ(코스닥) 접미사가 필요합니다.
        """
        # 1. 이미 .KS나 .KQ가 붙어있거나 미국 티커(알파벳)인 경우
        if not ticker.isdigit():
            return ticker

        # 2. 한국 종목 코드인 경우 (숫자 6자리)
        cls._load_krx()
        if not cls._krx_listing.empty:
            row = cls._krx_listing[cls._krx_listing['Code'] == ticker]
            if not row.empty:
                market = row.iloc[0]['Market']
                if market == 'KOSPI':
                    return f"{ticker}.KS"
                elif market == 'KOSDAQ':
                    return f"{ticker}.KQ"
                elif market == 'KOSDAQ GLOBAL':
                    return f"{ticker}.KQ"
        
        # 기본값: 코스피로 가정 (혹은 실패할 수 있음)
        return f"{ticker}.KS"

    @classmethod
    def resolve_ticker(cls, query: str) -> str:
        """
        입력된 검색어(이름 또는 코드)를 티커로 변환합니다.
        1. 이미 티커 형식(영어/숫자)이면 그대로 반환 (단, KRX 6자리 숫자는 확인)
        2. 인기 미국 주식 한글 매핑 확인
        3. KRX 종목명 검색
        4. 실패 시 그대로 반환 (데이터 조회 시도해볼 수 있도록)
        """
        query_upper = query.upper()
        
        # 1. 인기 미국 주식 한글 매핑
        if query in cls._us_map:
            return cls._us_map[query]
        
        # 2. KRX 종목명 검색
        cls._load_krx()
        if not cls._krx_listing.empty:
            # 정확히 일치하는 이름 찾기
            match = cls._krx_listing[cls._krx_listing['Name'] == query]
            if not match.empty:
                return match.iloc[0]['Code']
            
            # (선택) 포함하는 이름 찾기 - 첫번째 결과 반환? (너무 위험할 수 있으므로 일단 정확 일치만)
        
        # 3. 티커 형식이면 그대로 반환
        # KRX 종목코드는 숫자 6자리
        if query.isdigit() and len(query) == 6:
            return query
        
        # 미국 티커 (알파벳)
        if query_upper.isalpha():
            return query_upper
            
        return query  # 찾지 못했으면 원본 반환 (DataService에서 처리)
