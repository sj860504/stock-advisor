"""KOSPI/KOSDAQ 마스터 파일 다운로드 및 시가총액 상위 종목 리스트 생성."""
import os
import ssl
import urllib.request
import zipfile
from typing import List

import pandas as pd

from utils.logger import get_logger

logger = get_logger("master_data_service")

# 다운로드 URL 및 로컬 파일명
MASTER_DOWNLOAD_TARGETS = {
    "KOSPI": (
        "https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip",
        "kospi_code.zip",
    ),
    "KOSDAQ": (
        "https://new.real.download.dws.co.kr/common/master/kosdaq_code.mst.zip",
        "kosdaq_code.zip",
    ),
}
DEFAULT_TOP_COUNT = 100


class MasterDataService:
    """KOSPI/KOSDAQ 마스터 파일 다운로드·파싱 및 시총 상위 종목 조회."""

    BASE_DIR = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "data",
        "master",
    )

    @classmethod
    def _ensure_dir(cls) -> None:
        os.makedirs(cls.BASE_DIR, exist_ok=True)

    @classmethod
    def download_master_files(cls) -> None:
        """KOSPI, KOSDAQ 마스터 ZIP을 다운로드하고 압축 해제합니다."""
        cls._ensure_dir()
        ssl._create_default_https_context = ssl._create_unverified_context
        for market, (url, zip_name) in MASTER_DOWNLOAD_TARGETS.items():
            zip_path = os.path.join(cls.BASE_DIR, zip_name)
            logger.info(f"📥 Downloading {market} master zip from {url}...")
            urllib.request.urlretrieve(url, zip_path)
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(cls.BASE_DIR)
            os.remove(zip_path)
            logger.info(f"✅ {market} master file extracted.")

    @classmethod
    def get_kospi_master(cls):
        file_path = os.path.join(cls.BASE_DIR, "kospi_code.mst")
        if not os.path.exists(file_path):
            cls.download_master_files()

        tmp1 = os.path.join(cls.BASE_DIR, "kospi_part1.tmp")
        tmp2 = os.path.join(cls.BASE_DIR, "kospi_part2.tmp")

        with open(file_path, mode="r", encoding="cp949") as f, open(tmp1, "w") as wf1, open(tmp2, "w") as wf2:
            for row in f:
                rf1 = row[0:len(row) - 228]
                rf1_1 = rf1[0:9].rstrip()
                rf1_3 = rf1[21:].strip()
                wf1.write(f"{rf1_1},{rf1_3}\n")
                wf2.write(row[-228:])

        df1 = pd.read_csv(tmp1, header=None, names=['단축코드', '한글명'], encoding='utf-8')
        field_specs = [2, 1, 4, 4, 4, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 9, 5, 5, 1, 1, 1, 2, 1, 1, 1, 2, 2, 2, 3, 1, 3, 12, 12, 8, 15, 21, 2, 7, 1, 1, 1, 1, 1, 9, 9, 9, 5, 9, 8, 9, 3, 1, 1, 1]
        part2_columns = ['그룹코드', '시가총액규모', '지수업종대분류', '지수업종중분류', '지수업종소분류', '제조업', '저유동성', '지배구조지수종목', 'KOSPI200섹터업종', 'KOSPI100', 'KOSPI50', 'KRX', 'ETP', 'ELW발행', 'KRX100', 'KRX자동차', 'KRX반도체', 'KRX바이오', 'KRX은행', 'SPAC', 'KRX에너지화학', 'KRX철강', '단기과열', 'KRX미디어통신', 'KRX건설', 'Non1', 'KRX증권', 'KRX선박', 'KRX섹터_보험', 'KRX섹터_운송', 'SRI', '기준가', '매매수량단위', '시간외수량단위', '거래정지', '정리매매', '관리종목', '시장경고', '경고예고', '불성실공시', '우회상장', '락구분', '액면변경', '증자구분', '증거금비율', '신용가능', '신용기간', '전일거래량', '액면가', '상장일자', '상장주수', '자본금', '결산월', '공모가', '우선주', '공매도과열', '이상급등', 'KRX300', 'KOSPI', '매출액', '영업이익', '경상이익', '당기순이익', 'ROE', '기준년월', '시가총액', '그룹사코드', '회사신용한도초과', '담보대출가능', '대주가능']
        df2 = pd.read_fwf(tmp2, widths=field_specs, names=part2_columns)
        
        df = pd.concat([df1.reset_index(drop=True), df2.reset_index(drop=True)], axis=1)
        os.remove(tmp1)
        os.remove(tmp2)
        return df

    @classmethod
    def get_kosdaq_master(cls):
        file_path = os.path.join(cls.BASE_DIR, "kosdaq_code.mst")
        if not os.path.exists(file_path):
            cls.download_master_files()

        tmp1 = os.path.join(cls.BASE_DIR, "kosdaq_part1.tmp")
        tmp2 = os.path.join(cls.BASE_DIR, "kosdaq_part2.tmp")

        with open(file_path, mode="r", encoding="cp949") as f, open(tmp1, "w") as wf1, open(tmp2, "w") as wf2:
            for row in f:
                rf1 = row[0:len(row) - 222]
                rf1_1 = rf1[0:9].rstrip()
                rf1_3 = rf1[21:].strip()
                wf1.write(f"{rf1_1},{rf1_3}\n")
                wf2.write(row[-222:])

        df1 = pd.read_csv(tmp1, header=None, names=['단축코드', '한글명'], encoding='utf-8')
        field_specs = [2, 1, 4, 4, 4, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 9, 5, 5, 1, 1, 1, 2, 1, 1, 1, 2, 2, 2, 3, 1, 3, 12, 12, 8, 15, 21, 2, 7, 1, 1, 1, 1, 9, 9, 9, 5, 9, 8, 9, 3, 1, 1, 1]
        part2_columns = ['증권그룹구분코드','시가총액규모','지수업종대분류','지수업종중분류','지수업종소분류','벤처기업','저유동성','KRX종목','ETP','KRX100','KRX자동차','KRX반도체','KRX바이오','KRX은행','SPAC','KRX에너지화학','KRX철강','단기과열','KRX미디어통신','KRX건설','투자주의환기종목','KRX증권','KRX선박','KRX보험','KRX운송','KOSDAQ150','기준가','정규매매단위','시간외매매단위','거래정지','정리매매','관리종목','시장경고','경고예고','불성실공시','우회상장','락구분','액면변경','증자구분','증거금비율','신용가능','신용기간','전일거래량','액면가','상장일자','상장주수','자본금','결산월','공모가','우선주','공매도과열','이상급등','KRX300','매출액','영업이익','경상이익','당기순이익','ROE','기준년월','전일기준 시가총액 (억)','그룹사코드','회사신용한도초과','담보대출가능','대주가능']
        df2 = pd.read_fwf(tmp2, widths=field_specs, names=part2_columns)

        df = pd.concat([df1.reset_index(drop=True), df2.reset_index(drop=True)], axis=1)
        os.remove(tmp1)
        os.remove(tmp2)
        return df

    @classmethod
    def get_top_market_cap_tickers(cls, count: int = DEFAULT_TOP_COUNT) -> List[dict]:
        """코스피/코스닥 합산 시가총액 상위 count개 종목 리스트를 반환합니다. (랭킹 API 규격 호환)"""
        try:
            kospi = cls.get_kospi_master()
            kosdaq = cls.get_kosdaq_master()
            # ETP=1: ETF/ETN 제외 → 개별 주식만
            kospi_stocks = kospi[pd.to_numeric(kospi["ETP"], errors="coerce").fillna(0) == 0]
            kosdaq_stocks = kosdaq[pd.to_numeric(kosdaq["ETP"], errors="coerce").fillna(0) == 0]
            kospi_df = kospi_stocks[["단축코드", "한글명", "시가총액"]].rename(columns={"시가총액": "market_cap_raw"})
            kosdaq_df = kosdaq_stocks[["단축코드", "한글명", "전일기준 시가총액 (억)"]].rename(
                columns={"전일기준 시가총액 (억)": "market_cap_raw"}
            )
            merged = pd.concat([kospi_df, kosdaq_df])
            merged["market_cap_raw"] = pd.to_numeric(merged["market_cap_raw"], errors="coerce").fillna(0)
            top_stocks = merged.sort_values(by="market_cap_raw", ascending=False).head(count)
            result = [
                {
                    "mksc_shrn_iscd": row["단축코드"],
                    "hts_kor_isnm": row["한글명"],
                    "stck_prpr": "0",
                    "data_rank": "0",
                }
                for _, row in top_stocks.iterrows()
            ]
            logger.info(f"🏆 Local Ranking created: {len(result)} stocks selected.")
            return result
        except Exception as e:
            logger.error(f"❌ Error creating local ranking: {e}")
            return []
