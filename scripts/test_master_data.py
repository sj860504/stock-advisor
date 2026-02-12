from services.market.master_data_service import MasterDataService
import json

def test_master_ranking():
    print("🚀 Extracting top 10 stocks by market cap using master data...")
    top_stocks = MasterDataService.get_top_market_cap_tickers(10)
    
    if top_stocks:
        print(f"✅ Found {len(top_stocks)} stocks.")
        for idx, s in enumerate(top_stocks):
            print(f"{idx+1}. {s['hts_kor_isnm']} ({s['mksc_shrn_iscd']})")
    else:
        print("❌ No stocks found or error occurred.")

if __name__ == "__main__":
    test_master_ranking()
