# Stock Advisor API

한국투자증권(KIS) API 및 WebSocket 기반 주식 분석 및 알림 API (FastAPI + FinanceDataReader + KIS)

## 주요 기능

### 📡 실시간 모니터링
- KIS WebSocket(kis_ws)을 통한 실시간 시세 수신
- KIS API를 통한 해외/국내 상위 종목 자동 수집
- 기술적 지표 계산: RSI, EMA(5, 10, 20, 60, 100, 200)
- 주요 지수(S&P 500, KOSPI 등) 실시간 현황 조회

### 📊 가치평가 및 분석
- **DCF (현금흐름할인법)**: KIS API 재무 데이터를 기반으로 적정주가 산출
- **기술적 분석**: RSI 및 이평선 기반 매매 신호 포착
- **수익률 분석**: YTD 수익률 및 MDD(최대 낙폭) 계산
- **재무지표**: PER, PBR, ROE 등 주요 지표 제공

### 💼 포트폴리오 관리
- **엑셀 업로드**: 보유 종목 일괄 등록 (티커, 수량, 매수가)
- **수익률 추적**: 포트폴리오 전체 및 종목별 수익률 현황
- **종목 관리**: 개별 종목 추가/삭제

### 🚨 매매 신호 및 알림
- 과매도 알림 (RSI < 30)
- 과매수 알림 (RSI > 70)
- DCF 저평가 알림 (현재가 < DCF * 0.8)
- EMA200 지지선 터치 알림

## 실행 방법

```bash
# 의존성 설치
pip install -r requirements.txt

# 서버 실행
python main.py

# 또는 uvicorn 직접 실행
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

API 문서: http://localhost:8000/docs

## 기술 스택

- **Back-end**: FastAPI
- **Data**: KIS API (REST/WebSocket)
- **Real-time**: KIS WebSocket
- **Scheduling**: APScheduler
- **Analysis**: Pandas, NumPy
- **Utils**: OpenPyxl (Excel 처리)
