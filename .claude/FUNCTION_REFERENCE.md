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
| `calculate_dcf(ticker)` | str | float | analyst_count >= 5이면 DCF 70% + analyst_target 30% blend. fallback_fair_value 있으면 그대로 반환 | 없음 |
| `get_dcf_input(ticker, growth_rate)` | str, Optional[float] | tuple | 티커 검증 + DCF 입력 추출, FCF 없으면 ValueError | 없음 |
| `calculate_custom_dcf(ticker, growth_rate, discount_rate, terminal_growth)` | str, Optional[float]×3 | dict | 사용자 파라미터로 DCF 재계산 | 없음 |
| `get_filtered_list(market_type, has_value)` | Optional[str], bool | dict | 전 종목 DCF 목록 → 필터·upside_pct 내림차순 정렬 | 없음 |
| `save_override(ticker, fcf_per_share, beta, growth_rate, fair_value)` | str, float×4 | None | DB 저장 + FinancialService 캐시 무효화 | 캐시 삭제 |

---

### financial_service.py
`services/analysis/financial_service.py`
**클래스 변수**: `_recent_metrics` TTL 600초, `_dcf_input` TTL 1800초

**상수**: `GROWTH_RATE_CAP_BY_SECTOR` — 섹터별 FCF 성장률 상한 dict
  {Technology:0.30, Communication Services:0.25, Consumer Cyclical:0.20, Healthcare:0.20,
   Financial Services:0.15, Industrials:0.15, Consumer Defensive:0.12, Energy:0.12,
   Basic Materials:0.12, Utilities:0.10, Real Estate:0.10, DEFAULT:0.20}

| 함수 | 파라미터 | 반환 | 핵심 로직 | 캐싱 |
|------|---------|------|-----------|------|
| `get_metrics(ticker)` | str | Optional[AnalyzedFinancialMetrics] | 메모리(600s)→DB(1일)→KIS API→DB저장 | 메모리+DB |
| `get_dcf_data(ticker)` | str | Optional[DcfInputData] | 오버라이드→EPS CAGR→yfinance FCF→analyst target(analyst_count<3이면 skip)→EPS*PER→KIS 순 폴백. 결과에 _apply_growth_rate_cap() 자동 적용 | 메모리(1800s) |
| `_dcf_from_analyst_target(ticker)` | str | Optional[DcfInputData] | analyst_count < 3이면 None 반환 | 없음 |
| `_apply_growth_rate_cap(ticker, dcf_input)` | str, DcfInputData | DcfInputData | StockMetaRepo.get_stock_meta()로 섹터 조회 → GROWTH_RATE_CAP_BY_SECTOR[sector] 상한 적용 | 없음 |
| `_build_yearly_eps_as_cashflow(ticker, years)` | str, int=5 | list[dict] | 재무 이력에서 연도별 최신 EPS 추출 (최대 5개년) | 없음 |
| `_calc_cagr(series)` | list | float | (끝/시작)^(1/기간)-1, -15%~25% 범위 클리핑 | 없음 |
| `_calc_discount_rate_from_volatility(series)` | list | float | 기본9% + 현금흐름 변동성(최대+6%) → 6%~15% | 없음 |
| `get_overrides()` | - | dict | DB DcfOverride 전체 조회 | 없음 |
| `save_override(ticker, override_params)` | str, dict | dict | DB 저장 + 메모리 캐시 업데이트 | 메모리 갱신 |
| `update_dcf_override(ticker, fcf_per_share, beta, growth_rate, fair_value)` | str, float×4 | dict | save_override 래퍼 | 메모리 갱신 |

---

### yfinance_service.py
`services/analysis/yfinance_service.py`
**캐싱**: 24h In-Memory

| 함수 | 파라미터 | 반환 | 핵심 로직 | 캐싱 |
|------|---------|------|-----------|------|
| `get_fundamentals(ticker, market_type)` | str, str="US" | Optional[YFinanceFundamentals] | 24h 캐시 → _fetch() | 메모리 24h |
| `_fetch(ticker, market_type)` | str, str | Optional[YFinanceFundamentals] | yfinance info 파싱: FCF/share, beta, growth_rate, target_mean_price, analyst_count(numberOfAnalystOpinions) | 없음 |

**YFinanceFundamentals 필드**: fcf_per_share, beta, growth_rate, currency, source_ticker, target_mean_price, `analyst_count: int = 0`

---

## 2. Services — KIS (REST/WebSocket)

### kis_service.py
`services/kis/kis_service.py`
**클래스 변수**: `_access_token`, `_token_expiry`, `_last_balance_data`, `_req_lock`, `_min_req_interval=0.55s`

| 함수 | 파라미터 | 반환 | 핵심 로직 | 캐싱 |
|------|---------|------|-----------|------|
| `get_access_token()` | - | str | 메모리→DB→KIS OAuth2 발급 순 | 메모리+DB |
| `_load_cached_token()` | - | Optional[str] | DB(KIS_ACCESS_TOKEN/KIS_TOKEN_EXPIRY) 조회, 만료 시 None | DB |
| `_request_new_token()` | - | str | KIS OAuth2 발급 → DB 저장 (KIS_ACCESS_TOKEN, KIS_TOKEN_EXPIRY) | DB |
| `_load_cached_real_token()` | - | Optional[str] | DB(KIS_REAL_ACCESS_TOKEN/KIS_REAL_TOKEN_EXPIRY) 조회 | DB |
| `_request_new_real_token()` | - | str | KIS real OAuth2 발급 → DB 저장 | DB |
| `get_real_access_token()` | - | str | live 계정 토큰 반환. has_real_credentials()=False 시 VTS 폴백 | 메모리+DB |
| `get_headers(tr_id)` | str | dict | Authorization/appkey/appsecret/tr_id 헤더 구성 | 없음 |
| `get_real_headers(tr_id)` | str | dict | 시세 조회용 헤더 (live credentials 우선) | 없음 |
| `_throttle_request()` | - | None | 마지막 요청 후 0.55초 대기 (TPS 제한) | 없음 |
| `_is_rate_limited_response(response)` | Response | bool | HTTP429/500 + EGW00201 코드 감지 | 없음 |
| `get_balance()` | - | Optional[dict] | 국내 잔고조회, 재시도3회+1.2배 백오프, 마지막성공 폴백 | 메모리(폴백) |
| `_fetch_one_overseas_page(tr_id, url, params, page_num)` | str, str, dict, int | Optional[dict] | 단일 HTTP GET + 검증. {"holdings","summary","ctx_fk","ctx_nk"} 반환, 실패 시 None | 없음 |
| `_fetch_all_pages_for_tr_id(tr_id, url, base_params)` | str, str, dict | Optional[dict] | 페이지 루프(최대10) + ctx 토큰 업데이트. {"holdings","summary"} 반환, 빈 결과 시 None | 없음 |
| `get_overseas_balance()` | - | Optional[dict] | TR ID 2개 순차 시도(_fetch_all_pages_for_tr_id), 모두 실패 시 stale cache fallback | 메모리(폴백) |
| `get_overseas_available_cash()` | - | Optional[float] | DB에서 TR ID 조회(해외주식_가용현금조회), 첫 보유종목 ticker/exchange 사용, 보유없으면 None 반환 | SettingsService |
| `send_order(ticker, quantity, price, order_type)` | str, int, int=0, str="buy" | dict | 국내 매수/매도 (지정가00/시장가01) | 없음 |
| `send_overseas_order(ticker, quantity, price, order_type, market)` | str, int, float=0, str="buy", str="NASD" | dict | 해외 주문, 재시도3회+1.2배 백오프 | 없음 |
| `send_after_hours_order(ticker, quantity, order_type, ord_dvsn)` | str, int, str="buy", Optional[str]=None | dict | 사후장 주문 (실전 전용, VTS 미지원) | 없음 |
| `get_financials(ticker, meta)` | str, Optional[dict]=None | dict | KisFetcher.fetch_domestic_price 래퍼 | 없음 |
| `get_overseas_financials(ticker, market, meta)` | str, str="NASD", Optional[dict]=None | dict | KisFetcher.fetch_overseas_price 래퍼 | 없음 |
| `_send_domestic_order(ticker, quantity, tr_id, ord_dvsn, ord_price, log_tag)` | str, int, str, str, str, str | dict | 국내 주문 공통 실행 (재시도3회, TPS 제한 감지+백오프) | 없음 |
| `get_overseas_ranking(excd)` | str="NAS" | dict | 해외 시가총액 순위 via KisFetcher | 없음 |
| `get_domestic_trade_history(start_date, end_date)` | str, str | list | KIS API 국내 체결조회. TR ID DB 조회 → `_paginate_trade_history` 위임 | 없음 |
| `get_overseas_trade_history(start_date, end_date)` | str, str | list | KIS API 해외 체결조회. TR ID DB 조회 → `_paginate_trade_history` 위임 | 없음 |
| `get_unfilled_orders_kr()` | - | UnfilledOrdersResult | 국내 미체결 주문 조회 (CCLD_DVSN=02). `_fetch_unfilled_orders` 위임 ★ | 없음 |
| `get_unfilled_orders_us()` | - | UnfilledOrdersResult | 해외 미체결 주문 조회 (CCLD_NCCS_DVSN=01). `_fetch_unfilled_orders` 위임 ★ | 없음 |
| `_fetch_unfilled_orders(url, tr_id, params, output_key, ctx_suffix, market)` | str, str, dict, str, str, str | UnfilledOrdersResult | 미체결 조회 공통 헬퍼. `_paginate_trade_history` 재사용 → UnfilledOrder 파싱 ★ | 없음 |
| `_paginate_trade_history(url, tr_id, params, output_key, ctx_suffix, description)` | str, str, dict, str, str, str | list | 공통 페이지루프 헬퍼. tr_cont 기반 최대10페이지, ctx_area 갱신, output_key 누적 | 없음 |

---

### kis_ws_service.py
`services/kis/kis_ws_service.py`
**구독**: H0STCNT0 (국내), HDFSUSP0 (해외)
**참고**: 인스턴스 메서드 기반 클래스 (classmethod 아님)

| 함수 | 파라미터 | 반환 | 핵심 로직 | 캐싱 |
|------|---------|------|-----------|------|
| `get_approval_key()` | - | bool | KIS OAuth2 Approval API 접속키 발급 (live/VTS 분기) | self.approval_key |
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
| `register_ticker(ticker, name)` | str, str="" | None | register_batch 위임 | In-Memory |
| `register_batch(tickers)` | list | None | DB 우선 로드 + 부족 종목 백그라운드 warm-up | In-Memory(_states) |
| `_warm_up_data(ticker, _force)` | str, bool=False | None | Semaphore(=1)로 직렬화, _full_api_warmup 호출 | Semaphore |
| `_warmup_save_basic(ticker, state, basic_info, df)` | str, TickerState, dict, DataFrame | dict | Phase1: _build_partial_metrics → save_financials. partial_metrics 반환 | 없음 |
| `_warmup_compute_indicators(ticker, state, df, partial_metrics)` | str, TickerState, DataFrame, dict | tuple[snapshot, float, float] | Phase2: IndicatorService + DcfService + state.update_indicators. (snapshot, rsi, dcf_val) 반환 | 없음 |
| `_warmup_save_final(ticker, partial_metrics, snapshot, dcf_val)` | str, dict, snapshot, float | None | Phase3: partial_metrics + snapshot + dcf_val 병합 → save_financials | 없음 |
| `_full_api_warmup(ticker, state)` | str, TickerState | None | 오케스트레이터: _warmup_save_basic → _warmup_compute_indicators → _warmup_save_final → _update_target_prices | 없음 |
| `on_realtime_data(ticker, data)` | str, dict | None | WebSocket 실시간 데이터 수신→state 갱신 + EMA 재계산 | In-Memory(_states) |
| `update_price_from_sync(ticker, price, change_rate)` | str, float, float=None | None | REST 폴링/포트폴리오 동기화 시 현재가 갱신 | In-Memory(_states) |
| `get_state(ticker)` | str | Optional[TickerState] | 단일 종목 상태 조회 | In-Memory 읽기 |
| `get_all_states()` | - | Dict[str, TickerState] | 전체 종목 상태 조회 | In-Memory 읽기 |
| `prune_states(keep_tickers)` | set | None | 유니버스 외 종목 캐시 제거 | In-Memory 삭제 |
| `set_tiers(high_tickers, low_tickers)` | set, set | None | HIGH=WebSocket / LOW=REST5분 tier 설정 | In-Memory(_tiers) |
| `get_low_tier_tickers()` | - | List[str] | LOW tier 종목 목록 | In-Memory 읽기 |
| `get_high_tier_tickers()` | - | List[str] | HIGH tier 종목 목록 | In-Memory 읽기 |
| `build_trading_signals(data)` | dict | dict | 과매도/과매수/저평가/EMA200 신호 분류 | 없음 |
| `build_watch_item(ticker, state)` | str, TickerState | dict | TickerState → WatchItem dict 변환 | 없음 |
| `get_watch_list()` | - | list | 감시 중 종목 목록 (알파벳순) | In-Memory 읽기 |
| `_fetch_basic_price(ticker)` | str | dict | KIS REST 현재가+기초재무 조회 | 없음 |
| `_load_indicators_from_db(financials, state)` | Financials, TickerState | bool | DB 재무→state 적용, 24시간 최신성 확인 | 없음 |
| `_should_skip_by_market_hours(ticker)` | str | bool | 개장시장과 반대 시장 종목 skip | 없음 |

---

### macro_service.py
`services/market/macro_service.py`
**캐싱**: 1시간 In-Memory

**상수**:
- `COMPONENT_WEIGHTS` = {technical:20, vix:25, fng:20, econ:20, other:15}
- `ECONOMIC_PHASES` = {Stagflation:-12, Deflation:-8, Inflation:-5, Reflation:+3, Goldilocks:+8}

| 함수 | 파라미터 | 반환 | 핵심 로직 | 캐싱 |
|------|---------|------|-----------|------|
| `get_macro_data()` | - | dict | VIX/FnG/경제지표/10Y/암호화폐/원자재/국면 통합 | 메모리 1h |
| `get_macro_data_snapshot()` | - | MacroDataSnapshot | get_macro_data() dict → MacroDataSnapshot 변환 + `exchange_rate` 필드 채움 (`get_exchange_rate()` 호출) | 없음 |
| `_fetch_raw_indicators()` | - | tuple | (vix, fear_greed, economic_indicators, us_10y_yield, crypto, commodities) 6개 외부 API 일괄 조회 | 없음 |
| `invalidate_cache()` | - | None | macro 캐시 강제 초기화 | 캐시 삭제 |
| `refresh_on_release(release_name, series_ids)` | str, list | dict | 경제지표 발표 → 캐시초기화→재계산→DB저장→Slack | 없음 |
| `get_exchange_rate()` | - | float | yfinance USD/KRW 환율 (실패 시 1400 폴백) | 없음 |
| `_get_major_indices()` | - | Dict[str, IndexQuote] | KIS/yfinance 주요지수 조회 (S&P500, Dow, Nasdaq100, KOSPI) | 없음 |
| `_get_crypto_data()` | - | Dict[str, CryptoQuote] | yfinance BTC-USD 데이터 | 없음 |
| `_get_commodity_data()` | - | Dict[str, CommodityQuote] | yfinance Gold(GC=F), Oil(CL=F) | 없음 |
| `_get_us_10y_yield()` | - | float | yfinance ^TNX | 없음 |
| `_get_vix()` | - | float | KIS 또는 yfinance ^VIX | 없음 |
| `_get_fear_greed_index()` | - | int | CNN Fear&Greed API (0~100) | 없음 |
| `_get_economic_indicators()` | - | EconomicIndicatorsSnapshot | FRED 14개 지표 병렬조회 (ThreadPoolExecutor) | 없음 |
| `_get_market_regime(vix, fear_greed, economic_indicators, us_10y_yield, historical_avg_score)` | float\|None=None, int\|None=None, dict\|None=None, float\|None=None, float\|None=None | MarketRegimeSchema | `_calculate_all_regime_components` → `_assemble_regime_result` 위임 | 없음 |
| `_assemble_regime_result(close, ema_map, technical_20, ...)` | 다수 | MarketRegimeSchema | `_compute_weighted_score` → `_blend_regime_score` → `_build_regime_schema` 위임. @staticmethod | 없음 |
| `_compute_weighted_score(technical_20, vix_20, fng_20, econ_20, other_20, phase_modifier)` | int×6 | int | 5컴포넌트 합산+phase_modifier, 0~100 클리핑. @staticmethod, 순수 함수 | 없음 |
| `_blend_regime_score(regime_score, historical_avg_score)` | int, float\|None | int | 현재60%+과거40% blending. historical_avg_score=None이면 현재 그대로. @staticmethod | 없음 |
| `_build_regime_schema(blended_score, regime_score, bear_threshold, extreme_fear, close, ema_map, ...)` | 다수 | MarketRegimeSchema | blended_score 기준 Bull/Bear/Neutral 판정 + ma200/diff_pct + 전체 컴포넌트 스키마 구성. @staticmethod | 없음 |
| `_calculate_all_regime_components(close, vix, vix_1m_chg, fear_greed, economic_indicators, us_10y_yield, yield_spread, btc_ret, dxy_ret, gold_ret, oil_ret, ndx_1m_hist)` | Series, float, float\|None, int, EconomicIndicatorsSnapshot, float, float\|None, float\|None, float\|None, float\|None, float\|None, DataFrame=None | RegimeComponents | 5개 점수 + extreme_fear + 경제 국면 계산. 순수 함수 | 없음 |
| `_calc_fng_20(fear_greed)` | int | tuple[int, bool] | (score: 0~20, extreme_fear: bool). @staticmethod | 없음 |
| `_calc_technical_20(close, ndx_1m_hist)` | pd.Series, ndx_1m_hist=None | tuple[int, dict, dict] | (technical_20, tech_detail, ema_map) 반환. EMA alignment + SPX/NDX/2W momentum | 없음 |
| `_determine_economic_phase(inflation_pressure, growth_signal)` | int, int | tuple[str, int] | ECONOMIC_PHASES modifier 범위 확대: Stagflation→-12, Goldilocks→+8 | 없음 |
| `calculate_historical_regime(date_str)` | str | dict | yfinance 역사적 데이터로 특정 날짜 국면 계산 + DB 저장 | 없음 |
| `_get_fred_latest_pair(series_id)` | str\|None | tuple[float\|None, float\|None] | FRED 최신값 + 직전값 반환 | 없음 |

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
| `upsert_stock_meta(ticker, **kwargs)` | str, **kwargs | Optional[StockMeta] | INSERT/UPDATE (ticker 기준) | 없음 |
| `get_stock_meta(ticker)` | str | Optional[StockMeta] | SELECT | 없음 |
| `get_exchange_code(ticker)` | str | str | api_market_code 조회, 없으면 "NASD" 기본값 | 없음 |
| `get_stock_meta_bulk(tickers)` | list | list[StockMeta] | SELECT IN | 없음 |
| `find_ticker_by_name(name)` | str | Optional[str] | SELECT LIKE (대소문자 무시) | 없음 |
| `save_financials(ticker, metrics, base_date)` | str, dict, datetime=None | Optional[Financials] | INSERT or UPDATE (당일 기준) | 없음 |
| `initialize_default_meta(ticker)` | str | Optional[StockMeta] | INSERT 기본값 | 없음 |
| `get_latest_financials(ticker)` | str | Optional[Financials] | SELECT ORDER BY base_date DESC LIMIT 1 | 없음 |
| `get_all_latest_dcf(limit)` | int=1000 | list[dict] | 서브쿼리로 전 종목 최신 DCF + override 병합 | 없음 |
| `get_financials_history(ticker, limit)` | str, int=2500 | list[Financials] | SELECT ORDER BY base_date DESC LIMIT n | 없음 |
| `get_batch_latest_financials(tickers)` | list | dict | 서브쿼리로 최신 1건 일괄 조회 (성능 최적화) | 없음 |
| `upsert_api_tr_meta(api_name, **kwargs)` | str, **kwargs | Optional[ApiTrMeta] | TR ID 정보 저장 | 없음 |
| `get_api_meta(api_name)` | str | Optional[ApiTrMeta] | API 메타 전체 조회 | 없음 |
| `upsert_dcf_override(ticker, fcf_per_share, beta, growth_rate, fair_value)` | str, float, float, float, float | Optional[DcfOverride] | INSERT/UPDATE (ticker PK) | 없음 |
| `get_dcf_override(ticker)` | str | Optional[DcfOverride] | SELECT | 없음 |
| `get_all_dcf_overrides(limit)` | int=1000 | dict | 전체 DcfOverride 조회 | 없음 |
| `get_api_info(api_name, is_vts)` | str, bool=None | tuple[Optional[str], Optional[str]] | SELECT + VTS/Real 분기 | 없음 |
| `get_tr_id(api_name, is_vts)` | str, bool=None | Optional[str] | get_api_info 래퍼 (TR ID만 반환) | 없음 |
| `init_api_tr_meta()` | - | int | API TR ID INSERT (없는 것만). 총 26개. 신규: 해외주식_잔고조회, 해외주식_잔고조회_종합, 해외주식_가용현금조회, 국내주식_체결조회, 해외주식_체결조회 | 없음 |
| `update_market_code(ticker, market_code)` | str, str | None | api_market_code DB 갱신 | 없음 |
| `get_kr_individual_stocks(existing, limit)` | set, int | list[str] | ETF 제외 6자리 KR 개별주 | 없음 |
| `save_market_regime(date_str, regime_data, vix, fear_greed)` | str, dict, float, int | bool | INSERT/UPDATE (date UNIQUE) | 없음 |
| `get_market_regime_history(days)` | int=30 | list[dict] | SELECT ORDER BY date DESC LIMIT days | 없음 |
| `get_regime_for_date(date_str)` | str | dict\|None | SELECT WHERE date=date_str | 없음 |

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
**현황**: KIS 국내주식 뉴스 API 구조 구현 완료. 해외주식 미지원(빈 리스트). TR ID `FHKUP03500100` 실전 테스트 필요.

| 함수 | 파라미터 | 반환 | 핵심 로직 |
|------|---------|------|-----------|
| `get_latest_news(ticker, limit)` | str, int=3 | List[dict] | is_kr → _fetch_kr_news, 해외 → [] |
| `_fetch_kr_news(ticker, limit)` | str, int | List[dict] | KIS API (TR: FHKUP03500100, path: /uapi/domestic-stock/v1/quotations/news-title) |
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
| `sync_daily_market_data(limit)` | int=100 | None | Top100 KR/US + 보유종목 (top에 없는 것만) → 시세/지표/DCF → DB 저장 (매일 04:00) |
| `_get_holding_tickers()` | - | list[tuple[str,str]] | PortfolioRepo.load_holdings('sean') → (ticker, market) 리스트 |
| `_is_fund_like_security(ticker, name, market)` | str, str, str | bool | ETF/ETN/펀드 키워드 필터 |
| `_build_us_fallback_data(limit)` | int=100 | list | 미국 우량주 100+ 고정 fallback 리스트 |

---

## 4. Services — Trading Strategy

> ⚠️ develop-1 리팩토링으로 trading_strategy_service.py 내 많은 함수들이 하위 서비스로 이동됨.
> 현재 trading_strategy_service.py는 오케스트레이터 역할만 수행.

### asset_management_service.py (신규)
`services/strategy/asset_management_service.py`
**역할**: 예산 관리자 — 자산 비중 계산, 매수/매도 예산 결정

| 함수 | 파라미터 | 반환 | 핵심 로직 |
|------|---------|------|-----------|
| `run(user_id, holdings, kr_cash, usd_cash, macro_data, is_kr_open, is_us_open)` | str, List[HoldingSchema], float, float, MacroDataSnapshot, bool=True, bool=True | None | target_ratio 계산 → `_rebalance_market`("KR"/"US") 위임 |
| `_rebalance_market(user_id, market, cash, stock_total, target_ratio, holdings)` | str, str, float, float, float, List[HoldingSchema] | None | gap>0→execute_buy_budget, gap<0→execute_sell_for_cash. KR/US 공통 로직 |
| `_get_target_cash_ratio(regime, fear_greed, holdings)` | MarketRegimeSchema, Optional[float], List[HoldingSchema] | float | fear_greed<10→0.0, Bear→0.20, Neutral/Bull→0.40, 보유종목 30%이상 수익 초과→+10%p, 상한0.60 |
| `_calc_totals(holdings)` | List[HoldingSchema] | Tuple[float, float] | (kr_total_krw, us_total_usd). @staticmethod, 순수 함수, I/O 없음 |
| `_calc_cash_gap(cash, stock_total, target_ratio)` | float, float, float | float | 양수=여유(매수), 음수=부족(매도). @staticmethod, 순수 함수 |
| `_select_sell_candidates(holdings)` | List[HoldingSchema] | List[HoldingSchema] | 수익종목(profit_pct>0) 필터 → 수익률 내림차순. 순수 함수 |
| `_calc_profit_exceeding_ratio(holdings, threshold_pct)` | List[HoldingSchema], float | float | threshold 이상 수익 종목 비율. 순수 함수 |
| `_calc_holding_profit_pct(holding)` | HoldingSchema | float | (current_price - buy_price) / buy_price * 100. @staticmethod, 순수 함수 |

---

### trading_strategy_service.py
`services/strategy/trading_strategy_service.py`
**역할**: 오케스트레이터 — 하위 서비스에 위임

| 함수 | 파라미터 | 반환 | 핵심 로직 |
|------|---------|------|-----------|
| `run_strategy(user_id)` | str="sean" | None | 유니버스→보유/현금/거시→신호수집→실행→리포트 |
| `calculate_score(ticker, state, holding, macro, user_state, cash_balance, market_cash_ratio, market_total_krw)` | str, TickerState, Optional[HoldingSchema], MacroDataSnapshot, UserState, float, float=None, float=0.0 | tuple | **위임**: SignalService.calculate_score |
| `analyze_ticker(ticker, state, holding, macro, user_state, cash_balance, exchange_rate, market_total_krw)` | str, TickerState, Optional[HoldingSchema], MacroDataSnapshot, UserState, float, float, float=0.0 | dict | **위임**: SignalService.analyze_ticker |
| `get_top_weight_overrides()` | - | dict | **위임**: TradeExecutorService (ExecutionServiceV2) |
| `set_top_weight_overrides(overrides)` | dict | dict | **위임**: TradeExecutorService |
| `get_waiting_list(user_id)` | str="sean" | list | BUY/SELL 신호 목록 반환 |
| `get_opportunities(user_id)` | str="sean" | list | get_waiting_list 별칭 |
| `execute_sell(ticker, quantity, user_id)` | str, int=0, str="sean" | dict | 수동 매도 실행 |
| `sell_all_and_rebuy(user_id)` | str="sean" | dict | 전량매도 → run_strategy 재매수 |
| `set_enabled(enabled)` | bool | None | 전략 활성화/비활성화 + DB 영속 저장 |
| `is_enabled()` | - | bool | 메모리 변수 반환 |
| `_restore_enabled_state()` | - | None | 앱 시작 시 DB에서 상태 복원 |
| `_validate_preconditions(user_id)` | str | tuple[bool, bool, bool] | cls._enabled 체크 + 시장 개장 여부 → (can_run, is_kr_open, is_us_open) |
| `_load_and_sync_portfolio(user_id)` | str | tuple[list, float, float, dict] | sync_with_kis + load_portfolio + load_cash + usd_cash |
| `_load_macro_and_assets(holdings, kr_cash)` | list[HoldingSchema], float | tuple[MacroDataSnapshot, float, float, float, float, float] | MacroService.get_macro_data() → MacroDataSnapshot 변환. (snapshot, exchange_rate, kr_total, us_total_krw, target_cash_kr, target_cash_us) |
| `_load_user_state(user_id)` | str | tuple[dict, UserState] | StrategyStateRepo.load(user_id) → UserState(**raw) 변환 |
| `_run_signals_and_execute(user_id, holdings, macro_snapshot, user_state, kr_total, us_total_krw, cash_balance, target_cash_kr, target_cash_us, usd_cash, exchange_rate)` | str, list, MacroDataSnapshot, UserState, float, float, float, float, float, float=0.0, float=1350.0 | tuple[bool, set] | SignalService._collect_trading_signals + PositionService._execute_collected_signals |
| `_load_latest_portfolio(user_id)` | str | tuple[list, float, dict] | KIS 재동기화 후 최신 보유/현금/summary |
| `_filter_report_changes(before_snapshot, after_snapshot, executed_tickers, latest_holdings)` | dict, dict, set, list | tuple[set, list] | 순수함수. before/after 비교 → executed 교차필터 |
| `_update_target_universe(user_id, run_kr, run_us)` | str, bool=True, bool=True | set | Top100 변경 감지 + 캐시 정리 |
| `_log_intramarket_cash_ratio(holdings, cash_balance, usd_cash, exchange_rate, target_cash_kr, target_cash_us)` | list, float, float, float, float, float | None | 시장별 현금 비중 로깅 |
| `_init_strategy_user_state(state, user_id)` | dict, str | UserState | state dict에서 user_id UserState 초기화 또는 복원 |
| `_build_waiting_list_entry(ticker, ticker_state, score, reasons)` | str, TickerState, int, list | dict | 대기목록 단일 항목 dict 생성 |
| `_build_sell_rebuy_result(success_count, fail_count, failed_tickers, strategy_error)` | int, int, list, str=None | dict | sell_all_and_rebuy 결과 dict 생성. 순수 함수 |
| `_send_portfolio_report(user_id, before_snapshot, executed_tickers)` | str, dict, set=None | None | 매매 전/후 비교 Slack 전송 |

---

### signal_service.py ★
`services/strategy/signal_service.py`
**역할**: 점수 계산 + 신호 수집

| 함수 | 파라미터 | 반환 | 핵심 로직 |
|------|---------|------|-----------|
| `calculate_score(ticker, state, holding, macro, user_state, cash_balance, market_cash_ratio, market_total_krw)` | str, TickerState, Optional[HoldingSchema], MacroDataSnapshot, UserState, float, float=None, float=0.0 | tuple(score, reasons, breakdown) | [A]~[G] 컴포넌트 합산 → 0~100 점수. `curr_price <= 0`이면 `(0, ["no_price_data"], {})` 반환 |
| `analyze_ticker(ticker, state, holding, macro, user_state, cash_balance, exchange_rate, market_total_krw)` | str, TickerState, Optional[HoldingSchema], MacroDataSnapshot, UserState, float, float, float=0.0 | dict | calculate_score → BUY/SELL/WAIT 추천 반환 |
| `_collect_trading_signals(holdings, macro_data, user_state, kr_total, us_total_krw, cash_balance, target_cash_kr, target_cash_us, usd_cash, exchange_rate)` | list[HoldingSchema], MacroDataSnapshot, UserState, float, float, float, float, float, float=0.0, float=1350.0 | list[SignalSchema] | 모니터링 티커 전체 신호 수집 (committed cash 차감 포함) |
| `_apply_score_components(ticker, state, holding, macro, user_state, profit_pct, curr_price, regime, thresholds)` | str, TickerState, Optional[HoldingSchema], MacroDataSnapshot, UserState, float, float, str, dict | tuple(score, reasons, forced_sell, breakdown) | [A]~[G] 컴포넌트 누적 계산 |
| `_score_rsi(rsi, oversold_rsi, overbought_rsi)` | float, float, float | tuple(delta, reasons) | RSI [-20~+20] 범위 매핑 |
| `_score_dcf(dcf_value, curr_price)` | float, float | tuple(delta, reasons) | DCF 저/고평가 스코어링 |
| `_score_technical(state, curr_price, oversold_rsi, overbought_rsi, dip_buy_pct)` | TickerState, float, float, float, float | tuple(delta, reasons) | RSI+급락/급등+DCF+EMA200 |
| `_score_portfolio(holding, profit_pct, take_profit_pct, stop_loss_pct)` | Optional[HoldingSchema], float, float, float | tuple(delta, reasons, forced_sell) | 익절/추매/손절 판단 |
| `_score_market_context(macro, regime)` | MacroDataSnapshot, str | tuple(delta, reasons) | VIX/F&G + BULL/BEAR 보정 |
| `_score_target_prices(state, curr_price)` | TickerState, float | tuple(delta, reasons) | 목표 진입/매도가 트리거 |
| `_score_bonuses(ticker, holding, macro, user_state)` | str, Optional[HoldingSchema], MacroDataSnapshot, UserState | tuple(delta, reasons) | Top10 + 사용자 비중 + 섹터 보너스 |
| `_compute_holding_profit_pct(holding, state)` | Optional[HoldingSchema], TickerState | float | (ref_price - buy_price) / buy_price * 100. 보유 없으면 0.0. 순수 함수 |
| `_get_take_profit_pct_by_regime(regime)` | str | float | 레짐별 익절 기준. BULL 7% / NEUTRAL 5% / BEAR 3% |
| `_load_score_thresholds()` | - | dict | SettingsService에서 6개 임계값 로드 (take_profit_pct는 calculate_score에서 레짐별 오버라이드) |
| `_get_top10_market_cap_tickers()` | - | set[str] | 시총 Top10 캐시 (6h TTL) |
| `_dispatch_analyze_trade(ticker, side, score, reason_str, state, profit_pct, market_total, cash_balance, exchange_rate, holdings, user_id, holding, macro)` | str, str, int, str, TickerState, float, float, float, float, list, str, Optional[HoldingSchema], MacroDataSnapshot | None | TradeExecutorService._execute_trade_v2() 위임 |
| `_determine_analysis_markets(allow_extended)` | bool | tuple[bool,bool] | KR/US 분석 여부 판단. (analyze_kr, analyze_us) |
| `_apply_hard_gates(ticker, ticker_state, holding, cash_balance, usd_cash, exchange_rate, kr_total, us_total_krw, target_cash_kr, target_cash_us, macro)` | str, TickerState, Optional[HoldingSchema], float, float, float, float, float, float, float, MacroDataSnapshot=None | bool | True=차단. RSI 하드게이트 + 현금 게이트 (공포장 예외 포함) |
| `_is_fear_market_exception(macro)` | MacroDataSnapshot | bool | fear_greed<20 AND regime=Bear → True (현금 게이트 스킵) |
| `get_latest_signals()` | - | list[SignalSchema] | _cached_signals 반환 (재계산 없음). AssetManagementService용 |
| `_cached_signals` | - | list[SignalSchema] | 클래스 변수. _collect_trading_signals() 마지막 결과 캐시 |

---

### position_service.py ★
`services/strategy/position_service.py`
**역할**: 신호 실행 (분할 매수/매도/쿨다운 관리)

| 함수 | 파라미터 | 반환 | 핵심 로직 |
|------|---------|------|-----------|
| `_execute_collected_signals(user_id, prepared_signals, holdings, kr_total, us_total_krw, cash_balance, target_cash_kr, target_cash_us, macro_data, user_state, usd_cash)` | str, list[SignalSchema], list[HoldingSchema], float, float, float, float, float, MacroDataSnapshot, UserState=None, float=0.0 | tuple(bool, set) | 실제 주문 실행 (신호 리스트 처리) |
| `_process_single_signal(sig, cfg, sell_cooldown, add_buy_cooldown, holdings, user_id, kr_total, us_total_krw, cash_balance, macro_data, target_cash_kr, target_cash_us, split_orders, sell_split_orders, trailing_high)` | SignalSchema, ExecutionConfig, dict, dict, list, str, float, float, float, MacroDataSnapshot, float, float, dict=None, dict=None, dict=None | tuple(bool, Optional[str], float, float) | unpack → `_log_signal_evaluation` → `_route_signal` 위임 |
| `_log_signal_evaluation(u)` | UnpackedSignal | None | Score/RSI/DCF/Reasons 로그 출력. 순수 로그 함수 |
| `_route_signal(u, cfg, sell_cooldown, add_buy_cooldown, holdings, user_id, cash_balance, macro_data, target_cash_kr, target_cash_us, split_orders, sell_split_orders, trailing_high)` | UnpackedSignal, ExecutionConfig, dict, dict, list, str, float, MacroDataSnapshot, float, float, dict, dict, dict | tuple(bool, Optional[str], float, float) | 강제매도→트레일링→익절→추매→점수매매 순서 분기 라우터 |
| `_handle_score_trade(ticker, holding, score, reason_str, profit_pct, buy_max, sell_min, sell_cooldown, add_buy_cooldown, today, state, market_total, cash_balance, exchange_rate, holdings, user_id, macro_data, target_cash_kr, target_cash_us, split_orders, sell_split_orders)` | str, Optional[HoldingSchema], int, str, float, int, int, dict, dict, str, TickerState, float, float, float, list, str, MacroDataSnapshot, float, float, dict=None, dict=None | TradeResult | 점수 기반 매수/매도 분기 |
| `_handle_profit_take_signal(ticker, holding, profit_pct, take_profit_pct, sell_cooldown, today, state, score, market_total, cash_balance, exchange_rate, holdings, user_id, macro_data, target_cash_kr, target_cash_us, sell_split_orders)` | str, HoldingSchema, float, float, dict, str, TickerState, int, float, float, float, list, str, MacroDataSnapshot, float, float, dict=None | bool | 익절 → 분할 매도 트리거. 실행 여부 반환 |
| `_handle_add_buy_signal(ticker, holding, profit_pct, stop_loss_pct, current_rsi, add_rsi_limit, add_score_limit, score, add_buy_cooldown, today, state, market_total, cash_balance, exchange_rate, holdings, user_id, macro_data, target_cash_kr, target_cash_us, split_orders)` | str, HoldingSchema, float, float, float, float, int, int, dict, str, TickerState, float, float, float, list, str, MacroDataSnapshot, float, float, dict=None | TradeResult | 추매 쿨다운/RSI/점수 필터 |
| `_handle_buy_split(ticker, holding, score, reason_str, profit_pct, buy_max, add_buy_cooldown, today, state, market_total, cash_balance, exchange_rate, holdings, user_id, macro_data, target_cash_kr, target_cash_us, split_orders)` | str, Optional[HoldingSchema], int, str, float, int, dict, str, TickerState, float, float, float, list, str, MacroDataSnapshot, float, float, dict | TradeResult | 신규/분할 매수 로직 |
| `_handle_sell_signal(ticker, holding, score, reason_str, profit_pct, sell_min, sell_cooldown, today, state, market_total, cash_balance, exchange_rate, holdings, user_id, macro_data, target_cash_kr, target_cash_us, split_orders, sell_split_orders)` | str, Optional[HoldingSchema], int, str, float, int, dict, str, TickerState, float, float, float, list, str, MacroDataSnapshot, float, float, dict, dict=None | bool | 점수 기반 매도 + 분할 매도 추적. 실행 여부 반환 |
| `_init_split_order(ticker, state, score, cash_balance, current_price, exchange_rate, market_total, today, split_orders)` | str, TickerState, int, float, float, float, float, str, dict | bool | 분할 매수 초기화 (SplitOrderState 생성) |
| `_execute_split_tranche(ticker, holding, score, reason_str, profit_pct, split_orders, current_price_val, market_total, cash_balance, exchange_rate, holdings, user_id, macro_data, target_cash_kr, target_cash_us, add_buy_cooldown, today)` | str, Optional[HoldingSchema], int, str, float, dict, float, float, float, float, list, str, MacroDataSnapshot, float, float, dict, str | TradeResult | 분할 매수 1 트랜치 실행 (ceiling division) |
| `_get_sell_split_qty(ticker, holding_qty, sell_split_orders, today)` | str, int, dict, str | int | 분할 매도 수량 산출 |
| `_update_sell_split_state(ticker, sold_qty, sell_split_orders)` | str, int, dict | None | 분할 매도 상태 갱신 |
| `_calculate_committed_cash(split_orders, market)` | dict, str=None | float | 미집행 분할 주문 예약 현금 합산 |
| `_is_buy_cooldown_active(ticker, today, current_price, add_buy_cooldown)` | str, str, float, dict | bool | 쿨다운 체크 (가격 -5% 하락 시 우회 가능) |
| `_check_unmonitored_holdings(prepared_signals, holdings, user_id, kr_total, us_total_krw, cash_balance, cfg, macro_data, target_cash_kr, target_cash_us, sell_cooldown, sell_split_orders)` | list[SignalSchema], list[HoldingSchema], str, float, float, float, ExecutionConfig, MacroDataSnapshot, float, float, dict, dict | tuple(bool, set) | 필터링+루프 → `_process_unmonitored_holding` 위임 |
| `_process_unmonitored_holding(h, kr_total, us_total_krw, cash_balance, cfg, macro_data, target_cash_kr, target_cash_us, sell_cooldown, sell_split_orders, holdings, user_id)` | HoldingSchema, float, float, float, ExecutionConfig, MacroDataSnapshot, float, float, dict, dict, list, str | tuple(bool, Optional[str]) | 단일 미감시 종목 가격조회+손절/익절 실행. (executed, ticker_or_None) 반환 |
| `execute_buy_budget(user_id, budget_krw, budget_usd, signals)` | str, float, float, list[SignalSchema] | None | score 오름차순 정렬 → budget 소진까지 매수 실행 |
| `execute_sell_for_cash(user_id, need_krw, need_usd, candidates)` | str, float, float, list[HoldingSchema] | None | candidates 순서대로 need 충족까지 매도 실행 |
| `_get_take_profit_pct_by_regime(macro_data)` | MacroDataSnapshot=None | float | 레짐별 익절 기준. BULL 7% / NEUTRAL 5% / BEAR 3% |
| `_load_execution_config(macro_data)` | MacroDataSnapshot=None | ExecutionConfig | SettingsService 설정 일괄 조회 + 레짐별 take_profit_pct → ExecutionConfig 반환 |
| `_sort_signals_by_priority(signals, split_orders)` | list[SignalSchema], dict | list[SignalSchema] | 신규미보유(0)>기존보유(1)>split tranche(2). 순수 함수 |
| `_expire_split_orders(split_orders)` | dict | None | STRATEGY_SPLIT_EXPIRE_DAYS(기본5) 초과 항목 제거. `pop(t, None)` 사용 (KeyError 방지) |
| `_deduct_loop_cash(ticker, spent_krw, spent_usd, cash_balance, usd_cash)` | str, float, float, float, float | tuple[float, float] | KR 매수 시 cash_balance 차감, US 매수 시 usd_cash 차감. 순수 함수 |
| `_unpack_signal(sig, kr_total, us_total_krw)` | SignalSchema, float, float | UnpackedSignal | sig 정형화. forced_sell=stop_loss_hit, profit_pct, market_total 계산. 순수 함수 |
| `_set_panic_lock(ticker, user_state)` | str, UserState | None | 손절 종목 panic_locks에 등록 (당일 날짜). 재매수 3일 차단 ★ |
| `_clear_expired_panic_locks(user_state, expire_days)` | UserState, int=3 | None | 만료된 panic_locks 자동 해제. 루프 시작 시 호출 ★ |
| `_handle_forced_sell(ticker, holding, profit_pct, current_price, market_total, cash_balance, exchange_rate, holdings, user_id, macro_data, target_cash_kr, target_cash_us)` | str, HoldingSchema, float, float, float, float, float, list, str, MacroDataSnapshot, float, float | TradeResult | _execute_trade_v2(side="sell", forced_qty=holding.quantity) 호출. 전량 즉시 매도. 성공 시 _set_panic_lock() ★ |
| `_get_trailing_stop_pct(macro_data)` | MacroDataSnapshot | float | BULL→-7.0 / NEUTRAL/BEAR→-5.0 |
| `_update_trailing_high(ticker, price, trailing_high)` | str, float, dict | None | trailing_high[ticker] = max(기존값, price). 순수 함수 |
| `_handle_trailing_stop(ticker, holding, current_price, trailing_high, macro_data, market_total, cash_balance, exchange_rate, holdings, user_id, target_cash_kr, target_cash_us)` | str, HoldingSchema, float, dict, MacroDataSnapshot, float, float, float, list, str, float, float | Optional[TradeResult] | drawdown = (current - high) / high × 100. drawdown <= threshold 시 _handle_forced_sell() 호출. None if not triggered |

---

### execution_service_v2.py ★
`services/strategy/execution_service_v2.py`
**역할**: 주문 실행, 비중 검사, Slack 알림

| 함수 | 파라미터 | 반환 | 핵심 로직 |
|------|---------|------|-----------|
| `_execute_trade_v2(ticker, side, reason, profit_pct, is_holding, score, current_price, market_total, cash_balance, exchange_rate, holdings, user_id, holding, macro, target_cash_ratio_kr, target_cash_ratio_us, forced_qty)` | str, str, str, float, bool, int, float, float, float, float, Optional[List[HoldingSchema]]=None, str="sean", Optional[HoldingSchema]=None, Optional[MacroDataSnapshot]=None, float=None, float=None, int=None | TradeResult | 메인 주문 실행 (조건 검사 → KIS 주문 → 기록) |
| `_execute_buy_order(..., reason)` | ..., reason: str="" | TradeResult | 매수 주문 (비중 검사 포함). reason → DB result_msg 저장 |
| `_execute_sell_order(..., reason)` | ..., reason: str="" | tuple(executed, trade_qty) | 매도 주문. reason → DB result_msg 저장 |
| `_passes_allocation_limits(ticker, add_value, holdings, cash_balance, holding, kr_assets, us_assets_krw)` | str, float, List[HoldingSchema], float, Optional[HoldingSchema], float, float | tuple(bool, list) | 시장/섹터 비중 한도 검사. `_compute_sector_value_map` + `_check_sector_group_limit` 사용 |
| `_calculate_buy_quantity(score, cash_balance, current_price, exchange_rate, is_kr_flag, market_total_krw, usd_cash_krw)` | int, float, float, float, bool, float=0.0, float=0.0 | tuple(int, float, float) | 점수 기반 매수 수량 (고점수=2배 승수). Returns (total_qty, spent_krw, final_price) |
| `_check_sector_group_limit(ticker, holding, holdings, exchange_rate)` | str, Optional[HoldingSchema], List[HoldingSchema], float | list[str] | 섹터 비중 초과 시 경고 이유 반환 |
| `_is_panic_market(macro)` | MacroDataSnapshot | bool | VIX≥25 OR F&G≤30 |
| `_is_cash_below_target(ticker, holdings, cash_balance, exchange_rate, target_cash_ratio_kr, target_cash_ratio_us, macro)` | str, List[HoldingSchema], float, float, float, float, MacroDataSnapshot | bool | 현금비중이 목표치 이하이면 True → 매수 차단. 패닉장(`_is_panic_market`)이면 항상 False |
| `_get_target_cash_ratio(market, regime_status)` | str, str | float | 레짐별 목표 현금비중 (0.20~0.50) |
| `_calculate_total_assets(holdings, cash_balance, macro_data)` | List[HoldingSchema], float, MacroDataSnapshot | tuple(kr_total, us_total_krw, target_cash_kr, target_cash_us) | 시장별 자산 + 목표현금 산출 |
| `_compute_market_balances(holdings, cash_balance, exchange_rate, kr_assets, us_assets_krw, add_value, market)` | List[HoldingSchema], float, float, float, float, float, str | tuple(kr_market_value, us_market_value_krw, kr_cash, us_cash_krw) | 시장별 현금/자산 계산. `_get_sector_group_weights` 내부에서 호출 |
| `_get_sector_group_weights(holdings, exchange_rate, market)` | List[HoldingSchema], float, str | dict | 섹터그룹별 비중/편차 |
| `_check_buy_cash_and_entry_conditions(ticker, cash_balance, is_holding, profit_pct, holdings, exchange_rate, target_cash_ratio_kr, target_cash_ratio_us, macro)` | str, float, bool, float, List[HoldingSchema], float, float, float, MacroDataSnapshot | bool | 현금 + 진입 조건 통합 검사 |
| `_check_market_hours(ticker)` | str | bool | 시장 개장 여부 확인 |
| `_get_change_rate(ticker)` | str | float | MarketDataService.get_state(ticker).change_rate 반환, 없으면 0.0 |
| `_classify_reason(reason)` | str | str | 내부 reason → 한글 라벨 (손절/익절/에셋 확보/추매/점수기반 등) |
| `_send_trade_alert(..., reason, market_total, cash_balance, exchange_rate)` | ..., reason: str="", market_total: float=0.0, cash_balance: float=0.0, exchange_rate: float=1350.0 | None | Slack 체결 알림. 트리거 라벨 + 총자산/여유 현금 포함 |
| `get_top_weight_overrides()` | - | dict | 티커→점수델타 로드 (SettingsService) |
| `set_top_weight_overrides(overrides)` | dict | dict | 티커→점수델타 저장 |
| `_has_absolute_cash(ticker, cash_balance)` | str, float | bool | KR: cash_balance>0 확인, US: get_usd_cash_balance()>0 확인. False이면 매수 차단 |
| `_passes_add_buy_entry(ticker, is_holding, profit_pct)` | str, bool, float | bool | 보유 종목인 경우 profit_pct <= STRATEGY_ADD_POSITION_BELOW(-5%) 확인. 신규 종목은 항상 True |
| `_compute_group_values(holdings, exchange_rate)` | List[HoldingSchema], float | dict | 보유종목 섹터그룹별 KRW 평가액 합산. US 종목에 exchange_rate 적용 |
| `_classify_holdings_by_deviation(holdings, weights)` | List[HoldingSchema], dict | tuple | (underweight: list, overweight: list). 섹터 편차 기준 분류 |
| `_get_sector_target_weights(market)` | str | dict | Settings 우선, 없으면 SECTOR_TARGET_WEIGHT 기본값 반환 |
| `_refresh_us_price(ticker, current_price)` | str, float | float | US 티커는 _fetch_fresh_us_price() 호출, KR은 그대로 반환 |
| `_place_and_record(ticker, side, qty, price, reason, user_id, buy_price)` | str, str, int, float, str, str, Optional[float] | bool | KIS 주문 전송 + OrderService.record_trade. 성공 시 True |
| `_fetch_fresh_us_price(ticker, fallback)` | str, float | float | 미국 주문 전 현재가 갱신 |

---

### backtest_service.py ★
`services/strategy/backtest_service.py`
**역할**: 포트폴리오 백테스트 (RSI 전략)

| 함수 | 파라미터 | 반환 | 핵심 로직 |
|------|---------|------|-----------|
| `run_portfolio_backtest(tickers, years, initial_capital, position_pct, target_cash_ratio, ...)` | 다수 | dict | `_load_backtest_data` → `_compute_rsi_and_dates` → `_run_simulation_loop` → `_build_backtest_result` 위임 |
| `_compute_rsi_and_dates(price_data)` | dict[str, DataFrame] | tuple(dict[str,Series], list) | RSI 시리즈 계산 + 공통 날짜 인덱스 구성. @staticmethod |
| `_run_simulation_loop(price_data, rsi_data, all_dates, portfolio, tickers, ...)` | dict, dict, list, BtPortfolio, list, float×4, int×2 | tuple(list,list,list,int,int) | 날짜 루프 — Phase1 매도/Phase2 매수 + 스냅샷. (equity_curve, cash_ratio_curve, trades, wins, losses) 반환 |
| `_build_backtest_result(equity_curve, cash_ratio_curve, trades, wins, losses, initial_capital, portfolio, config)` | list,list,list,int,int,float,BtPortfolio,dict | dict | 최종 Sharpe/MDD/win_rate/max_holdings 계산 후 결과 dict 반환 |
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
| `save_portfolio(user_id, holdings, cash_balance)` | str, list, float=None | bool | PortfolioRepo.save 래퍼 | 없음 |
| `load_portfolio(user_id)` | str | List[HoldingSchema] | PortfolioRepo.load_holdings → HoldingSchema 변환 | 없음 |
| `load_portfolio_dtos(user_id)` | str | List[PortfolioHoldingDto] | load_portfolio → DTO 변환 | 없음 |
| `load_cash(user_id)` | str | float | PortfolioRepo.load_cash | 없음 |
| `sync_with_kis(user_id)` | str="sean" | List[HoldingSchema] | KIS 국내/해외 잔고→DB 업데이트, summary 캐시 저장, _sync_in_memory_prices 호출. `HoldingSchema` 객체 리스트 직접 반환 | 메모리(summary) |
| `_sync_in_memory_prices(holdings)` | List[HoldingSchema] | None | 보유종목 current_price → MarketDataService.update_price_from_sync 일괄 전달 | 없음 |
| `get_last_balance_summary()` | - | dict | _last_balance_summary 반환 | 메모리읽기 |
| `get_usd_cash_balance(overseas_balance)` | Optional[dict]=None | float | KIS 가용현금 API → 해외잔고 역산 → SettingsService 순 폴백 | 없음 |
| `analyze_portfolio(user_id, price_cache)` | str, dict | dict | 한국/미국 수익률 분리 분석 + 환율 적용 | 없음 |
| `build_full_report(user_id, price_cache)` | str, dict | list | 보유 종목 전체 상세 분석 (수익률 내림차순). API 반환 시 `{holdings, exchange_rate}` 래핑 ★ | 없음 |
| `add_holding_manual(user_id, ticker, quantity, buy_price, name)` | str, str, float, float, Optional[str]=None | list | 수동 추가 (평단가 계산 포함) | 없음 |
| `apply_buy(holdings, ticker, quantity, price)` | list, str, float, float | list | 기존 보유 시 평단가 재계산, 없으면 신규 추가 | 없음 |
| `apply_sell(holdings, ticker, quantity)` | list, str, float | list | 수량 차감 후 0 이하이면 제거 (잔고부족→ValueError) | 없음 |
| `apply_trade_action(holdings, ticker, action, quantity, price)` | list, str, str, float, float | list | buy/sell 검증 후 apply_buy/apply_sell 호출 | 없음 |
| `rebalance_portfolio(user_id)` | str="sean" | dict | _rebalance_logic 위임 | 없음 |
| `_rebalance_logic(user_id)` | str | dict | sync_with_kis → 섹터별 비중 계산 → 로그 출력 | 없음 |
| `_extract_float(data, *keys)` | dict, *str | float | 딕셔너리에서 첫 번째 유효한 float 반환. "0" 문자열 트루시 버그 방지 | 없음 |
| `_extract_holding_fields(h)` | HoldingSchema\|dict | tuple | HoldingSchema 또는 dict에서 공통 필드 추출 | 없음 |
| `_parse_balance_holdings(balance_data, existing_sector_map)` | dict, dict | tuple[List[HoldingSchema], Dict[str, HoldingSchema]] | KIS 잔고 데이터 → KR holdings 리스트 + US us_by_ticker dict 파싱 | 없음 |
| `_apply_overseas_balance_override(overseas_balance, us_by_ticker, existing_sector_map)` | dict, Dict[str, HoldingSchema], dict | None | 해외 잔고로 US holdings 덮어쓰기 (in-place) | 없음 |
| `_extract_kr_cash_from_summary(balance_data)` | dict | tuple[dict, float] | 최적 summary 항목 선택 + D+2 예수금(=실질 현금) 추출 | 없음 |
| `_resolve_us_holdings(overseas_balance, us_by_ticker, existing_us_map, existing_sector_map)` | Optional[dict], dict, dict, dict | List[HoldingSchema] | stale 여부에 따라 US holdings 결정 (stale이면 DB 보유 유지) | 없음 |
| `_enrich_summary_with_overseas(summary, overseas_balance)` | dict, Optional[dict] | None | 해외 잔고 요약 → KR summary dict에 주입 (in-place) | 없음 |
| `_calc_kr_holding_results(kr_holdings)` | List[dict] | tuple | (invested, current, results). KR 보유종목 투자금/평가금/결과리스트 계산. @staticmethod | 없음 |
| `_calc_us_holding_results(us_holdings, exchange_rate)` | List[dict], float | tuple | (invested_usd, current_usd, results). US 보유종목 투자금/평가금 계산. @staticmethod | 없음 |
| `calculate_balances(holdings, cash, usd_cash, exchange_rate)` | List[dict], float, float=0.0, float=1350.0 | dict | KR/US 자산 별도 계산. market/kr/us/sector 분리 dict 반환 | 없음 |
| `build_holding_report_row(holding, cached)` | dict, dict | dict | 단일 보유종목 분석 리포트 행 생성 (수익률, DCF upside 포함) | 없음 |
| `update_holding_sector(user_id, ticker, sector)` | str, str, str | list | 보유종목 섹터 수동 변경 | 없음 |

---

### order_service.py
`services/trading/order_service.py`

| 함수 | 파라미터 | 반환 | 핵심 로직 |
|------|---------|------|-----------|
| `sell_single_holding(ticker, name, qty, price)` | str, str, int, float | Tuple[bool, str] | is_kr로 국내/해외 분기 → KIS send_order |
| `execute_mass_sell(holdings)` | list | Tuple[int, int, list] | 전 보유 종목 매도 (성공수, 실패수, 실패티커) |
| `record_trade(ticker, order_type, quantity, price, result_msg, strategy_name, buy_price, status)` | str, str, int, float, str, str="manual", Optional[float]=None, str="pending" | Optional[TradeHistory] | TradeHistoryRepo.record 래퍼. 기본 status='pending' |
| `verify_and_update_pending_orders()` | - | List[OrderVerificationResult] | DB pending → KIS 미체결 API 확인 → filled/pending 갱신 ★ |
| `has_pending_order(ticker, order_type)` | str, str=None | bool | DB에 해당 종목/방향의 pending 주문 존재 여부 ★ |
| `get_trade_history(limit, market, date)` | int=50, Optional[str]=None, Optional[str]=None | List[TradeRecordDto] | TradeHistoryRepo.query + _to_dto 변환 |
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
| `format_comprehensive_report(data)` | Union[dict, ComprehensiveReport] | str | 종합분석→Slack 텍스트 (종목명/현재가/DCF/RSI/EMA200/거시/결론) |
| `format_hourly_gainers(gainers, macro)` | list, Optional[MacroDataSnapshot] | str | 급등종목 + VIX/금리/암호화폐 포함 시간별 리포트 |
| `format_portfolio_report(holdings, cash, states, summary)` | List[HoldingSchema], float, dict=None, dict=None | str | 원화/외화 자산 분리, 초기원금대비 손익, KIS계좌 손익 |
| `format_trade_result_report(changed_holdings, changed_tickers, before_snapshot, after_snapshot, cash, states, summary)` | List[HoldingSchema], set, dict, dict, float, dict=None, dict=None | str | 헤더 없음. `💰 총평가 {total:,.0f} \| 현금 {cash:,.0f}` 라인 |
| `format_daily_trade_history(trades, start_dt, end_dt)` | list, datetime, datetime | str | 일일 매매내역 Slack 메시지 (티커별 집계) |
| `_format_kr_holding_line(holding, states)` | HoldingSchema, dict | str | 국내 보유종목 한 줄 (원화, 평단 대비 수익률) |
| `_format_us_holding_line(holding, states, exchange_rate)` | HoldingSchema, dict, float | str | 미국 보유종목 한 줄 (달러 + 원화환산) |
| `_format_changed_ticker_line(ticker, before_qty, after_qty, changed_holdings)` | str, int, int, list | str | BUY: `🔵 BUY {ticker} {name} {qty}sh @{price}` / SELL: `🔴 SELL {ticker} {name} {qty}sh @{price} \| {pct:+.1f}% {profit}` |
| `_format_kis_summary_lines(t, summary)` | dict, dict | list | KIS 국내순자산·평가손익합계 라인 생성. format_portfolio_report 내부 위임용 |

---

## 7. Services — Config & Meta & KIS Fetcher

### kis_fetcher.py ★
`services/kis/fetch/kis_fetcher.py`
**역할**: KIS REST API 저수준 데이터 조회 (TR ID 동적 조회)

| 함수 | 파라미터 | 반환 | 핵심 로직 |
|------|---------|------|-----------|
| `fetch_domestic_price(token, ticker, meta)` | str, str, dict=None | dict | 국내 현재가 (주식현재가_시세 TR) |
| `fetch_overseas_detail(token, ticker, meta)` | str, str, dict=None | dict | 해외 상세 시세 (해외주식_상세시세 TR) |
| `fetch_overseas_price(token, ticker, meta)` | str, str, dict=None | dict | 해외 기본 현재가 (해외주식_현재가 TR). `_get_with_retry()` 사용 (재시도 지원) |
| `fetch_overseas_ranking(token, excd)` | str, str="NAS" | dict | 해외 시가총액 순위 (NASD/NYSE) |
| `fetch_domestic_ranking(token, mrkt_div)` | str, str="0000" | dict | 국내 순위 (VTS→MasterDataService 폴백) |
| `fetch_daily_price(token, ticker, start_date, end_date)` | str, str, str, str | dict | 국내 일봉 OHLCV (국내주식_일자별시세 TR) |
| `fetch_overseas_daily_price(token, ticker, start_date, end_date)` | str, str, str, str | dict | 해외 일봉 OHLCV. DB에서 TR ID 2개 조회 → 빈 응답 시 _try_exchange_fallback |
| `_build_overseas_daily_params(tr_id, ticker, excd, start_date, end_date, legacy_tr_id)` | str, str, str, str, str, str="" | dict | tr_id==legacy_tr_id 시 FHKST03030100 포맷(fid_*), 그 외 HHDFS 포맷(EXCD/SYMB) |
| `_try_exchange_fallback(ticker, url, headers, params)` | str, str, dict, dict | dict\|None | NAS/NYS/AMS 교대 시도, 성공 시 api_market_code 자동 갱신 |
| `_get_api_info(api_name)` | str | tuple(str, str) | DB에서 TR ID + 경로 조회. @staticmethod |
| `_get_headers(token, tr_id)` | str, str | dict | KIS 공통 헤더 구성. @staticmethod |
| `_get_price_base_url()` | - | str | 실전/VTS base URL 분기. @staticmethod |
| `_get_price_headers(token, tr_id)` | str, str | dict | 시세용 헤더 (live credentials 우선) |
| `_throttle_request()` | - | None | 0.55초 TPS 간격 강제 |
| `_is_rate_limited_response(response)` | Response | bool | 429/500 감지 |
| `_get_with_retry(url, headers, params, timeout, retries)` | str, dict, dict, int=None, int=4 | Optional[Response] | GET + 지수백오프 재시도 (1.2배) |

---

### settings_service.py
`services/config/settings_service.py`
**클래스 변수**: `_cache` TTL 30초, `DEFAULT_SETTINGS` 54개 키

| 함수 | 파라미터 | 반환 | 핵심 로직 | 캐싱 |
|------|---------|------|-----------|------|
| `init_defaults()` | - | None | 없는 키만 DB 삽입, 구버전 보정, TICK_ENABLED 항상 0 | 없음 |
| `get_setting(key, default)` | str, any=None | str | 30초TTL→DB→DEFAULT_SETTINGS 순 | 메모리 30s |
| `get_float(key, default)` | str, float=0.0 | float | get_setting + float 변환. `(ValueError, TypeError)` 예외만 포착 | 메모리 30s |
| `get_int(key, default)` | str, int=0 | int | get_setting + int(float()) 변환. `(ValueError, TypeError)` 예외만 포착 | 메모리 30s |
| `set_setting(key, value)` | str, str | None | DB 저장 + 캐시 즉시 무효화 | 캐시 삭제 |
| `get_all_settings()` | - | list | init_defaults 후 SettingsRepo.get_all() → [{key, value, description}, ...] 리스트 | 없음 |
| `get_tick_settings()` | - | dict | STRATEGY_TICK_* 설정 일괄 조회 | 메모리 30s |
| `update_tick_settings(updates)` | dict | None | set_setting 반복 | 캐시 삭제 |

---

## 8. Repositories

### stock_meta_repo.py ★
`repositories/stock_meta_repo.py`

| 함수 | SQL | 파라미터 | 반환 |
|------|-----|---------|------|
| `upsert_stock_meta(ticker, **kwargs)` | INSERT/UPDATE | str, **kwargs | Optional[StockMeta] |
| `get_stock_meta(ticker)` | SELECT | str | Optional[StockMeta] |
| `get_stock_meta_bulk(tickers)` | SELECT IN | list | list[StockMeta] |
| `get_name_map(tickers)` | SELECT IN | list | dict(ticker→name) |
| `find_ticker_by_name(name)` | SELECT LIKE (대소문자 무시) | str | Optional[str] |
| `get_kr_individual_stocks(existing, limit)` | SELECT | set, int | list[str] (ETF 제외 6자리) |
| `get_all_latest_dcf(limit)` | 서브쿼리 DCF | int=1000 | list[dict] |
| `save_financials(ticker, metrics, base_date)` | INSERT or UPDATE | str, dict, datetime=None | Optional[Financials] |
| `get_latest_financials(ticker)` | SELECT ORDER BY base_date DESC LIMIT 1 | str | Optional[Financials] |
| `get_financials_history(ticker, limit)` | SELECT ORDER BY base_date DESC | str, int=2500 | list[Financials] |
| `get_batch_latest_financials(tickers)` | 서브쿼리 일괄 조회 | list | dict[str, Financials] |
| `upsert_api_tr_meta(api_name, **kwargs)` | INSERT/UPDATE | str, **kwargs | Optional[ApiTrMeta] |
| `get_api_meta(api_name)` | SELECT | str | Optional[ApiTrMeta] |
| `upsert_dcf_override(ticker, fcf_per_share, beta, growth_rate, fair_value)` | INSERT/UPDATE | str, float, float, float, float | Optional[DcfOverride] |
| `get_dcf_override(ticker)` | SELECT | str | Optional[DcfOverride] |
| `get_all_dcf_overrides(limit)` | SELECT | int=1000 | dict |
| `save_market_regime(date_str, regime_data, vix, fear_greed)` | INSERT/UPDATE (date UNIQUE) | str, dict, float, int | bool |
| `get_market_regime_history(days)` | SELECT ORDER BY date DESC LIMIT | int=30 | list[dict] |
| `get_regime_for_date(date_str)` | SELECT WHERE date= | str | Optional[dict] |
| `get_30d_avg_regime_score()` | get_market_regime_history(30) → regime_score 평균 | - | Optional[float] |

---

### strategy_state_repo.py ★
`repositories/strategy_state_repo.py`
**상태 필드**: sell_cooldown, add_buy_cooldown, panic_locks, split_orders, sell_split_orders, **trailing_high** (tick_trade 제거됨)

| 함수 | SQL | 파라미터 | 반환 |
|------|-----|---------|------|
| `load(user_id)` | SELECT | str | dict (없으면 {}) |
| `save(user_id, user_state)` | INSERT/UPDATE | str, dict | None |
| `get_field(user_id, field)` | SELECT | str, str | dict |
| `set_field(user_id, field, value)` | UPDATE | str, str, dict | None |
| `_serialize_value(value)` | - | Any | Any (Pydantic→dict 재귀변환) |
| `_deserialize_field(field, raw_json)` | - | str, str | dict (모델 복원) |

---

### portfolio_repo.py
`repositories/portfolio_repo.py`

| 함수 | SQL | 파라미터 | 반환 |
|------|-----|---------|------|
| `save(user_id, holding_dicts, cash_balance)` | INSERT/UPDATE+DELETE+INSERT | str, list, float=None | bool |
| `load_holdings(user_id)` | SELECT (read-only) | str | list[dict] |
| `load_cash(user_id)` | SELECT | str | float |

---

### settings_repo.py
`repositories/settings_repo.py`

| 함수 | SQL | 파라미터 | 반환 |
|------|-----|---------|------|
| `get(key)` | SELECT | str | Optional[str] — `session_ro()` 사용 |
| `set(key, value, description)` | INSERT or UPDATE | str, str, str="" | Optional[Settings] — `session_scope()` 사용 |
| `get_all()` | SELECT | - | dict — `session_ro()` 사용 |
| `upsert_many(items)` | INSERT (없는 키만) | dict | None — `session_scope()` 사용 |

---

### trade_history_repo.py
`repositories/trade_history_repo.py`

| 함수 | SQL | 파라미터 | 반환 |
|------|-----|---------|------|
| `record(ticker, order_type, quantity, price, result_msg, strategy_name, buy_price)` | INSERT | str, str, int, float, str, str="manual", Optional[float]=None | Optional[TradeHistory] |
| `query(market, date, action, limit)` | SELECT (market=kr: GLOB '[0-9]*') | Optional[str]=None, Optional[str]=None, Optional[str]=None, int=50 | List[TradeHistory] |
| `query_by_date_range(start_dt, end_dt)` | SELECT (timestamp 범위) | datetime, Optional[datetime]=None | List[TradeHistory] |
| `get_holdings_map(tickers)` | SELECT IN | list[str] | dict[str, PortfolioHolding] |
| `_apply_filters(query, market, date, action)` | query, Optional[str], Optional[str], Optional[str]=None | query | market/date/action 필터 적용 |

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
| trailing_high | Text | JSON: {ticker: float} 고점 추적 (trailing stop용) |
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

### DcfInputData (Pydantic)

| 필드 | 타입 | 비고 |
|------|------|------|
| analyst_count | int = 0 | 애널리스트 의견 수 (numberOfAnalystOpinions) |

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
| STRATEGY_SPLIT_EXPIRE_DAYS | 5 | 분할 매수 TTL 만료 기한 (일) |

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

**Last Updated**: 2026-03-15
