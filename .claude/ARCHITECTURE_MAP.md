# Architecture Map

**File locations and project structure**

---

## Directory Structure

```
003_quant/
├── main.py                          # FastAPI 앱 진입점
├── .env                             # 환경변수 (DEV_MODE, KIS_IS_VTS 등)
├── data/
│   └── stock_advisor.db             # SQLite DB
├── models/
│   ├── stock.py                     # Stock, TradeHistory 모델
│   └── stock_meta.py                # MarketRegimeHistory 모델
├── services/
│   ├── base/
│   │   ├── scheduler_service.py     # APScheduler 잡 관리
│   │   └── slack_service.py         # Slack 알림
│   ├── market/
│   │   ├── macro_service.py         # Regime 계산, FRED 조회 (핵심!)
│   │   ├── market_data_service.py   # 주가 데이터, yfinance
│   │   ├── stock_meta_service.py    # MarketRegimeHistory DB 저장
│   │   └── economic_calendar_service.py  # FRED 캘린더
│   ├── data/
│   │   ├── kis_token.json           # KIS 토큰 캐시
│   │   └── strategy_state.json      # 전략 상태
│   └── strategy/
│       └── trading_strategy_service.py  # 매매 전략 (KR+US)
├── routers/                         # FastAPI 라우터
└── scripts/                         # 유틸 스크립트
```

## Key File Locations

- **Regime 계산**: `services/market/macro_service.py`
- **스케줄러**: `services/base/scheduler_service.py`
- **매매 전략**: `services/strategy/trading_strategy_service.py`
- **DB 모델**: `models/stock.py`, `models/stock_meta.py`
- **환경변수**: `.env` (DEV_MODE, KIS_IS_VTS, KIS_BASE_URL)

## 핵심 패턴

### Regime 점수 (0~100)
- Bull ≥ 65, Bear ≤ bear_threshold (동적)
- 5개 컴포넌트: 기술(EMA)+VIX+F&G+경제지표+기타

### 스케줄러 잡
- KR 시장: KST 09:00~15:30
- US 시장: KST 23:30~06:00 (ET 09:30~16:00)
- VIX 모니터링: 30분마다

### DEV_MODE
- `DEV_MODE=true` → 모든 주문 차단 (로그에 "[DEV MODE] 실제 주문 차단" 출력)
- `KIS_IS_VTS=true` → 모의투자 서버 사용

---

**Last Updated**: 2026-03-06
