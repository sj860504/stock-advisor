# Stock Advisor API

실시간 주식 분석 및 가치평가 API (FastAPI + FinanceDataReader + yfinance)

## 주요 기능

### 📊 실시간 모니터링
- 미국 시총 Top 20 종목 자동 수집 (1분 주기)
- DCF 적정주가 계산 (30분 주기)
- 기술적 지표: RSI, EMA(5, 10, 20, 60, 100, 200)

### 💰 가치평가
- **DCF (현금흐름 할인법)**: yfinance에서 실제 FCF 데이터 사용
- **신뢰도 검증**: DCF 대비 현재가 괴리율 분석

### 🔔 매매 신호
- 과매도 알림 (RSI < 30)
- 과매수 알림 (RSI > 70)
- DCF 저평가 알림 (현재가 < DCF * 0.8)
- EMA200 지지선 터치 알림

## API 엔드포인트

| 엔드포인트 | 설명 |
|-----------|------|
| `GET /market/top20` | 실시간 Top 20 시세 + 지표 |
| `GET /valuation/{ticker}` | 종목 가치평가 (한글/영문 지원) |
| `GET /returns/{ticker}` | 수익률 및 MDD 분석 |
| `GET /metrics/{ticker}` | 재무지표 (PER, PBR, ROE 등) |
| `GET /signals` | 현재 매매 신호 |
| `GET /summary` | 일일 요약 리포트 |
| `GET /market` | 주요 지수 현황 |

## 실행 방법

```bash
# 의존성 설치
pip install -r requirements.txt

# 서버 실행
python main.py

# 또는
uvicorn main:app --reload
```

API 문서: http://localhost:8000/docs

## 지원 종목

### 한글 → 티커 자동 변환
- 테슬라 → TSLA
- 애플 → AAPL
- 엔비디아 → NVDA
- 삼성전자 → 005930
- ...

## 기술 스택

- **FastAPI**: 비동기 웹 프레임워크
- **FinanceDataReader**: 가격 데이터 수집
- **yfinance**: 재무제표 + DCF 데이터
- **APScheduler**: 백그라운드 스케줄링
