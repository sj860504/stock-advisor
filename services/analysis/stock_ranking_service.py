from services.kis.kis_service import KisService
from services.market.stock_meta_service import StockMetaService
from utils.logger import get_logger

logger = get_logger("stock_ranking_service")

class StockRankingService:
    """
    Overseas stock market cap ranking-based metadata collection and DB storage service.
    """
    
    @classmethod
    def populate_top_overseas_stocks(cls, exchanges=None):
        """
        Collect top market cap stocks from major exchanges (NAS, NYS, AMS) and save to DB.
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
                # Query TR ID/Path once outside loop (prevent N+1)
                tr_id, api_path = StockMetaService.get_api_info("해외주식_상세시세")
                for row in output:
                    ticker = row.get("symb")
                    name_en = row.get("name")

                    if not ticker: continue
                    StockMetaService.upsert_stock_meta(
                        ticker=ticker,
                        name_en=name_en,
                        market_type="US",
                        exchange_code=excd,
                        api_path=api_path,
                        api_tr_id=tr_id,
                        api_market_code=excd,
                    )
                    count += 1
                
                logger.info(f"✅ Successfully saved {count} stocks from {excd}")
                
            except Exception as e:
                logger.error(f"Error in ranking for {excd}: {e}")

    @classmethod
    def run_init_population(cls):
        """Run initial data population."""
        # Initialize DB first
        StockMetaService.init_db()
        # Collect data
        cls.populate_top_overseas_stocks()
