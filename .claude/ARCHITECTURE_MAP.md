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
│   │   ├── signal_service.py            # 점수 계산 + 신호 수집 → list[SignalSchema]
│   │   ├── position_service.py          # 신호 실행 (매수/매도/분할)
│   │   ├── execution_service_v2.py      # 주문 실행, 비중 검사, 알림
│   │   ├── asset_management_service.py  # 예산 관리자 (현금비중·매수예산 계산)
│   │   ├── backtest_service.py          # 백테스트 (RSI 전략)
│   │   └── simulation_service.py        # 전략 시뮬레이션
│   └── trading/
│       ├── execution_service.py         # (Legacy) 실행 서비스
│       ├── order_service.py             # 주문 기록 & 이력
│       └── portfolio_service.py         # 포트폴리오 동기화, 현금 관리
├── scripts/
│   ├── migrate_trailing_high.py         # trailing high 마이그레이션 스크립트
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
| 예산 관리자 | `services/strategy/asset_management_service.py` ★ |
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
  ├── _validate_preconditions() → MarketHourService
  ├── _load_macro_and_assets() → MacroService.get_macro_data()
  │     └── StockMetaRepo.get_30d_avg_regime_score()
  ├── _load_and_sync_portfolio() → PortfolioService.sync_with_kis()
  ├── _run_signals_and_execute()
  │     ├── signal_service._collect_trading_signals()
  │     │     ├── _determine_analysis_markets() → MarketHourService
  │     │     ├── _apply_hard_gates() → _is_fear_market_exception()
  │     │     └── calculate_score()
  │     └── position_service._execute_collected_signals()
  │           ├── _load_execution_config() → SettingsService
  │           ├── _expire_split_orders()
  │           ├── _process_single_signal()
  │           │     ├── _handle_forced_sell() → execution_service_v2._execute_trade_v2()
  │           │     ├── _handle_trailing_stop() → _handle_forced_sell()
  │           │     ├── _handle_profit_take_signal() → execution_service_v2
  │           │     └── _handle_score_trade() → execution_service_v2
  │           └── _check_unmonitored_holdings()
  └── _send_portfolio_report()
        ├── _load_latest_portfolio() → PortfolioService
        └── _filter_report_changes()

execution_service_v2 (주문 실행)
  ├── _execute_buy_order() → TradeResult(executed, spent_krw, spent_usd)
  │     ├── _check_buy_cash_and_entry_conditions()
  │     ├── _compute_buy_market_totals()
  │     ├── _calculate_buy_quantity()
  │     └── KisService.send_order() / send_overseas_order()
  └── _execute_sell_order() → TradeResult
        └── KisService.send_order() / send_overseas_order()

market_data_service (In-Memory TickerState)
  ├── kis_fetcher    # KIS REST 현재가 조회
  └── kis_ws_service # WebSocket 실시간 가격 수신
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

### 핵심 Pydantic 모델 (models/schemas.py)

| 모델 | 필드 요약 | 사용처 |
|------|-----------|--------|
| `HoldingSchema` | ticker, name, quantity, buy_price, current_price, sector | `PortfolioService.load_portfolio()` 반환, 전략 전체 |
| `MacroDataSnapshot` | us_10y_yield, market_regime, vix, fear_greed, indices, economic_indicators | 전략 실행 전 거시 스냅샷 |
| `UserState` | user_id, panic_locks, sell_cooldown, add_buy_cooldown, split_orders, sell_split_orders, trailing_high | `_load_user_state()` 반환, 전략 상태 |
| `SignalSchema` | ticker, state(TickerState), holding(HoldingSchema), score, reasons | `_collect_trading_signals()` 반환 |

> **규칙**: `PortfolioService.load_portfolio()` → `List[HoldingSchema]`. 내부 CRUD(`apply_buy`, `apply_sell`)는 `PortfolioRepo.load_holdings()` raw dict 사용.

### 전략 상태 영속
- `StrategyStateRepo`: user_id별 JSON 컬럼 (sell_cooldown, split_orders 등)
- `StrategyState` ORM 모델 (`models/strategy_state.py`)

### 시장 레짐 (Regime)
- Bull ≥ 65점, Bear ≤ 동적 임계값
- 5개 컴포넌트 (COMPONENT_WEIGHTS: technical:20, vix:25, fng:20, econ:20, other:15) + 경제 국면 modifier(±15) + extreme_fear=True 시 Bear 강제 + 30일 평균 blending(현재 60% + 과거 40%)
- EMA 비교: close.tail(20) 평균 거리 기준
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
| sync_daily_market | 매일 04:00 | Top100 + 보유종목 시세/지표/DCF 동기화 |
| manage_subscriptions | 매일 08:30 KST | WebSocket 구독 갱신 |
| run_trading_strategy | 매 1분 | 전략 실행 루프 |
| kr_close_report | 매일 15:35 KST | KR 장마감 리포트 |
| us_close_report | 매일 06:05 KST | US 장마감 리포트 |
| report_daily_trade_history | 매일 09:00 KST | 일일 매매 내역 Slack 전송 |
| run_rebalancing | 매일 09:10 KST | 포트폴리오 리밸런싱 (현재 pass) |
| refresh_low_tier_prices | 매 5분 | LOW tier 가격 폴링 |
| sync_portfolio_periodic | 매 10분 | 포트폴리오 동기화 |
| econ_0830/0915/1000 | 08:31/09:16/10:01 ET | FRED 경제지표 발표 확인 |
| vix_spike_check | 월-금 9:00-15:00 ET, 30분 | VIX 급등 감지 |

---

**Last Updated**: 2026-03-15
