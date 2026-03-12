# Architecture Map

**File locations and project structure — develop-1 merge 이후 기준**

---

## Directory Structure

```
001_quant/
├── main.py                              # FastAPI 앱 진입점
├── config.py                            # 환경변수 (KIS_APP_KEY, KIS_IS_VTS 등)
├── data/
│   └── stock_advisor.db                 # SQLite DB
├── logs/
│   └── app.log                          # 애플리케이션 로그
├── models/
│   ├── kis_schemas.py                   # KIS API 응답 스키마
│   ├── portfolio.py                     # Portfolio, PortfolioHolding ORM
│   ├── schemas.py                       # Pydantic 요청/응답 모델
│   ├── settings.py                      # Settings ORM
│   ├── stock_meta.py                    # StockMeta, Financials, DcfOverride, MarketRegimeHistory ORM
│   ├── strategy_state.py                # StrategyState ORM (전략 상태 영속)
│   ├── ticker_state.py                  # TickerState (In-Memory 데이터 구조)
│   └── trade_history.py                 # TradeHistory ORM
├── repositories/
│   ├── database.py                      # SQLAlchemy session_scope, session_ro
│   ├── portfolio_repo.py                # Portfolio CRUD
│   ├── settings_repo.py                 # Settings CRUD
│   ├── stock_meta_repo.py               # StockMeta, Financials, ApiTrMeta, DcfOverride, MarketRegimeHistory CRUD
│   ├── strategy_state_repo.py           # 전략 상태 CRUD (JSON 직렬화)
│   └── trade_history_repo.py            # 거래 내역 CRUD
├── routers/
│   ├── alerts.py                        # /api/alerts — Slack 알림
│   ├── analysis.py                      # /api/analysis — 밸류에이션, DCF, 점수
│   ├── auth.py                          # /api/auth — JWT 인증
│   ├── logs.py                          # /api/logs — 서버 로그
│   ├── market.py                        # /api/market — 시장 데이터, 신호, 매크로
│   ├── portfolio.py                     # /api/portfolio — 포트폴리오 관리
│   ├── reports.py                       # /api/reports — 리포트
│   └── trading.py                       # /api/trading — 자동매매 제어, 주문
├── services/
│   ├── analysis/
│   │   ├── analysis_service.py          # 종합 리포트 생성
│   │   ├── dcf_service.py               # DCF 계산
│   │   ├── financial_service.py         # 재무 지표 (캐싱 포함)
│   │   ├── indicator_service.py         # 기술 지표 (RSI, EMA, BB)
│   │   ├── stock_ranking_service.py     # 종목 랭킹
│   │   └── yfinance_service.py          # yfinance 래퍼
│   ├── base/
│   │   ├── file_service.py              # 파일 I/O 유틸
│   │   └── scheduler_service.py        # APScheduler 잡 + WebSocket 관리
│   ├── config/
│   │   └── settings_service.py         # Settings DB CRUD (30초 캐시)
│   ├── kis/
│   │   ├── kis_service.py               # KIS REST (토큰, 주문, 잔고)
│   │   ├── kis_ws_service.py            # KIS WebSocket (실시간 가격)
│   │   └── fetch/
│   │       └── kis_fetcher.py           # KIS REST 저수준 (가격, 랭킹)
│   ├── market/
│   │   ├── data_service.py              # yfinance & 데이터 집계
│   │   ├── economic_calendar_service.py # 경제지표 일정
│   │   ├── macro_service.py             # 시장 레짐, VIX, F&G, FRED
│   │   ├── market_data_service.py       # In-Memory TickerState 캐시 (핵심!)
│   │   ├── market_hour_service.py       # 개장시간 판단
│   │   ├── market_overview_service.py   # 시장 개요 데이터
│   │   ├── master_data_service.py       # KRX/US 마스터 파일
│   │   ├── news_service.py              # 뉴스 조회 (placeholder)
│   │   ├── stock_meta_service.py        # StockMetaRepo 래퍼
│   │   └── ticker_service.py            # 티커 해석/정규화
│   ├── notification/
│   │   ├── alert_service.py             # Slack 알림
│   │   └── report_service.py            # 리포트 포맷팅
│   ├── strategy/                        # ★ develop-1 리팩토링으로 분리됨
│   │   ├── trading_strategy_service.py  # 오케스트레이터 (sub-service에 위임)
│   │   ├── signal_service.py            # 점수 계산 + 신호 수집
│   │   ├── position_service.py          # 신호 실행 (매수/매도/분할)
│   │   ├── execution_service_v2.py      # 주문 실행, 비중 검사, 알림
│   │   ├── sector_rebalancer_service.py # 섹터 리밸런싱 (매주 월요일)
│   │   ├── backtest_service.py          # 백테스트 (RSI 전략)
│   │   └── simulation_service.py        # 전략 시뮬레이션
│   └── trading/
│       ├── execution_service.py         # (Legacy) 실행 서비스
│       ├── order_service.py             # 주문 기록 & 이력
│       └── portfolio_service.py         # 포트폴리오 동기화, 현금 관리
├── scripts/
│   └── verify_kis_services.py           # KIS 서비스 검증 스크립트
└── static/                              # 프론트엔드 대시보드 파일
```

---

## 핵심 파일 위치

| 역할 | 파일 |
|------|------|
| 매매 전략 오케스트레이터 | `services/strategy/trading_strategy_service.py` |
| 점수 계산 & 신호 수집 | `services/strategy/signal_service.py` ★ |
| 주문 실행 & 비중 검사 | `services/strategy/execution_service_v2.py` ★ |
| 신호 → 매매 실행 | `services/strategy/position_service.py` ★ |
| 섹터 리밸런싱 | `services/strategy/sector_rebalancer_service.py` ★ |
| KIS REST 저수준 | `services/kis/fetch/kis_fetcher.py` ★ |
| In-Memory 가격 캐시 | `services/market/market_data_service.py` |
| 시장 레짐 | `services/market/macro_service.py` |
| DB 세션 | `repositories/database.py` |
| 전략 상태 영속 | `repositories/strategy_state_repo.py` ★ |
| 종목 메타 | `repositories/stock_meta_repo.py` ★ |
| 스케줄러 | `services/base/scheduler_service.py` |

★ = develop-1 머지 이후 신규/분리

---

## 서비스 의존성

```
trading_strategy_service (오케스트레이터)
  ├── signal_service          # 점수 계산, 신호 수집
  │     └── execution_service_v2._execute_trade_v2()
  ├── position_service        # 신호 실행 (분할매수, 익절, 손절)
  │     └── execution_service_v2._execute_trade_v2()
  ├── execution_service_v2    # 주문 조건 검증 + KIS 주문
  │     ├── kis_service       # 실제 주문 전송
  │     ├── portfolio_service # 보유 비중 계산
  │     └── alert_service     # Slack 알림
  └── sector_rebalancer_service # 섹터 리밸런싱
        └── execution_service_v2._execute_trade_v2()

market_data_service (In-Memory TickerState)
  ├── kis_fetcher             # KIS REST 현재가 조회
  └── kis_ws_service          # WebSocket 실시간 가격 수신
```

---

## 핵심 패턴

### Repository Pattern
- DB 접근은 반드시 `repositories/` 경유
- `session_scope` (write), `session_ro` (read-only)
- 직접 DB 조작 금지

### TR ID 관리
- KIS API TR ID는 `StockMetaService.get_api_info(api_name)` 사용
- DB의 `ApiTrMeta` 테이블에 저장 (VTS/실전 분기)
- 하드코딩 TR ID 사용 금지

### In-Memory TickerState 캐시
- `MarketDataService._states: Dict[str, TickerState]`
- HIGH tier: WebSocket 실시간 (최대 20종목/시장)
- LOW tier: 5분 폴링 (나머지)

### 전략 상태 영속
- `StrategyStateRepo`: user_id별 JSON 컬럼 (sell_cooldown, split_orders 등)
- `StrategyState` ORM 모델 (`models/strategy_state.py`)

### 시장 레짐 (Regime)
- Bull ≥ 65점, Bear ≤ 동적 임계값
- 5개 컴포넌트: 기술(EMA200) + VIX + F&G + 경제지표 + 기타
- `MacroService.get_macro_data()` → 1시간 캐시

### DEV_MODE / VTS
- `KIS_IS_VTS=true` → 모의투자 서버 사용
- DEV_MODE → 실제 주문 차단 (로그에 "[DEV MODE]" 출력)

### TPS 제한
- KIS API: 최소 0.55초 간격, 재시도 3회 1.2배 백오프
- `KisFetcher._throttle_request()` / `KisService._throttle_request()`

---

## 스케줄러 잡 요약

| 잡 | 주기 | 역할 |
|----|------|------|
| sync_daily_market | 매일 04:00 | Top100 시세/지표/DCF 동기화 |
| run_trading_strategy | 매 1분 | 전략 실행 루프 |
| weekly_sector_rebalance | 매주 월 09:20 | 섹터 리밸런싱 |
| refresh_low_tier_prices | 매 5분 | LOW tier 가격 폴링 |
| sync_portfolio_periodic | 매 10분 | 포트폴리오 동기화 |
| vix_spike_check | 월-금 9:00-15:00 ET, 30분 | VIX 급등 감지 |

---

**Last Updated**: 2026-03-12
