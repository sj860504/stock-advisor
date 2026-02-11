import os
import sys
from datetime import datetime

# 프로젝트 루트 경로 추가
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(project_root)

from services.stock_meta_service import StockMetaService
from models.stock_meta import ApiTrMeta

def populate_tr_ids():
    print("📦 KIS TR ID 데이터베이스 연동 및 초기화 중...")
    StockMetaService.init_db()
    
    tr_data = [
        # 1. 국내주식
        {"category": "국내주식", "api_name": "주식주문_매도", "tr_id_real": "TTTC0801U", "tr_id_vts": "VTTC0801U"},
        {"category": "국내주식", "api_name": "주식주문_매수", "tr_id_real": "TTTC0802U", "tr_id_vts": "VTTC0802U"},
        {"category": "국내주식", "api_name": "주식주문_신용_매도", "tr_id_real": "TTTC0803U", "tr_id_vts": ""},
        {"category": "국내주식", "api_name": "주식주문_신용_매수", "tr_id_real": "TTTC0804U", "tr_id_vts": ""},
        {"category": "국내주식", "api_name": "주식주문_정정취소", "tr_id_real": "TTTC0803U", "tr_id_vts": "VTTC0803U"},
        {"category": "국내주식", "api_name": "주식정정취소가능주문조회", "tr_id_real": "TTTC8036R", "tr_id_vts": "VTTC8036R"},
        {"category": "국내주식", "api_name": "주식일별주문체결조회", "tr_id_real": "TTTC8001R", "tr_id_vts": "VTTC8001R"},
        {"category": "국내주식", "api_name": "주식잔고조회", "tr_id_real": "TTTC8434R", "tr_id_vts": "VTTC8434R"},
        {"category": "국내주식", "api_name": "매수가능조회", "tr_id_real": "TTTC8908R", "tr_id_vts": "VTTC8908R"},
        {"category": "국내주식", "api_name": "매도가능수량조회", "tr_id_real": "TTTC8408R", "tr_id_vts": ""},
        {"category": "국내주식", "api_name": "주식현재가_시세", "tr_id_real": "FHKST01010100", "tr_id_vts": "FHKST01010100"},
        {"category": "국내주식", "api_name": "주식현재가_호가", "tr_id_real": "FHKST01010200", "tr_id_vts": "FHKST01010200"},
        {"category": "국내주식", "api_name": "주식현재가_체결", "tr_id_real": "FHKST01010300", "tr_id_vts": "FHKST01010300"},
        {"category": "국내주식", "api_name": "주식현재가_일자별", "tr_id_real": "FHKST01010400", "tr_id_vts": "FHKST01010400"},
        {"category": "국내주식", "api_name": "주식당일분봉조회", "tr_id_real": "FHKST03010200", "tr_id_vts": "FHKST03010200"},
        {"category": "국내주식", "api_name": "주식일별분봉조회", "tr_id_real": "FHKST03010230", "tr_id_vts": ""},
        
        # 2. 해외주식
        {"category": "해외주식", "api_name": "해외주식_미국매수", "tr_id_real": "TTTT1002U", "tr_id_vts": "VTTT1002U"},
        {"category": "해외주식", "api_name": "해외주식_미국매도", "tr_id_real": "TTTT1006U", "tr_id_vts": "VTTT1006U"},
        {"category": "해외주식", "api_name": "해외주식_정정취소", "tr_id_real": "TTTT1004U", "tr_id_vts": "VTTT1004U"},
        {"category": "해외주식", "api_name": "해외주식_주문체결내역", "tr_id_real": "TTTS3035R", "tr_id_vts": "VTTS3035R"},
        {"category": "해외주식", "api_name": "해외주식_미체결내역", "tr_id_real": "TTTS3018R", "tr_id_vts": ""},
        {"category": "해외주식", "api_name": "해외주식_잔고", "tr_id_real": "TTTS3012R", "tr_id_vts": "VTTS3012R"},
        {"category": "해외주식", "api_name": "해외주식_체결기준현재잔고", "tr_id_real": "CTRP6504R", "tr_id_vts": "VTRP6504R"},
        {"category": "해외주식", "api_name": "해외주식_매수가능금액조회", "tr_id_real": "TTTS3007R", "tr_id_vts": "VTTS3007R"},
        {"category": "해외주식", "api_name": "해외주식_현재가", "tr_id_real": "HHDFS00000300", "tr_id_vts": "HHDFS00000300"},
        {"category": "해외주식", "api_name": "해외주식_상세시세", "tr_id_real": "HHDFS70200200", "tr_id_vts": "HHDFS70200200"},
        {"category": "해외주식", "api_name": "해외주식_시가총액순위", "tr_id_real": "HHDFS76350100", "tr_id_vts": "HHDFS76350100"},
        {"category": "해외주식", "api_name": "해외주식_기간별시세", "tr_id_real": "HHDFS76240000", "tr_id_vts": "HHDFS76240000"},
        {"category": "해외주식", "api_name": "해외주식_종목지수환율기간별", "tr_id_real": "FHKST03030100", "tr_id_vts": "FHKST03030100"},
        
        # 3. 국내선물옵션
        {"category": "국내선물옵션", "api_name": "선물옵션_주문", "tr_id_real": "TTTO1101U", "tr_id_vts": "VTTO1101U"},
        {"category": "국내선물옵션", "api_name": "선물옵션_정정취소주문", "tr_id_real": "TTTO1103U", "tr_id_vts": "VTTO1103U"},
        {"category": "국내선물옵션", "api_name": "선물옵션_주문체결내역조회", "tr_id_real": "TTTO5201R", "tr_id_vts": "VTTO5201R"},
        {"category": "국내선물옵션", "api_name": "선물옵션_잔고현황", "tr_id_real": "CTFO6118R", "tr_id_vts": "VTFO6118R"},
        {"category": "국내선물옵션", "api_name": "선물옵션_주문가능", "tr_id_real": "TTTO5105R", "tr_id_vts": "VTTO5105R"},
        {"category": "국내선물옵션", "api_name": "선물옵션_시세", "tr_id_real": "FHMIF10000000", "tr_id_vts": "FHMIF10000000"},
        
        # 4. 공통/인증
        {"category": "공통", "api_name": "접근토큰발급", "tr_id_real": "tokenP", "tr_id_vts": "tokenP", "api_path": "/oauth2/tokenP"},
        {"category": "공통", "api_name": "접근토큰폐기", "tr_id_real": "revokeP", "tr_id_vts": "revokeP", "api_path": "/oauth2/revokeP"},
        {"category": "공통", "api_name": "Hashkey", "tr_id_real": "hashkey", "tr_id_vts": "hashkey", "api_path": "/uapi/hashkey"},
    ]
    
    count = 0
    for data in tr_data:
        res = StockMetaService.upsert_api_tr_meta(**data)
        if res:
            count += 1
            
    print(f"✅ 총 {count}개의 TR ID 정보가 저장되었습니다.")

if __name__ == "__main__":
    populate_tr_ids()
