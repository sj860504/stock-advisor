# Function Reference

**프로젝트**: Python Quant — 주식 분석 및 자동매매 시스템
**목적**: 서비스·레포지토리·모델 함수별 입출력·로직·캐싱·호출 체인 상세 레퍼런스

> 고수준 구조는 `ARCHITECTURE_MAP.md` 참조

---

## 목차

1. [Services — Analysis](#1-services--analysis)
2. [Services — KIS (REST/WebSocket)](#2-services--kis-restweb-socket)
3. [Services — Market Data](#3-services--market-data)
4. [Services — Trading Strategy](#4-services--trading-strategy)
5. [Services — Portfolio & Order](#5-services--portfolio--order)
6. [Services — Notification & Report](#6-services--notification--report)
7. [Services — Config & Meta](#7-services--config--meta)
8. [Repositories](#8-repositories)
9. [ORM Models Schema](#9-orm-models-schema)
10. [Config 설정값 (config.py)](#10-config-설정값-configpy)

---

## 1. Services — Analysis

### analysis_service.py
`services/analysis/analysis_service.py`

| 함수 | 파라미터 | 반환 | 핵심 로직 | 캐싱 |
|------|---------|------|-----------|------|
| `get_comprehensive_report(ticker, user_id)` | str, str="sean" | Optional[ComprehensiveReport] | 현재가→포트폴리오→기술지표→DCF→뉴스→거시→점수 통합 | 없음 |
| `get_formatted_report(ticker)` | str | str | get_comprehensive_report → ReportService 텍스트 변환 | 없음 |
| `_fetch_price_data(token, ticker)` | str, str | Optional[dict] | KIS 국내/해외 분기 현재가 조회 | 없음 |
| `_build_portfolio_summary(ticker, price, user_id)` | str, float, str | tuple | 보유여부 + 평단가 + 수익률 계산 | 없음 |
| `_build_technical_context(ticker)` | str | tuple | 2년 일봉→RSI, EMA, 볼린저밴드 계산 | 없음 |
| `_calculate_dcf_fair(ticker, rfr)` | str, float | float\|'N/A' | Stage1 10년 + Terminal DCF, 불충분 시 N/A | 없음 |
| `_calculate_trade_score(ticker, price, chg, rsi, emas, bb, dcf, user_id, macro)` | 다수 | tuple(score, reasons) | TickerState 임시 생성 → TradingStrategyService.calculate_score | 없음 |

---

### dcf_service.py
`services/analysis/dcf_service.py`

| 함수 | 파라미터 | 반환 | 핵심 로직 | 캐싱 |
|------|---------|------|-----------|------|
| `calculate_dcf(ticker)` | str | float | FinancialService.get_dcf_data → DcfAnalyzer.calculate_fair_value | 없음 |
| `get_dcf_input(ticker, growth_rate)` | str, Optional[float] | tuple | 티커 검증 + DCF 입력 추출, FCF 없으면 ValueError | 없음 |
| `calculate_custom_dcf(ticker, growth_rate, discount_rate, terminal_growth)` | str, Optional[float]×3 | dict | 사용자 파라미터로 DCF 재계산 | 없음 |
| `get_filtered_list(market_type, has_value)` | Optional[str], bool | dict | 전 종목 DCF 목록 → 필터·upside_pct 내림차순 정렬 | 없음 |
| `save_override(ticker, fcf_per_share, beta, growth_rate, fair_value)` | str, float×4 | None | DB 저장 + FinancialService 캐시 무효화 | 캐시 삭제 |

---

### financial_service.py
`services/analysis/financial_service.py`
**클래스 변수**: `_recent_metrics` TTL 600초, `_dcf_input` TTL 1800초

| 함수 | 파라미터 | 반환 | 핵심 로직 | 캐싱 |
|------|---------|------|-----------|------|
| `get_metrics(ticker)` | str | Optional[AnalyzedFinancialMetrics] | 메모리(600s)→DB(1일)→KIS API→DB저장 | 메모리+DB |
| `get_dcf_data(ticker)` | str | Optional[DcfInputData] | 오버라이드→EPS CAGR→yfinance FCF→EPS*PER→KIS 순 폴백 | 메모리(1800s) |
| `_build_yearly_eps_as_cashflow(ticker, years)` | str, int=5 | list[dict] | 재무 이력에서 연도별 최신 EPS 추출 (최대 5개년) | 없음 |
| `_calc_cagr(series)` | list | float | (끝/시작)^(1/기간)-1, -15%~25% 범위 클리핑 | 없음 |
| `_calc_discount_rate_from_volatility(series)` | list | float | 기본9% + 현금흐름 변동성(최대+6%) → 6%~15% | 없음 |
| `get_overrides()` | - | dict | DB DcfOverride 전체 조회 | 없음 |
| `save_override(ticker, override_params)` | str, dict | dict | DB 저장 + 메모리 캐시 업데이트 | 메모리 갱신 |
| `update_dcf_override(ticker, fcf_per_share, beta, growth_rate, fair_value)` | str, float×4 | dict | save_override 래퍼 | 메모리 갱신 |

---

## 2. Services — KIS (REST/WebSocket)

### kis_service.py
`services/kis/kis_service.py`
**클래스 변수**: `_access_token`, `_token_expiry`, `_last_balance_data`, `_req_lock`, `_min_req_interval=0.55s`

| 함수 | 파라미터 | 반환 | 핵심 로직 | 캐싱 |
|------|---------|------|-----------|------|
| `get_access_token()` | - | str | 메모리→파일(2h)→KIS OAuth2 발급 순 | 메모리+파일 |
| `get_headers(tr_id)` | str | dict | Authorization/appkey/appsecret/tr_id 헤더 구성 | 없음 |
| `_throttle_request()` | - | None | 마지막 요청 후 0.55초 대기 (TPS 제한) | 없음 |
| `_is_rate_limited_response(response)` | Response | bool | HTTP429/500 + EGW00201 코드 감지 | 없음 |
| `get_balance()` | - | Optional[dict] | 국내 잔고조회, 재시도3회+1.2배 백오프, 마지막성공 폴백 | 메모리(폴백) |
| `get_overseas_balance()` | - | Optional[dict] | 해외 잔고조회, 여러 TR ID 시도 | 없음 |
| `get_overseas_available_cash()` | - | Optional[float] | USD 가용현금 조회 + SettingsService 저장 | 설정저장 |
| `send_order(ticker, quantity, price, order_type)` | str, int, int=0, str="buy" | dict | 국내 매수/매도 (지정가00/시장가01) | 없음 |
| `send_overseas_order(ticker, quantity, price, order_type, market)` | str, int, float=0, str="buy", str="NASD" | dict | 해외 주문, 재시도3회+1.2배 백오프 | 없음 |
| `send_after_hours_order(ticker, quantity, order_type, ord_dvsn)` | str, int, str="buy", Optional[str] | dict | 사후장 주문 (실전 전용, VTS 미지원) | 없음 |
| `get_financials(ticker, meta)` | str, Optional[dict] | dict | KisFetcher.fetch_domestic_price 래퍼 | 없음 |
| `get_overseas_financials(ticker, market, meta)` | str, str="NASD", Optional[dict] | dict | KisFetcher.fetch_overseas_price 래퍼 | 없음 |
| `_send_domestic_order(ticker, qty, tr_id, ord_dvsn, ord_price, log_tag)` | 다수 | dict | 국내 주문 공통 실행 (재시도3회, TPS 제한 감지+백오프) | 없음 |

---

### kis_ws_service.py
`services/kis/kis_ws_service.py`
**구독**: H0STCNT0 (국내), HDFSUSP0 (해외)

| 함수 | 파라미터 | 반환 | 핵심 로직 | 캐싱 |
|------|---------|------|-----------|------|
| `get_approval_key()` | - | bool | KIS OAuth2 Approval API 접속키 발급 | 메모리 |
| `connect()` | - | None (async) | WS연결→재구독→메시지루프, 지수백오프(5~60초) 자동재연결 | 없음 |
| `subscribe(ticker, market)` | str, str="KRX" | None (async) | 실시간 구독 요청 + MarketDataService.register_ticker | subscribed_tickers |
| `handle_message(msg)` | str | None (async) | 수신 메시지 파싱 (구분자'\|', 헤더체크) | 없음 |
| `parse_realtime_price(ticker, data_str)` | str, str | None | H0STCNT0 파싱 → MarketDataService.on_realtime_data | 없음 |
| `parse_overseas_realtime_price(ticker, data_str)` | str, str | None | HDFSUSP0 파싱 → MarketDataService.on_realtime_data | 없음 |

---

## 3. Services — Market Data

### market_data_service.py
`services/market/market_data_service.py`
**클래스 변수**: `_states: Dict[str,TickerState]`, `_tiers: Dict[str,str]`, `WARMUP_CONCURRENCY=1`

| 함수 | 파라미터 | 반환 | 핵심 로직 | 캐싱 |
|------|---------|------|-----------|------|
| `register_ticker(ticker, name)` | str, str | None | register_batch 위임 | In-Memory |
| `register_batch(tickers)` | list | None | DB 우선 로드 + 부족 종목 백그라운드 warm-up | In-Memory(_states) |
| `_warm_up_data(ticker)` | str | None | Semaphore(=1)로 직렬화, _full_api_warmup 호출 | Semaphore |
| `_full_api_warmup(ticker, state)` | str, TickerState | None | 기초저장→지표계산→최종저장→목표가 산출 (4단계) | 없음 |
| `on_realtime_data(ticker, data)` | str, dict | None | WebSocket 실시간 데이터 수신→state 갱신 + EMA 재계산 | In-Memory(_states) |
| `update_price_from_sync(ticker, price, change_rate)` | str, float, float | None | REST 폴링/포트폴리오 동기화 시 현재가 갱신 | In-Memory(_states) |
| `get_state(ticker)` | str | Optional[TickerState] | 단일 종목 상태 조회 | In-Memory 읽기 |
| `get_all_states()` | - | Dict[str,TickerState] | 전체 종목 상태 조회 | In-Memory 읽기 |
| `prune_states(keep_tickers)` | set | None | 유니버스 외 종목 캐시 제거 | In-Memory 삭제 |
| `set_tiers(high, low)` | set, set | None | HIGH=WebSocket / LOW=REST5분 tier 설정 | In-Memory(_tiers) |
| `get_low_tier_tickers()` | - | List[str] | LOW tier 종목 목록 | In-Memory 읽기 |
| `build_trading_signals(data)` | dict | dict | 과매도/과매수/저평가/EMA200 신호 분류 | 없음 |
| `get_watch_list()` | - | list | 감시 중 종목 목록 (알파벳순) | In-Memory 읽기 |
| `_fetch_basic_price(ticker)` | str | dict | KIS REST 현재가+기초재무 조회 | 없음 |
| `_load_indicators_from_db(financials, state)` | Financials, TickerState | bool | DB 재무→state 적용, 24시간 최신성 확인 | 없음 |
| `_should_skip_by_market_hours(ticker)` | str | bool | 개장시장과 반대 시장 종목 skip | 없음 |

---

### macro_service.py
`services/market/macro_service.py`
**캐싱**: 1시간 In-Memory

| 함수 | 파라미터 | 반환 | 핵심 로직 | 캐싱 |
|------|---------|------|-----------|------|
| `get_macro_data()` | - | dict | VIX/FnG/경제지표/10Y/암호화폐/원자재/국면 통합 | 메모리 1h |
| `invalidate_cache()` | - | None | macro 캐시 강제 초기화 | 캐시 삭제 |
| `refresh_on_release(name, series_ids)` | str, list | dict | 경제지표 발표 → 캐시초기화→재계산→DB저장→Slack | 없음 |
| `get_exchange_rate()` | - | float | 현재 고정값 1400.0 | 없음 |
| `_get_major_indices()` | - | dict | KIS/yfinance 주요지수 조회 (S&P500, Dow, Nasdaq100, KOSPI) | 없음 |
| `_get_crypto_data()` | - | dict | yfinance BTC-USD 2일 데이터 | 없음 |
| `_get_commodity_data()` | - | dict | yfinance Gold(GC=F), Oil(CL=F) | 없음 |
| `_get_us_10y_yield()` | - | float | yfinance ^TNX | 없음 |
| `_get_vix()` | - | float | KIS 또는 yfinance ^VIX | 없음 |
| `_get_fear_greed_index()` | - | int | CNN Fear&Greed API (0~100) | 없음 |
| `_get_economic_indicators()` | - | dict | FRED 14개 지표 병렬조회 (ThreadPoolExecutor, max_workers=8) | 없음 |
| `_get_market_regime(vix, fg, econ, us10y)` | float, int, dict, float | dict | Bull/Bear/Neutral 판단 + 0~100 regime_score (5개 컴포넌트 가중평균) | 없음 |
| `calculate_historical_regime(date_str)` | str | dict | yfinance 역사적 데이터로 특정 날짜 국면 계산 + DB 저장 | 없음 |
| `_get_fred_latest_pair(series_id)` | str | tuple(float,float) | FRED 최신값 + 직전값 반환 | 없음 |

---

### economic_calendar_service.py
`services/market/economic_calendar_service.py`
**상태**: In-Memory `_last_obs_date`로 변경감지

| 함수 | 파라미터 | 반환 | 핵심 로직 |
|------|---------|------|-----------|
| `check_for_new_releases()` | - | list[dict] | 모든 FRED 시리즈 관측일 확인, 기준점보다 최신이면 신규발표 반환 |
| `get_weekly_calendar(days)` | int=7 | list[dict] | 오늘부터 N일 경제지표 예상 발표일정 (ET/KST 시각 포함) |
| `_estimate_next_release_date(release_id, freq)` | str, str | str | 과거 이력으로 다음 발표 날짜 추정 |
| `_get_fred_latest_obs_date(series_id)` | str | str | FRED series/observations 최신 관측일 |
| `_to_et(date_str, time_et)` | str, str | datetime | ET 시간대 datetime 생성 |
| `_et_to_kst(dt_et)` | datetime | datetime | ET → KST 변환 |

---

### market_hour_service.py
`services/market/market_hour_service.py`

| 함수 | 파라미터 | 반환 | 핵심 로직 |
|------|---------|------|-----------|
| `is_kr_market_open(allow_extended)` | bool=False | bool | 평일 + 09:00~15:30 (allow_extended: ~18:00, 실전만) |
| `is_kr_after_hours_open()` | - | bool | 평일 15:40~18:00 |
| `is_us_market_open(allow_extended)` | bool=False | bool | 평일 + 공휴일제외 + 09:30~16:00 (allow_extended: 04:00~20:00) |
| `is_strategy_window_open(allow_extended, pre_open_lead_minutes)` | bool=True, int=60 | bool | KR 08:00~ 또는 US 04:00~ 중 하나라도 열려있으면 True |
| `should_fetch(market)` | str="KR" | bool | KR: 09:00~16:30 / US: 09:30~17:30 (장종료 1시간 허용) |

---

### stock_meta_service.py
`services/market/stock_meta_service.py`
**역할**: Repository 래퍼

| 함수 | 파라미터 | 반환 | SQL | 캐싱 |
|------|---------|------|-----|------|
| `upsert_stock_meta(ticker, **kwargs)` | str, dict | StockMeta | INSERT/UPDATE (ticker 기준) | 없음 |
| `get_stock_meta(ticker)` | str | StockMeta | SELECT | 없음 |
| `get_stock_meta_bulk(tickers)` | list | list[StockMeta] | SELECT IN | 없음 |
| `find_ticker_by_name(name)` | str | Optional[str] | SELECT LIKE (대소문자 무시) | 없음 |
| `save_financials(ticker, metrics, base_date)` | str, dict, datetime | Financials | INSERT or UPDATE (당일 기준) | 없음 |
| `initialize_default_meta(ticker)` | str | StockMeta | INSERT 기본값 | 없음 |
| `get_latest_financials(ticker)` | str | Financials | SELECT ORDER BY base_date DESC LIMIT 1 | 없음 |
| `get_all_latest_dcf()` | - | list[dict] | 서브쿼리로 전 종목 최신 DCF + override 병합 | 없음 |
| `get_financials_history(ticker, limit)` | str, int | list[Financials] | SELECT ORDER BY base_date DESC LIMIT n | 없음 |
| `get_batch_latest_financials(tickers)` | list | dict | 서브쿼리로 최신 1건 일괄 조회 (성능 최적화) | 없음 |
| `upsert_dcf_override(ticker, fcf_per_share, beta, growth_rate, fair_value)` | str, float×4 | DcfOverride | INSERT/UPDATE (ticker PK) | 없음 |
| `get_dcf_override(ticker)` | str | DcfOverride | SELECT | 없음 |
| `get_api_info(api_name, is_vts)` | str, bool | tuple(str,str) | SELECT + VTS/Real 분기 | 없음 |
| `init_api_tr_meta()` | - | int | 21개 API TR ID INSERT (없는 것만) | 없음 |
| `save_market_regime(date_str, regime_data, vix, fear_greed)` | str, dict, float, int | bool | INSERT/UPDATE (date UNIQUE) | 없음 |
| `get_market_regime_history(days)` | int | list[dict] | SELECT ORDER BY date DESC LIMIT days | 없음 |
| `get_regime_for_date(date_str)` | str | dict | SELECT WHERE date=date_str | 없음 |

---

### ticker_service.py
`services/market/ticker_service.py`

| 함수 | 파라미터 | 반환 | 핵심 로직 |
|------|---------|------|-----------|
| `resolve_ticker(name_or_ticker)` | str | Optional[str] | 숫자패턴→알파벳패턴→DB검색(find_ticker_by_name) 3단계 |
| `normalize_ticker(ticker)` | str | str | 대문자+공백제거 |
| `get_market_type(ticker)` | str | str | 6자리숫자/.KS/.KQ→KR, 나머지→US |

---

### news_service.py
`services/market/news_service.py`
**현황**: placeholder 상태 — KIS 뉴스 API 연동 예정

| 함수 | 파라미터 | 반환 | 핵심 로직 |
|------|---------|------|-----------|
| `get_latest_news(ticker, limit)` | str, int | List[dict] | placeholder, KIS API 연동 예정 |
| `summarize_news(ticker, news_list)` | str, List[dict] | str | 뉴스 목록 마크다운 포맷팅 |
| `get_market_summary()` | - | dict | MacroService.get_macro_data 위임 |

---

### data_service.py
`services/data/data_service.py`

| 함수 | 파라미터 | 반환 | 핵심 로직 |
|------|---------|------|-----------|
| `get_top_krx_tickers(limit)` | int=100 | list[str] | KIS 시가총액 순위→ETF제외→메타저장→DB보충 |
| `get_top_us_tickers(limit)` | int=100 | list[str] | KIS NASDAQ/NYSE 순위→시총정렬→메타저장→fallback |
| `get_price_history(ticker, days)` | str, int=300 | pd.DataFrame | KIS 일봉(재귀적100건 이상)→FinanceDataReader 폴백 |
| `sync_daily_market_data(limit)` | int=100 | None | Top100 종목→현재가→일봉→지표→DCF→DB저장 (매일04:00) |
| `_is_fund_like_security(ticker, name, market)` | str, str, str | bool | ETF/ETN/펀드 키워드 필터 |
| `_build_us_fallback_data(limit)` | int=100 | list | 미국 우량주 100+ 고정 fallback 리스트 |

---

## 4. Services — Trading Strategy

> ⚠️ develop-1 리팩토링으로 trading_strategy_service.py 내 많은 함수들이 하위 서비스로 이동됨.
> 현재 trading_strategy_service.py는 오케스트레이터 역할만 수행.

### trading_strategy_service.py
`services/strategy/trading_strategy_service.py`
**역할**: 오케스트레이터 — 하위 서비스에 위임

| 함수 | 파라미터 | 반환 | 핵심 로직 |
|------|---------|------|-----------|
| `run_strategy(user_id)` | str="sean" | None | 유니버스→보유/현금/거시→신호수집→실행→틱매매→리포트 |
| `calculate_score(...)` | 다수 | tuple | **위임**: SignalService.calculate_score |
| `analyze_ticker(...)` | 다수 | dict | **위임**: SignalService.analyze_ticker |
| `get_sector_rebalance_status(user_id)` | str | dict | **위임**: SectorRebalancerService |
| `run_sector_rebalance(user_id)` | str | dict | **위임**: SectorRebalancerService |
| `get_top_weight_overrides()` | - | dict | **위임**: TradeExecutorService (ExecutionServiceV2) |
| `set_top_weight_overrides(overrides)` | dict | dict | **위임**: TradeExecutorService |
| `get_waiting_list(user_id)` | str="sean" | list | BUY/SELL 신호 목록 반환 |
| `get_opportunities(user_id)` | str="sean" | list | get_waiting_list 별칭 |
| `execute_sell(ticker, quantity, user_id)` | str, int=0, str="sean" | dict | 수동 매도 실행 |
| `sell_all_and_rebuy(user_id)` | str="sean" | dict | 전량매도 → run_strategy 재매수 |
| `set_enabled(enabled)` | bool | None | 전략 활성화/비활성화 + DB 영속 저장 |
| `is_enabled()` | - | bool | 메모리 변수 반환 |
| `_restore_enabled_state()` | - | None | 앱 시작 시 DB에서 상태 복원 |
| `_migrate_json_to_db()` | - | None | 원타임: strategy_state.json → DB 마이그레이션 |
| `_load_state(user_id)` | str | dict | StrategyStateRepo에서 유저 상태 로드 |
| `_save_state(state)` | dict | None | StrategyStateRepo에 유저 상태 저장 |
| `_run_tick_trade(user_id, holdings, ...)` | 다수 | bool | 1종목 틱매매: 1시간 저가 추적, TP/SL |
| `_run_signals_and_tick(user_id, ...)` | 다수 | tuple | 신호 수집 + 실행 + 틱매매 합산 |
| `_update_target_universe(user_id, ...)` | str, bool, bool | set | Top100 변경 감지 + 캐시 정리 |
| `_log_intramarket_cash_ratio(...)` | 다수 | None | 시장별 현금 비중 로깅 |
| `_send_portfolio_report(user_id, ...)` | 다수 | None | 매매 전/후 비교 Slack 전송 |

---

### signal_service.py ★
`services/strategy/signal_service.py`
**역할**: 점수 계산 + 신호 수집

| 함수 | 파라미터 | 반환 | 핵심 로직 |
|------|---------|------|-----------|
| `calculate_score(ticker, state, holding, macro, user_state, cash_balance, market_cash_ratio, market_total_krw)` | 다수 | tuple(score, reasons, breakdown) | [A]~[G] 컴포넌트 합산 → 0~100 점수 |
| `analyze_ticker(ticker, state, holding, macro, user_state, cash_balance, exchange_rate, market_total_krw)` | 다수 | dict | calculate_score → BUY/SELL/WAIT 추천 반환 |
| `_collect_trading_signals(holdings, macro_data, user_state, kr_total, us_total_krw, cash_balance, target_cash_kr, target_cash_us, usd_cash, exchange_rate)` | 다수 | list[dict] | 모니터링 티커 전체 신호 수집 (committed cash 차감 포함) |
| `_apply_score_components(ticker, state, holding, macro, user_state, profit_pct, curr_price, regime, thresholds)` | 다수 | tuple(score, reasons, forced_sell, breakdown) | [A]~[G] 컴포넌트 누적 계산 |
| `_score_rsi(rsi, oversold_rsi, overbought_rsi)` | float×3 | tuple(delta, reasons) | RSI [-20~+20] 범위 매핑 |
| `_score_dcf(dcf_value, curr_price)` | float×2 | tuple(delta, reasons) | DCF 저/고평가 스코어링 |
| `_score_technical(state, curr_price, oversold, overbought, dip_pct)` | 다수 | tuple(delta, reasons) | RSI+급락/급등+DCF+EMA200 |
| `_score_portfolio(holding, profit_pct, tp_pct, sl_pct)` | 다수 | tuple(delta, reasons, forced_sell) | 익절/추매/손절 판단 |
| `_score_market_context(macro, regime)` | dict, str | tuple(delta, reasons) | VIX/F&G + BULL/BEAR 보정 |
| `_score_target_prices(state, curr_price)` | TickerState, float | tuple(delta, reasons) | 목표 진입/매도가 트리거 |
| `_score_bonuses(ticker, holding, macro, user_state)` | 다수 | tuple(delta, reasons) | Top10 + 사용자 비중 + 섹터 보너스 |
| `_load_score_thresholds()` | - | dict | SettingsService에서 6개 임계값 로드 |
| `_get_top10_market_cap_tickers()` | - | set[str] | 시총 Top10 캐시 (6h TTL) |
| `_dispatch_analyze_trade(ticker, side, score, reason_str, ...)` | 다수 | None | TradeExecutorService._execute_trade_v2() 위임 |

---

### position_service.py ★
`services/strategy/position_service.py`
**역할**: 신호 실행 (분할 매수/매도/쿨다운 관리)

| 함수 | 파라미터 | 반환 | 핵심 로직 |
|------|---------|------|-----------|
| `_execute_collected_signals(user_id, prepared_signals, holdings, kr_total, us_total_krw, cash_balance, ...)` | 다수 | tuple(executed, executed_tickers) | 실제 주문 실행 (신호 리스트 처리) |
| `_process_single_signal(sig, buy_max, sell_min, take_profit_pct, stop_loss_pct, add_rsi_limit, add_score_limit, ...)` | 다수 | tuple(executed, ticker, spent_krw) | 단일 신호 처리, KRW 소비량 반환 |
| `_handle_score_trade(ticker, holding, score, reason_str, profit_pct, buy_max, sell_min, ...)` | 다수 | bool | 점수 기반 매수/매도 분기 |
| `_handle_profit_take_signal(ticker, holding, profit_pct, take_profit_pct, sell_cooldown, today, ...)` | 다수 | bool | 익절 → 분할 매도 트리거 |
| `_handle_add_buy_signal(ticker, holding, profit_pct, stop_loss_pct, current_rsi, add_rsi_limit, add_score_limit, ...)` | 다수 | bool | 추매 쿨다운/RSI/점수 필터 |
| `_handle_buy_split(ticker, holding, score, reason_str, profit_pct, buy_max, add_buy_cooldown, today, ...)` | 다수 | bool | 신규/분할 매수 로직 |
| `_handle_sell_signal(ticker, holding, score, reason_str, profit_pct, sell_min, sell_cooldown, today, ...)` | 다수 | bool | 점수 기반 매도 + 분할 매도 추적 |
| `_init_split_order(ticker, state, score, cash_balance, current_price, exchange_rate, market_total, today, split_orders)` | 다수 | bool | 분할 매수 초기화 (SplitOrderState 생성) |
| `_execute_split_tranche(ticker, holding, score, reason_str, profit_pct, split_orders, ...)` | 다수 | bool | 분할 매수 1 트랜치 실행 (ceiling division) |
| `_get_sell_split_qty(ticker, holding_qty, sell_split_orders, today)` | 다수 | int | 분할 매도 수량 산출 |
| `_update_sell_split_state(ticker, sold_qty, sell_split_orders)` | 다수 | None | 분할 매도 상태 갱신 |
| `_calculate_committed_cash(split_orders, market)` | dict, str | float | 미집행 분할 주문 예약 현금 합산 |
| `_is_buy_cooldown_active(ticker, today, current_price, add_buy_cooldown)` | 다수 | bool | 쿨다운 체크 (가격 -5% 하락 시 우회 가능) |
| `_check_unmonitored_holdings(prepared_signals, holdings, user_id, ...)` | 다수 | tuple(executed, executed_tickers) | 미모니터링 보유종목 손절/익절 체크 |

---

### execution_service_v2.py ★
`services/strategy/execution_service_v2.py`
**역할**: 주문 실행, 비중 검사, Slack 알림

| 함수 | 파라미터 | 반환 | 핵심 로직 |
|------|---------|------|-----------|
| `_execute_trade_v2(ticker, side, reason, profit_pct, is_holding, score, current_price, market_total, cash_balance, ...)` | 다수 | bool | 메인 주문 실행 (조건 검사 → KIS 주문 → 기록) |
| `_execute_buy_order(ticker, score, profit_pct, is_holding, current_price, market_total, cash_balance, exchange_rate, ...)` | 다수 | tuple(executed, trade_qty) | 매수 주문 (비중 검사 포함) |
| `_execute_sell_order(ticker, score, current_price, holdings, user_id, forced_qty)` | 다수 | tuple(executed, trade_qty) | 매도 주문 (부분/전량) |
| `_passes_allocation_limits(ticker, add_value, holdings, cash_balance, holding, kr_assets, us_assets_krw)` | 다수 | tuple(bool, list) | 시장/섹터 비중 한도 검사 |
| `_calculate_buy_quantity(score, cash_balance, current_price, exchange_rate, is_kr_flag, market_total_krw, usd_cash_krw)` | 다수 | tuple(qty, spent_krw, final_price) | 점수 기반 매수 수량 (고점수=2배 승수) |
| `_check_sector_group_limit(ticker, holding, holdings, exchange_rate)` | 다수 | list[str] | 섹터 비중 초과 시 경고 이유 반환 |
| `_is_panic_market(macro)` | dict | bool | VIX≥25 OR F&G≤30 |
| `_is_cash_ratio_sufficient(ticker, holdings, cash_balance, exchange_rate, target_cash_ratio_kr, target_cash_ratio_us, macro)` | 다수 | bool | 시장별 목표 현금비중 충족 여부 |
| `_get_target_cash_ratio(market, regime_status)` | str, str | float | 레짐별 목표 현금비중 (0.20~0.50) |
| `_calculate_total_assets(holdings, cash_balance, macro_data)` | 다수 | tuple(kr_total, us_total_krw, target_cash_kr, target_cash_us) | 시장별 자산 + 목표현금 산출 |
| `_compute_market_balances(holdings, cash_balance, exchange_rate, kr_assets, us_assets_krw, add_value, market)` | 다수 | tuple | 시장별 현금/자산 계산 |
| `_get_sector_group_weights(holdings, exchange_rate, market)` | list, float, str | dict | 섹터그룹별 비중/편차 |
| `_check_buy_cash_and_entry_conditions(ticker, cash_balance, is_holding, profit_pct, holdings, exchange_rate, ...)` | 다수 | bool | 현금 + 진입 조건 통합 검사 |
| `_check_market_hours(ticker)` | str | bool | 시장 개장 여부 확인 |
| `_send_trade_alert(ticker, side, score, current_price, change_rate, trade_qty, profit_pct, holding, executed)` | 다수 | None | Slack 매매 알림 (실행 시만) |
| `_send_tick_alert(ticker, side, current_price, qty, reason, pnl_pct, holding)` | 다수 | None | Slack 틱매매 알림 |
| `get_top_weight_overrides()` | - | dict | 티커→점수델타 로드 (SettingsService) |
| `set_top_weight_overrides(overrides)` | dict | dict | 티커→점수델타 저장 |
| `_fetch_fresh_us_price(ticker, fallback)` | str, float | float | 미국 주문 전 현재가 갱신 |

---

### sector_rebalancer_service.py ★
`services/strategy/sector_rebalancer_service.py`
**역할**: 섹터 리밸런싱 (매주 월요일 09:20 실행)

| 함수 | 파라미터 | 반환 | 핵심 로직 |
|------|---------|------|-----------|
| `run_sector_rebalance(user_id)` | str | dict | 전체 리밸런싱 실행 (매도 → 매수 순) |
| `get_sector_rebalance_status(user_id)` | str | dict | 섹터 비중 현황 + 리밸런싱 필요 항목 |
| `_execute_rebalance_trades(weights, holdings, kr_total, us_total_krw, ...)` | 다수 | dict | STEP1 매도 + STEP2 매수 실행 |
| `_execute_overweight_sells(weights, holdings, kr_total, us_total_krw, ...)` | 다수 | tuple(sells, sold, skipped, updated_cash) | 초과섹터 매도 |
| `_execute_underweight_buys(weights, holdings, kr_total, us_total_krw, ...)` | 다수 | tuple(buys, bought, skipped) | 부족섹터 매수 |
| `_try_sell_overweight_holding(h, grp, dev, holdings, market_total, cash_balance, ...)` | 다수 | tuple(executed, skipped_entry, cash_delta) | 초과 보유종목 1개 매도 시도 |
| `_buy_best_underweight_candidate(grp, dev, candidates, buy_threshold, ...)` | 다수 | tuple(executed, skipped, bought_entry) | 부족섹터 최우량 후보 매수 |
| `_score_underweight_buy_candidates(grp, all_states, holdings_map, macro, user_state, ...)` | 다수 | list[tuple] | 부족섹터 매수 후보 점수 정렬 |
| `_get_overweight_groups(weights, threshold)` | dict, float | list[tuple] | 편차 내림차순 초과 그룹 목록 |
| `_build_market_rebalance(holdings, exchange_rate, market)` | list, float, str | dict | 시장별 리밸런싱 현황 구성 |
| `_build_rebalance_summary(sold, bought, weights_before, weights_after)` | 다수 | str | Slack 요약 문자열 생성 |
| `_notify_rebalance_slack(summary)` | str | None | Slack 전송 |

---

### backtest_service.py ★
`services/strategy/backtest_service.py`
**역할**: 포트폴리오 백테스트 (RSI 전략)

| 함수 | 파라미터 | 반환 | 핵심 로직 |
|------|---------|------|-----------|
| `run_portfolio_backtest(tickers, years, initial_capital, position_pct, target_cash_ratio, ...)` | 다수 | dict | 멀티 종목 백테스트 (Sharpe, MDD, 승률) |
| `run_rsi_backtest(ticker, years)` | str, int=3 | dict | 단일 종목 RSI 백테스트 |
| `_simulate(df, strategy)` | DataFrame, str | dict | RSI 기반 매매 시뮬레이션 |
| `_load_backtest_data(tickers, years)` | list, int | dict[str, DataFrame] | yfinance 다수 티커 데이터 로드 |
| `_calc_invest_amount(strategy, cash, shares, price)` | str, float, float, float | float | 전략별 투자금액 산출 |
| `_calc_mdd_from_equity(equity_curve)` | list | float | 최대낙폭(MDD) 계산 |

---

## 5. Services — Portfolio & Order

### portfolio_service.py
`services/trading/portfolio_service.py`
**상태**: In-Memory `_last_balance_summary`

| 함수 | 파라미터 | 반환 | 핵심 로직 | 캐싱 |
|------|---------|------|-----------|------|
| `save_portfolio(user_id, holdings, cash_balance)` | str, list, float | bool | PortfolioRepo.save 래퍼 | 없음 |
| `load_portfolio(user_id)` | str | List[dict] | PortfolioRepo.load_holdings | 없음 |
| `load_portfolio_dtos(user_id)` | str | List[PortfolioHoldingDto] | PortfolioRepo.load_holdings → DTO 변환 | 없음 |
| `load_cash(user_id)` | str | float | PortfolioRepo.load_cash | 없음 |
| `sync_with_kis(user_id)` | str | List[dict] | KIS 국내/해외 잔고→DB 업데이트, summary 캐시 저장 | 메모리(summary) |
| `get_last_balance_summary()` | - | dict | _last_balance_summary 반환 | 메모리읽기 |
| `get_usd_cash_balance()` | - | float | KIS 또는 SettingsService 가용 USD 현금 | 없음 |
| `analyze_portfolio(user_id, price_cache)` | str, dict | dict | 한국/미국 수익률 분리 분석 + 환율 적용 | 없음 |
| `build_full_report(user_id, price_cache)` | str, dict | list | 보유 종목 전체 상세 분석 (수익률 내림차순) | 없음 |
| `add_holding_manual(user_id, ticker, quantity, buy_price, name)` | str, str, float, float, str | list | 수동 추가 (평단가 계산 포함) | 없음 |
| `apply_buy(holdings, ticker, quantity, price)` | list, str, float, float | list | 기존 보유 시 평단가 재계산, 없으면 신규 추가 | 없음 |
| `apply_sell(holdings, ticker, quantity)` | list, str, float | list | 수량 차감 후 0 이하이면 제거 (잔고부족→ValueError) | 없음 |
| `apply_trade_action(holdings, ticker, action, quantity, price)` | list, str, str, float, float | list | buy/sell 검증 후 apply_buy/apply_sell 호출 | 없음 |
| `rebalance_portfolio(user_id)` | str | None | _rebalance_logic 위임 (현재 pass) | 없음 |
| `upload_portfolio(content, filename, user_id)` | bytes, str, str | list | 엑셀 파일 파싱 → save_portfolio | 없음 |

---

### order_service.py
`services/trading/order_service.py`

| 함수 | 파라미터 | 반환 | 핵심 로직 |
|------|---------|------|-----------|
| `sell_single_holding(ticker, name, qty, price)` | str, str, int, float | Tuple[bool,str] | is_kr로 국내/해외 분기 → KIS send_order |
| `execute_mass_sell(holdings)` | list | Tuple[int,int,list] | 전 보유 종목 매도 (성공수, 실패수, 실패티커) |
| `record_trade(ticker, order_type, qty, price, result_msg, strategy_name)` | 다수 | TradeHistory | TradeHistoryRepo.record 래퍼 |
| `get_trade_history(limit, market, date)` | int=50, str, str | List[TradeRecordDto] | TradeHistoryRepo.query + _to_dto 변환 |
| `get_trade_history_by_date_range(start_dt, end_dt)` | datetime, datetime | List[TradeRecordDto] | TradeHistoryRepo.query_by_date_range + 변환 |
| `_to_dto(record, holdings_map)` | TradeHistory, dict | TradeRecordDto | 엔티티→DTO 변환 (손익=(매도가-평단가)×수량) |

---

## 6. Services — Notification & Report

### alert_service.py
`services/notification/alert_service.py`
**상태**: In-Memory `_user_alerts`, `_pending_alerts`, `_prev_data`, `_sent_alerts`

| 함수 | 파라미터 | 반환 | 핵심 로직 | 캐싱 |
|------|---------|------|-----------|------|
| `send_slack_alert(message, channel)` | str, str | bool | Slack Webhook POST | 없음 |
| `add_user_alert(alert)` | PriceAlert | None | 티커 자동해석 + _user_alerts 목록 추가 | 메모리 |
| `check_user_alerts()` | - | List[str] | _user_alerts 전체 조건 확인 | 없음 |
| `get_pending_alerts()` | - | list | _pending_alerts 반환 후 비우기 | 메모리 초기화 |
| `check_and_alert(ticker, data)` | str, dict | list | 4개 체크(volatility/rsi/undervalued/ma_crossover) | 없음 |
| `generate_daily_summary(data)` | dict | str | RSI 과매도/과매수 + 급등Top5 요약 리포트 | 없음 |
| `_check_volatility(ticker, data)` | str, dict | list | ±2.5% 급등/급락 감지 (이전 상태 비교) | _prev_data |
| `_check_rsi(ticker, data)` | str, dict | list | RSI>70/RSI<30 감지, 시간당 1회 쿨다운 | _sent_alerts |
| `_check_undervalued(ticker, data)` | str, dict | list | 가격 < DCF×0.8 저평가 감지 | _sent_alerts |
| `_check_ma_crossover(ticker, data)` | str, dict | list | EMA 골든크로스/데드크로스 감지 | _prev_data |

---

### report_service.py
`services/notification/report_service.py`

| 함수 | 파라미터 | 반환 | 핵심 로직 |
|------|---------|------|-----------|
| `format_comprehensive_report(data)` | dict/ComprehensiveReport | str | 종합분석→Slack 텍스트 (종목명/현재가/DCF/RSI/EMA200/거시/결론) |
| `format_hourly_gainers(gainers, macro)` | list, dict | str | 급등종목 + VIX/금리/암호화폐 포함 시간별 리포트 |
| `format_portfolio_report(holdings, cash, states, summary)` | list, float, dict, dict | str | 원화/외화 자산 분리, 초기원금대비 손익, KIS계좌 손익 |
| `format_daily_trade_history(trades, start_dt, end_dt)` | list, datetime, datetime | str | 일일 매매내역 Slack 메시지 (티커별 집계) |
| `_format_kr_holding_line(holding, states)` | dict, dict | str | 국내 보유종목 한 줄 (원화, 평단 대비 수익률) |
| `_format_us_holding_line(holding, states, exchange_rate)` | dict, dict, float | str | 미국 보유종목 한 줄 (달러 + 원화환산) |

---

## 7. Services — Config & Meta & KIS Fetcher

### kis_fetcher.py ★
`services/kis/fetch/kis_fetcher.py`
**역할**: KIS REST API 저수준 데이터 조회 (TR ID 동적 조회)

| 함수 | 파라미터 | 반환 | 핵심 로직 |
|------|---------|------|-----------|
| `fetch_domestic_price(token, ticker, meta)` | str, str, Optional[dict] | dict | 국내 현재가 (주식현재가_시세 TR) |
| `fetch_overseas_detail(token, ticker, meta)` | str, str, Optional[dict] | dict | 해외 상세 시세 (해외주식_상세시세 TR) |
| `fetch_overseas_price(token, ticker, meta)` | str, str, Optional[dict] | dict | 해외 기본 현재가 (해외주식_현재가 TR) |
| `fetch_overseas_ranking(token, excd)` | str, str | dict | 해외 시가총액 순위 (NASD/NYSE) |
| `fetch_domestic_ranking(token, mrkt_div)` | str, str | dict | 국내 순위 (VTS→MasterDataService 폴백) |
| `_get_api_info(api_name)` | str | tuple(tr_id, path) | DB에서 TR ID + 경로 조회 |
| `_get_headers(token, tr_id)` | str, str | dict | KIS 공통 헤더 구성 |
| `_get_price_base_url()` | - | str | 실전/VTS base URL 분기 |
| `_throttle_request()` | - | None | 0.55초 TPS 간격 강제 |
| `_is_rate_limited_response(response)` | Response | bool | 429/500 + EGW00201 감지 |
| `_get_with_retry(url, headers, params, timeout, retries)` | 다수 | Optional[Response] | GET + 지수백오프 재시도 (1.2배) |

---

### settings_service.py
`services/config/settings_service.py`
**클래스 변수**: `_cache` TTL 30초, `DEFAULT_SETTINGS` 54개 키

| 함수 | 파라미터 | 반환 | 핵심 로직 | 캐싱 |
|------|---------|------|-----------|------|
| `init_defaults()` | - | None | 없는 키만 DB 삽입, 구버전 보정, TICK_ENABLED 항상 0 | 없음 |
| `get_setting(key, default)` | str, any | str | 30초TTL→DB→DEFAULT_SETTINGS 순 | 메모리 30s |
| `get_float(key, default)` | str, float=0.0 | float | get_setting + float 변환 | 메모리 30s |
| `get_int(key, default)` | str, int=0 | int | get_setting + int 변환 | 메모리 30s |
| `set_setting(key, value)` | str, str | Optional[Settings] | DB 저장 + 캐시 즉시 무효화 | 캐시 삭제 |
| `get_all_settings()` | - | dict | init_defaults 후 SettingsRepo.get_all() | 없음 |
| `get_tick_settings()` | - | dict | STRATEGY_TICK_* 설정 7개 일괄 조회 | 메모리 30s |
| `update_tick_settings(updates)` | dict | None | set_setting 반복 | 캐시 삭제 |

---

## 8. Repositories

### stock_meta_repo.py ★
`repositories/stock_meta_repo.py`

| 함수 | SQL | 파라미터 | 반환 |
|------|-----|---------|------|
| `upsert_stock_meta(ticker, **kwargs)` | INSERT/UPDATE | str, dict | Optional[StockMeta] |
| `get_stock_meta(ticker)` | SELECT | str | Optional[StockMeta] |
| `get_stock_meta_bulk(tickers)` | SELECT IN | list | list[StockMeta] |
| `get_name_map(tickers)` | SELECT IN | list | dict(ticker→name) |
| `find_ticker_by_name(name)` | SELECT LIKE (대소문자 무시) | str | Optional[str] |
| `get_kr_individual_stocks(existing, limit)` | SELECT | set, int | list[str] (ETF 제외 6자리) |
| `save_financials(ticker, metrics, base_date)` | INSERT or UPDATE | str, dict, datetime | Optional[Financials] |
| `get_latest_financials(ticker)` | SELECT ORDER BY base_date DESC LIMIT 1 | str | Optional[Financials] |
| `get_financials_history(ticker, limit)` | SELECT ORDER BY base_date DESC | str, int | list[Financials] |
| `get_batch_latest_financials(tickers)` | 서브쿼리 일괄 조회 | list | dict[str, Financials] |
| `get_all_latest_dcf(limit)` | 서브쿼리 DCF | int | list[dict] |
| `upsert_api_tr_meta(api_name, **kwargs)` | INSERT/UPDATE | str, dict | Optional[ApiTrMeta] |
| `get_api_meta(api_name)` | SELECT | str | Optional[ApiTrMeta] |
| `upsert_dcf_override(ticker, fcf_per_share, beta, growth_rate, fair_value)` | INSERT/UPDATE | str, float×4 | Optional[DcfOverride] |
| `get_dcf_override(ticker)` | SELECT | str | Optional[DcfOverride] |
| `get_all_dcf_overrides(limit)` | SELECT | int | dict |
| `save_market_regime(date_str, regime_data, vix, fear_greed)` | INSERT/UPDATE (date UNIQUE) | str, dict, float, int | bool |
| `get_market_regime_history(days)` | SELECT ORDER BY date DESC LIMIT | int | list[dict] |
| `get_regime_for_date(date_str)` | SELECT WHERE date= | str | Optional[dict] |

---

### strategy_state_repo.py ★
`repositories/strategy_state_repo.py`
**상태 필드**: sell_cooldown, add_buy_cooldown, panic_locks, split_orders, sell_split_orders, tick_trade

| 함수 | SQL | 파라미터 | 반환 |
|------|-----|---------|------|
| `load(user_id)` | SELECT | str | dict (없으면 {}) |
| `save(user_id, user_state)` | INSERT/UPDATE | str, dict | None |
| `get_field(user_id, field)` | SELECT | str, str | dict |
| `set_field(user_id, field, value)` | UPDATE | str, str, Any | None |
| `_serialize_value(value)` | - | Any | Any (Pydantic→dict 재귀변환) |
| `_deserialize_field(field, raw_json)` | - | str, str | dict (모델 복원) |

---

### portfolio_repo.py
`repositories/portfolio_repo.py`

| 함수 | SQL | 파라미터 | 반환 |
|------|-----|---------|------|
| `save(user_id, holding_dicts, cash_balance)` | INSERT/UPDATE+DELETE+INSERT | str, list, float | bool |
| `load_holdings(user_id)` | SELECT (read-only) | str | List[dict] |
| `load_cash(user_id)` | SELECT | str | float |

---

### settings_repo.py
`repositories/settings_repo.py`

| 함수 | SQL | 파라미터 | 반환 |
|------|-----|---------|------|
| `get(key)` | SELECT | str | Optional[str] |
| `set(key, value, description)` | INSERT or UPDATE | str, str, str="" | Optional[Settings] |
| `get_all()` | SELECT | - | dict |
| `upsert_many(items)` | INSERT (없는 키만) | dict | None |

---

### trade_history_repo.py
`repositories/trade_history_repo.py`

| 함수 | SQL | 파라미터 | 반환 |
|------|-----|---------|------|
| `record(ticker, order_type, qty, price, result_msg, strategy_name)` | INSERT | 다수 | Optional[TradeHistory] |
| `query(market, date, limit)` | SELECT (market=kr: GLOB '[0-9]*') | Optional[str]×2, int=50 | List[TradeHistory] |
| `query_by_date_range(start_dt, end_dt)` | SELECT (timestamp 범위) | datetime×2 | List[TradeHistory] |
| `get_holdings_map(tickers)` | SELECT IN | list | dict |

---

## 9. ORM Models Schema

### StockMeta
`models/stock_meta.py`

| 컬럼 | 타입 | 제약 |
|------|------|------|
| id | Integer PK | auto |
| ticker | String(20) | UNIQUE, INDEX, NOT NULL |
| name_ko | String(100) | nullable |
| name_en | String(100) | nullable |
| market_type | String(20) | nullable (KR/US) |
| exchange_code | String(20) | nullable |
| sector | String(100) | nullable |
| industry | String(100) | nullable |
| api_path | String(200) | nullable |
| api_tr_id | String(50) | nullable |
| api_market_code | String(20) | nullable |
| updated_at | DateTime | default=now |

---

### Financials (1:N ← StockMeta)
`models/stock_meta.py`

| 컬럼 | 타입 | 제약 |
|------|------|------|
| id | Integer PK | auto |
| stock_id | Integer FK | INDEX (→StockMeta.id) |
| name | String(100) | nullable |
| base_date | DateTime | INDEX, NOT NULL |
| per, pbr, roe, eps, bps | Float | nullable |
| dividend_yield | Float | nullable |
| current_price, market_cap | Float | nullable |
| high52, low52, volume, amount | Float | nullable |
| rsi | Float | nullable |
| ema5, ema10, ema20, ema60, ema100, ema120, ema200 | Float | nullable |
| dcf_value | Float | nullable |
| updated_at | DateTime | default=now |

---

### Portfolio / PortfolioHolding
`models/portfolio.py`

| 테이블 | 컬럼 | 타입 | 제약 |
|--------|------|------|------|
| Portfolio | id, user_id(UNIQUE,IDX), cash_balance(def=0.0), updated_at | - | 1:N→PortfolioHolding |
| PortfolioHolding | id, portfolio_id(FK,IDX), ticker(IDX), name, quantity(def=0), buy_price(def=0.0), current_price(def=0.0), sector, updated_at | - | cascade delete-orphan |

---

### TradeHistory
`models/trade_history.py`

| 컬럼 | 타입 | 제약 |
|------|------|------|
| id | Integer PK autoincrement | |
| ticker | String(20) | INDEX, NOT NULL |
| order_type | String(10) | NOT NULL (buy/sell) |
| quantity | Integer | NOT NULL |
| price | Float | NOT NULL |
| result_msg | String(255) | nullable |
| timestamp | DateTime | default=now |
| strategy_name | String(50) | default="manual" |

---

### Settings
`models/settings.py`

| 컬럼 | 타입 | 제약 |
|------|------|------|
| key | String(50) PK | |
| value | String(255) | nullable |
| description | String(255) | nullable |

---

### StrategyState ★
`models/strategy_state.py`

| 컬럼 | 타입 | 비고 |
|------|------|------|
| user_id | String(50) PK | 사용자 ID |
| sell_cooldown | Text | JSON: {ticker: "YYYY-MM-DD"} |
| add_buy_cooldown | Text | JSON: {ticker: BuyCooldownEntry(date, price)} |
| panic_locks | Text | JSON: {ticker: locked_date} |
| tick_trade | Text | JSON: {date, second_done, last_sell_price, price_window} |
| split_orders | Text | JSON: {ticker: SplitOrderState(total_qty, remaining_qty, split_count, start_date, entry_price)} |
| sell_split_orders | Text | JSON: {ticker: SplitSellOrderState(total_qty, remaining_qty, split_count, start_date, splits_done)} |
| updated_at | DateTime | auto-update |

---

### DcfOverride
`models/stock_meta.py`

| 컬럼 | 타입 | 제약 |
|------|------|------|
| ticker | String(20) PK | |
| fcf_per_share, beta, growth_rate, fair_value | Float | nullable |
| updated_at | DateTime | default=now |

---

### MarketRegimeHistory
`models/stock_meta.py`

| 컬럼 | 타입 | 제약 |
|------|------|------|
| id | Integer PK | auto |
| date | String(10) | UNIQUE, INDEX |
| status | String(10) | nullable (Bull/Bear/Neutral) |
| regime_score | Integer | nullable (0~100) |
| vix, fear_greed, us_10y_yield, spx_price, spx_ma200, spx_diff_pct | Float/Integer | nullable |
| components_json | String | nullable |
| created_at | DateTime | default=now |

---

## 10. Config 설정값 (config.py)

### KIS API
| 키 | 기본값 | 설명 |
|----|--------|------|
| KIS_APP_KEY | 필수 | API Key |
| KIS_APP_SECRET | 필수 | API Secret |
| KIS_BASE_URL | https://openapivts.koreainvestment.com:29443 | KIS 기본 URL |
| KIS_WS_URL | ws://ops.koreainvestment.com:21000 | WebSocket URL |
| KIS_ACCOUNT_NO | 필수 | 계좌번호 |
| KIS_IS_VTS | true | 모의투자 여부 |
| KIS_ENABLE_AFTER_HOURS_ORDER | false | 사후장 주문 활성화 |
| KIS_AFTER_HOURS_ORD_DVSN | 81 | 사후장 주문 구분값 |

### 외부 API
| 키 | 기본값 | 설명 |
|----|--------|------|
| SLACK_WEBHOOK_URL | 선택 | Slack 알림 webhook |
| FRED_API_KEY | 선택 | FRED 경제지표 API |
| DATA_GO_KR_API_KEY | 선택 | 공공데이터 포털 API |

### 전략 매매 (DB Settings로 런타임 변경 가능)
| 키 | 기본값 | 설명 |
|----|--------|------|
| STRATEGY_BUY_THRESHOLD_MIN | 30 | 매수 하한 점수 |
| STRATEGY_BUY_THRESHOLD | 40 | 매수 점수 |
| STRATEGY_SELL_THRESHOLD | 70 | 매도 점수 |
| STRATEGY_TARGET_CASH_RATIO | 0.40 | 목표 현금 비중 |
| STRATEGY_PER_TRADE_RATIO | 0.05 | 1회 매매 비중 |
| STRATEGY_SPLIT_COUNT | 3 | 분할 매매 횟수 |
| STRATEGY_STOP_LOSS_PCT | -8.0 | 손절 기준 (%) |
| STRATEGY_TAKE_PROFIT_PCT | 3.0 | 익절 기준 (%) |
| STRATEGY_DIP_BUY_PCT | -5.0 | 급락 매수 기준 (%) |
| STRATEGY_OVERSOLD_RSI | 30.0 | 과매도 RSI 기준 |
| STRATEGY_OVERBOUGHT_RSI | 70.0 | 과매수 RSI 기준 |

### DCF
| 키 | 기본값 | 설명 |
|----|--------|------|
| DCF_EQUITY_RISK_PREMIUM | 0.055 | 주식 위험 프리미엄 (5.5%) |
| DCF_DISCOUNT_RATE_FLOOR | 0.06 | 할인율 하한 |
| DCF_DISCOUNT_RATE_CEIL | 0.15 | 할인율 상한 |
| DCF_DEFAULT_DISCOUNT_RATE | 0.10 | 기본 할인율 |
| DCF_STAGE1_YEARS | 10 | 1단계 고성장 기간 |
| DCF_TERMINAL_GROWTH | 0.03 | 터미널 성장률 |

### 인증
| 키 | 기본값 | 설명 |
|----|--------|------|
| JWT_SECRET | change-me-in-production | JWT 비밀키 |
| JWT_ALGORITHM | HS256 | 알고리즘 |
| JWT_EXPIRE_HOURS | 24 | 만료 시간(시) |
| AUTH_USERNAME | sean | 기본 사용자명 |

---

**Last Updated**: 2026-03-12
