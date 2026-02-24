from services.kis.kis_service import KisService
from services.market.stock_meta_service import StockMetaService
from utils.logger import get_logger

logger = get_logger("stock_ranking_service")

class StockRankingService:
    """
    해외주식 시가총액 순위 기반 메타데이터 수집 및 DB 저장 서비스
    """
    
    @classmethod
    def populate_top_overseas_stocks(cls, exchanges=None):
        """
        주요 거래소(NAS, NYS, AMS)의 시가총액 상위 종목을 수집하여 DB에 저장합니다.
        """
        if exchanges is None:
            exchanges = ["NAS", "NYS", "AMS"]
            
        logger.info(f"🌐 Populating top overseas stocks for {exchanges}...")
        
        for excd in exchanges:
            try:
                response = KisService.get_overseas_ranking(excd=excd)
                if not response or response.get("rt_cd") != "0":
                    logger.error(f"❌ Failed to fetch ranking for {excd}: {response.get('msg1')}")
                    continue
                output = response.get("output", [])
                count = 0
                for row in output:
                    ticker = row.get("symb")
                    name_en = row.get("name")
                    
                    if not ticker: continue
                    
                    # StockMeta 저장
                    StockMetaService.upsert_stock_meta(
                        ticker=ticker,
                        name_en=name_en,
                        market_type="US",
                        exchange_code=excd,
                        # 이후 상세 조회를 위한 API 상세 정보 강제 설정 (VTS 대응)
                        api_path="/uapi/overseas-stock/v1/quotations/price-detail",
                        api_tr_id="HHDFS70200200",
                        api_market_code=excd
                    )
                    count += 1
                
                logger.info(f"✅ Successfully saved {count} stocks from {excd}")
                
            except Exception as e:
                logger.error(f"Error in ranking for {excd}: {e}")

    @classmethod
    def run_init_population(cls):
        """초기 데이터 채우기 실행"""
        # 먼저 DB 초기화
        StockMetaService.init_db()
        # 데이터 수집
        cls.populate_top_overseas_stocks()
