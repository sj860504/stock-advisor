import FinanceDataReader as fdr
import pandas as pd

class TickerService:
    _krx_listing = None
    _us_map = {
        "?뚯뒳??: "TSLA",
        "?좏뵆": "AAPL",
        "留덉씠?щ줈?뚰봽??: "MSFT",
        "留덉냼": "MSFT",
        "?붾퉬?붿븘": "NVDA",
        "援ш?": "GOOGL",
        "?뚰뙆踰?: "GOOGL",
        "?꾨쭏議?: "AMZN",
        "硫뷀?": "META",
        "?섏씠?ㅻ턿": "META",
        "?룻뵆由?뒪": "NFLX",
        "?ㅽ?踰낆뒪": "SBUX",
        "肄붿뭅肄쒕씪": "KO",
        "?섏씠??: "NKE",
        "AMD": "AMD",
        "?명뀛": "INTC",
        "TSMC": "TSM",
        "?곗뿉?ㅼ뿞??: "TSM"
    }

    @classmethod
    def _load_krx(cls):
        if cls._krx_listing is None:
            try:
                # KRX ?꾩껜 醫낅ぉ 由ъ뒪??(肄붿뒪?? 肄붿뒪?? 肄붾꽖??
                cls._krx_listing = fdr.StockListing('KRX')
            except Exception as e:
                print(f"Failed to load KRX listing: {e}")
                cls._krx_listing = pd.DataFrame()
    
    @classmethod
    def get_yahoo_ticker(cls, ticker: str) -> str:
        """
        Yahoo Finance?먯꽌 ?ъ슜?섎뒗 ?곗빱濡?蹂?섑빀?덈떎.
        KRX 醫낅ぉ??寃쎌슦 .KS(肄붿뒪??, .KQ(肄붿뒪?? ?묐??ш? ?꾩슂?⑸땲??
        """
        # 1. ?대? .KS??.KQ媛 遺숈뼱?덇굅??誘멸뎅 ?곗빱(?뚰뙆踰???寃쎌슦
        if not ticker.isdigit():
            return ticker

        # 2. ?쒓뎅 醫낅ぉ 肄붾뱶??寃쎌슦 (?レ옄 6?먮━)
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
        
        # 湲곕낯媛? 肄붿뒪?쇰줈 媛??(?뱀? ?ㅽ뙣?????덉쓬)
        return f"{ticker}.KS"

    @classmethod
    def resolve_ticker(cls, query: str) -> str:
        """
        ?낅젰??寃?됱뼱(?대쫫 ?먮뒗 肄붾뱶)瑜??곗빱濡?蹂?섑빀?덈떎.
        1. ?대? ?곗빱 ?뺤떇(?곸뼱/?レ옄)?대㈃ 洹몃?濡?諛섑솚 (?? KRX 6?먮━ ?レ옄???뺤씤)
        2. ?멸린 誘멸뎅 二쇱떇 ?쒓? 留ㅽ븨 ?뺤씤
        3. KRX 醫낅ぉ紐?寃??
        4. ?ㅽ뙣 ??洹몃?濡?諛섑솚 (?곗씠??議고쉶 ?쒕룄?대낵 ???덈룄濡?
        """
        query_upper = query.upper()
        
        # 1. ?멸린 誘멸뎅 二쇱떇 ?쒓? 留ㅽ븨
        if query in cls._us_map:
            return cls._us_map[query]
        
        # 2. KRX 醫낅ぉ紐?寃??
        cls._load_krx()
        if not cls._krx_listing.empty:
            # ?뺥솗???쇱튂?섎뒗 ?대쫫 李얘린
            match = cls._krx_listing[cls._krx_listing['Name'] == query]
            if not match.empty:
                return match.iloc[0]['Code']
            
            # (?좏깮) ?ы븿?섎뒗 ?대쫫 李얘린 - 泥ル쾲吏?寃곌낵 諛섑솚? (?덈Т ?꾪뿕?????덉쑝誘濡??쇰떒 ?뺥솗 ?쇱튂留?
        
        # 3. ?곗빱 ?뺤떇?대㈃ 洹몃?濡?諛섑솚
        # KRX 醫낅ぉ肄붾뱶???レ옄 6?먮━
        if query.isdigit() and len(query) == 6:
            return query
        
        # 誘멸뎅 ?곗빱 (?뚰뙆踰?
        if query_upper.isalpha():
            return query_upper
            
        return query  # 李얠? 紐삵뻽?쇰㈃ ?먮낯 諛섑솚 (DataService?먯꽌 泥섎━)
