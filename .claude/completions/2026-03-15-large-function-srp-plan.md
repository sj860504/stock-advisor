# 거대 함수 SRP 분리 계획 (2026-03-15)

> 30줄 이상 함수 전수 조사 결과. 심각도 기준 우선순위 정렬.
> **방침**: 기존 동작 100% 유지. Extract Method만 사용. 로직 변경 없음.

---

## 대상 함수 요약

| 순위 | 함수 | 파일 | 줄수 | 책임 수 | 비고 |
|------|------|------|------|--------|------|
| 1 | `run_portfolio_backtest` | backtest_service.py | 187 | 5 | 데이터로드+계산+루프+포지션+통계 |
| 2 | `_assemble_regime_result` | macro_service.py | 67 | 3 | 5컴포넌트계산+가중치+결과구성 |
| 3 | `connect` | kis_ws_service.py | 60 | 4 | 연결+승인+하트비트+메시지루프 |
| 4 | `_evaluate_single_ticker` | scanner_service.py | 54 | 3 | 점수계산+신호생성+분기기록 |
| 5 | `run` | asset_management_service.py | 52 | 3 | KR/US갭계산+매수루프+매도루프 |
| 6 | `_check_unmonitored_holdings` | position_service.py | 51 | 3 | 필터링+가격조회+손절/익절 분기 |
| 7 | `get_domestic_trade_history` | kis_service.py | 50 | 3 | 페이지루프+재시도+ctx토큰+병합 |
| 8 | `get_overseas_trade_history` | kis_service.py | 50 | 3 | 동일 패턴 |
| 9 | `_execute_collected_signals` | position_service.py | 42 | 3 | 정렬+루프+미감시체크+현금갱신 |
| 10 | `_process_single_signal` | position_service.py | 40 | 4 | 강제/트레일링/익절/추매/점수 분기 |
| 11 | `_handle_profit_take_signal` | position_service.py | 34 | 3 | 검증+쿨다운+수량계산+실행+상태 |
| 12 | `_handle_sell_signal` | position_service.py | 33 | 3 | 동일 패턴 |
| 13 | `_handle_score_trade` | position_service.py | 33 | 2 | 거의 분리됨 |
| 14 | `_handle_add_buy_signal` | position_service.py | 31 | 3 | 조건검증+쿨다운+실행+상태저장 |
| 15 | `_collect_trading_signals` | signal_service.py | 30 | 2 | 거의 분리됨 |

> **수정 대상**: 1~12번 (13~15번은 정상 오케스트레이터 또는 경계선)

---

## Phase 1 — `kis_service.get_domestic/overseas_trade_history` (최우선, 중복 제거)

### 현재 구조 (2개 함수 각 50줄, 동일 패턴)
```
get_domestic_trade_history(start_date, end_date)
  ├─ TR ID + URL 구성
  ├─ 파라미터 구성
  └─ for page in range(10):        ← 책임 A: 페이지루프
       ├─ headers 갱신 (page > 0)
       ├─ HTTP GET + 상태 검증      ← 책임 B: 요청
       ├─ output 파싱 + all_records 누적  ← 책임 C: 파싱
       └─ ctx 토큰 업데이트 + break 조건

get_overseas_trade_history(start_date, end_date)
  └─ 완전히 동일한 구조 (output key만 다름)
```

### 분리 후 구조
```
_paginate_trade_history(url, tr_id, params, output_key) -> list
  └─ 페이지루프 + ctx 토큰 처리 + 응답 누적 [공통 헬퍼]

get_domestic_trade_history(start_date, end_date) -> list
  └─ TR ID+URL+파라미터 구성 → _paginate_trade_history 위임

get_overseas_trade_history(start_date, end_date) -> list
  └─ 동일
```

### 추출할 함수

**`_paginate_trade_history(url, tr_id, params, output_key) -> list`**
```
파라미터:
  url: str              — 요청 URL
  tr_id: str            — KIS TR ID
  params: dict          — 초기 쿼리 파라미터
  output_key: str       — 응답에서 추출할 키 ("output1" 또는 "output")
반환: list              — 전체 레코드 (페이지 합산)
로직:
  - for page in range(10): headers 구성 → HTTP GET → rt_cd 검증
  - data[output_key] 누적
  - ctx_fk/ctx_nk 없으면 break, 있으면 params 갱신
```

---

## Phase 2 — `position_service._check_unmonitored_holdings` (51줄)

### 현재 구조
```
_check_unmonitored_holdings(prepared_signals, holdings, ...)
  ├─ 감시 티커 set 구성              ← 책임 A: 필터링
  └─ for h in holdings:
       ├─ ticker/qty 유효성 검사
       ├─ 현재가 조회 (in-memory → holding fallback)  ← 책임 B: 가격 조회
       ├─ profit_pct 계산
       └─ if stop_loss → _handle_forced_sell          ← 책임 C: 실행 분기
          elif take_profit → _handle_profit_take_signal
```

### 분리 후 구조
```
_check_unmonitored_holdings(...)  — ~15줄 (필터링 + 루프만)
  └─ _process_unmonitored_holding(h, ...) -> tuple[bool, Optional[str]]
         └─ 가격조회 + profit_pct 계산 + 실행 분기
```

### 추출할 함수

**`_process_unmonitored_holding(h, monitored_tickers, kr_total, us_total_krw, cash_balance, cfg, macro_data, target_cash_kr, target_cash_us, sell_cooldown, sell_split_orders, holdings, user_id) -> tuple[bool, Optional[str]]`**
```
파라미터:
  h: HoldingSchema
  monitored_tickers: set[str]
  kr_total, us_total_krw: float
  cash_balance: float
  cfg: ExecutionConfig
  macro_data: MacroDataSnapshot
  target_cash_kr, target_cash_us: float
  sell_cooldown: dict
  sell_split_orders: dict
  holdings: list[HoldingSchema]
  user_id: str
반환: tuple[bool, Optional[str]]  — (executed, ticker_if_executed)
로직:
  - ticker/qty 유효성 체크 → None이면 (False, None)
  - 현재가 in-memory → holding fallback
  - buy_price 없으면 (False, None)
  - profit_pct 계산
  - stop_loss → _handle_forced_sell → (result.executed, ticker)
  - take_profit → _handle_profit_take_signal → (executed, ticker)
  - 그 외 → (False, None)
```

---

## Phase 3 — `position_service._process_single_signal` (40줄)

### 현재 구조
```
_process_single_signal(sig, cfg, sell_cooldown, add_buy_cooldown, ...)
  ├─ _unpack_signal()               ← 책임 A: 신호 언팩 (이미 분리됨)
  ├─ 로깅
  ├─ if u.forced_sell:              ← 책임 B: 강제 매도 분기
  │     _handle_forced_sell()
  │     return
  ├─ _update_trailing_high()
  ├─ _handle_trailing_stop()        ← 책임 C: 트레일링 스탑 분기
  │     if executed: return
  ├─ _handle_profit_take_signal()   ← 책임 D: 익절 분기
  │     if executed: return
  ├─ _handle_add_buy_signal()       ← 책임 E: 추매 분기
  │     if executed: return
  └─ _handle_score_trade()          ← 책임 F: 점수 매매 분기
```

### 분리 후 구조
```
_process_single_signal(sig, cfg, ...)  — ~15줄 (라우팅만)
  ├─ u = _unpack_signal()
  ├─ _log_signal_evaluation(u)
  └─ _route_signal(u, cfg, ...) -> tuple[bool, Optional[str], float, float]
       ├─ forced_sell 분기
       ├─ trailing_stop 분기
       ├─ profit_take 분기
       ├─ add_buy 분기
       └─ score_trade 분기
```

### 추출할 함수

**`_log_signal_evaluation(u: UnpackedSignal) -> None`**
```
파라미터:
  u: UnpackedSignal
반환: None
로직:
  - stock_name, dcf_str 추출
  - logger.info(f"🔍 Evaluated {ticker} ...")
```

**`_route_signal(u, cfg, sell_cooldown, add_buy_cooldown, holdings, user_id, cash_balance, macro_data, target_cash_kr, target_cash_us, split_orders, sell_split_orders, trailing_high) -> tuple[bool, Optional[str], float, float]`**
```
파라미터:
  u: UnpackedSignal
  cfg: ExecutionConfig
  sell_cooldown, add_buy_cooldown: dict
  holdings: list[HoldingSchema]
  user_id: str
  cash_balance: float
  macro_data: MacroDataSnapshot
  target_cash_kr, target_cash_us: float
  split_orders, sell_split_orders: dict
  trailing_high: dict
반환: tuple[bool, Optional[str], float, float]
  — (executed, ticker_if_executed, spent_krw, spent_usd)
로직:
  - forced_sell → _handle_forced_sell → return
  - trailing stop → _handle_trailing_stop → return
  - profit_take → _handle_profit_take_signal → return
  - add_buy → _handle_add_buy_signal → return
  - score_trade → _handle_score_trade → return
```

---

## Phase 4 — `asset_management_service.run` (52줄)

### 현재 구조
```
run(user_id, holdings, kr_cash, usd_cash, macro_data, is_kr_open, is_us_open)
  ├─ KR 현금갭 계산 + 신호 조회     ← 책임 A
  ├─ if gap > 0: PositionService.execute_buy_budget()
  ├─ elif gap < 0: execute_sell_for_cash()
  ├─ US 현금갭 계산                  ← 책임 B
  ├─ if gap > 0: execute_buy_budget()
  └─ elif gap < 0: execute_sell_for_cash()
```

### 분리 후 구조
```
run(user_id, holdings, kr_cash, usd_cash, macro_data, is_kr_open, is_us_open)
  ├─ _rebalance_market(user_id, "KR", kr_cash, ...)   ← 책임 A
  └─ _rebalance_market(user_id, "US", usd_cash, ...)  ← 책임 B
```

### 추출할 함수

**`_rebalance_market(user_id, market, cash, stock_total, target_ratio, signals, candidates, exchange_rate) -> None`**
```
파라미터:
  user_id: str
  market: str                     — "KR" 또는 "US"
  cash: float                     — 현재 현금 (KRW 또는 USD)
  stock_total: float              — 보유 종목 총 평가액
  target_ratio: float             — 목표 현금 비중
  signals: list[SignalSchema]     — 매수 후보
  candidates: list[HoldingSchema] — 매도 후보 (수익 종목)
  exchange_rate: float
반환: None
로직:
  - gap = _calc_cash_gap(cash, stock_total, target_ratio)
  - gap > 0 → PositionService.execute_buy_budget(budget=gap)
  - gap < 0 → PositionService.execute_sell_for_cash(need=abs(gap))
```

---

## Phase 5 — `macro_service._assemble_regime_result` (67줄)

### 현재 구조
```
_assemble_regime_result(close, vix, fear_greed, economic_indicators, ...)
  ├─ components = _calculate_all_regime_components(...)   ← 이미 분리됨
  ├─ weighted_score = sum(comp * weight for each)         ← 책임 A: 가중합산
  ├─ blended_score = 현재60% + 과거40%                    ← 책임 B: blending
  ├─ extreme_fear 강제 Bear 처리
  └─ MarketRegimeSchema 객체 구성                          ← 책임 C: 결과 구성
```

### 분리 후 구조
```
_assemble_regime_result(...)  — ~20줄
  ├─ components = _calculate_all_regime_components(...)
  ├─ raw_score = _compute_weighted_score(components) -> float
  ├─ blended_score = _blend_regime_score(raw_score, historical_avg_score) -> float
  └─ _build_regime_schema(blended_score, components, ...) -> MarketRegimeSchema
```

### 추출할 함수

**`_compute_weighted_score(components: RegimeComponents) -> float`**
```
파라미터:
  components: RegimeComponents
반환: float   — 0~100 가중합산 점수
로직:
  - COMPONENT_WEIGHTS × 각 컴포넌트 점수
  - sum / total_weight → 0~100 클리핑
```

**`_blend_regime_score(raw_score: float, historical_avg_score: float) -> float`**
```
파라미터:
  raw_score: float          — 현재 계산 점수
  historical_avg_score: float — 30일 평균 점수
반환: float                 — blended score (현재60% + 과거40%)
로직:
  - historical_avg_score가 유효하면: raw * 0.6 + hist * 0.4
  - 아니면: raw 그대로
```

**`_build_regime_schema(blended_score, components, vix, fear_greed, ...) -> MarketRegimeSchema`**
```
파라미터:
  blended_score: float
  components: RegimeComponents
  vix: float, fear_greed: int
  extreme_fear: bool
  ... (기타 detail 필드들)
반환: MarketRegimeSchema
로직:
  - extreme_fear=True이면 Bear 강제
  - blended_score 기준 Bull/Bear/Neutral 판정
  - MarketRegimeSchema(**fields) 반환
```

---

## Phase 6 — `backtest_service.run_portfolio_backtest` (187줄) ← 가장 복잡

### 현재 구조
```
run_portfolio_backtest(ticker_list, start_date, end_date, initial_cash)
  ├─ 데이터 로드 (yfinance + KIS)     ← 책임 A
  ├─ RSI/EMA 계산                      ← 책임 B
  └─ for date in date_range:            ← 책임 C: 날짜 루프
       ├─ 매도 시그널 평가 + 실행       ← 책임 D
       ├─ 매수 시그널 평가 + 실행       ← 책임 E
       └─ 포지션 스냅샷 기록
  └─ 성과 통계 계산 및 반환            ← 책임 F
```

### 분리 후 구조
```
run_portfolio_backtest(ticker_list, start_date, end_date, initial_cash) — ~25줄
  ├─ price_data = _load_backtest_data(ticker_list, start_date, end_date) -> dict
  ├─ indicators = _compute_backtest_indicators(price_data) -> dict
  ├─ portfolio = _run_backtest_simulation(price_data, indicators, initial_cash) -> dict
  └─ return _build_backtest_result(portfolio) -> dict
```

### 추출할 함수

**`_load_backtest_data(ticker_list, start_date, end_date) -> dict[str, DataFrame]`**
```
파라미터:
  ticker_list: list[str]
  start_date: str         — "YYYY-MM-DD"
  end_date: str
반환: dict[str, DataFrame]  — {ticker: price_df}
```

**`_compute_backtest_indicators(price_data) -> dict[str, dict]`**
```
파라미터:
  price_data: dict[str, DataFrame]
반환: dict[str, dict]  — {ticker: {rsi: Series, ema: dict, ...}}
```

**`_run_backtest_simulation(price_data, indicators, initial_cash) -> BacktestState`**
```
파라미터:
  price_data: dict[str, DataFrame]
  indicators: dict[str, dict]
  initial_cash: float
반환: BacktestState (dataclass) — {cash, positions, trade_log, daily_values}
로직:
  - date 루프
  - _evaluate_sell_signals_for_date()
  - _evaluate_buy_signals_for_date()
  - 스냅샷 기록
```

**`_build_backtest_result(state: BacktestState) -> dict`**
```
파라미터:
  state: BacktestState
반환: dict  — {total_return, sharpe, max_drawdown, trade_log, ...}
```

---

## 작업 순서

```
Phase 1: kis_service 페이지루프 공통화            ✅ 완료
Phase 2: _check_unmonitored_holdings              ✅ 완료
Phase 3: _process_single_signal                   ✅ 완료
Phase 4: asset_management_service.run             ✅ 완료
Phase 5: macro_service._assemble_regime_result    ✅ 완료
Phase 6: backtest_service                         ✅ 완료
Phase 7: backtest_service._run_simulation_loop    ✅ 완료
Phase 8: kis_ws_service.connect                   ✅ 완료
Phase 9: scanner_service._evaluate_single_ticker  ✅ 완료
```

---

## Phase 7 — `backtest_service._run_simulation_loop` (91줄) ← 2차 분리

### 현재 구조
```
_run_simulation_loop(price_data, rsi_data, all_dates, portfolio, tickers, ...)
  └─ for date in all_dates:
       ├─ prices/rsis 딕셔너리 구성           ← 책임 A: 스냅샷
       ├─ Phase 1: 매도 루프 (~25줄)          ← 책임 B
       └─ Phase 2: 매수 루프 (~25줄)          ← 책임 C
```

### 분리 후 구조
```
_run_simulation_loop(...) — ~25줄
  └─ for date:
       prices, rsis = _build_daily_snapshot(price_data, rsi_data, date) → (dict, dict)
       wins_d, losses_d = _process_sell_phase(portfolio, prices, rsis, ..., trades) → (int, int)
       _process_buy_phase(portfolio, prices, rsis, tickers, ..., trades) → None
       equity_curve.append(...) / cash_ratio_curve.append(...)
```

### 추출할 함수

**`_build_daily_snapshot(price_data, rsi_data, date) -> tuple[dict, dict]`**
```
파라미터:
  price_data: Dict[str, DataFrame]
  rsi_data: Dict[str, pd.Series]
  date: Any                    — 날짜 인덱스 항목
반환: tuple[dict, dict]       — (prices, rsis) 해당 날짜 스냅샷
로직:
  - price_data 루프 → date in df.index → prices[t]
  - rsi_data 루프 → not nan → rsis[t]
```

**`_process_sell_phase(portfolio, prices, rsis, stop_loss_pct, take_profit_pct, rsi_overbought, trades) -> tuple[int, int]`**
```
파라미터:
  portfolio: BtPortfolio
  prices: Dict[str, float]
  rsis: Dict[str, float]
  stop_loss_pct: float
  take_profit_pct: float
  rsi_overbought: int
  trades: list                 — append 대상 (in-place)
반환: tuple[int, int]         — (wins_delta, losses_delta)
로직:
  - portfolio.holdings 루프
  - stop_loss / take_profit / rsi_overbought 조건 판정
  - 조건 충족 시 portfolio.cash 갱신 + trades.append + del holdings[t]
```

**`_process_buy_phase(portfolio, prices, rsis, tickers, position_pct, target_cash_ratio, rsi_oversold, trades) -> None`**
```
파라미터:
  portfolio: BtPortfolio
  prices: Dict[str, float]
  rsis: Dict[str, float]
  tickers: List[str]
  position_pct: float
  target_cash_ratio: float
  rsi_oversold: int
  trades: list                 — append 대상 (in-place)
반환: None
로직:
  - tickers 루프
  - rsi 조건 + 현금비중 조건 + 예산 계산
  - 조건 충족 시 portfolio.cash/holdings 갱신 + trades.append
```

---

## Phase 8 — `kis_ws_service.connect` (61줄)

### 현재 구조
```
connect()                                    — async, while True 재시도 루프
  ├─ get_approval_key()                      ← 이미 분리됨
  ├─ ws_url 구성 + websockets.connect()      ← 책임 A: 연결
  ├─ for ticker in subscribed_tickers:       ← 책임 B: 재구독
  │     await subscribe(ticker, market)
  └─ while True: recv → handle_message       ← 책임 C: 메시지 루프
```

### 분리 후 구조
```
connect() — ~25줄 (재시도 루프 골격만)
  └─ async with websockets.connect(...) as ws:
       await _resubscribe_all_tickers(ws)    ← 책임 B 추출
       await _run_message_loop(ws)           ← 책임 C 추출
```

### 추출할 함수

**`_resubscribe_all_tickers(websocket) -> None`** (async)
```
파라미터:
  websocket: WebSocketClientProtocol
반환: None
로직:
  - subscribed_tickers가 있으면 로그
  - saved_items = [(t, market) for t in subscribed_tickers]
  - subscribed_tickers.clear()
  - for ticker, market in saved_items: await subscribe(ticker, market) + asyncio.sleep(1.0)
```

**`_run_message_loop(websocket) -> None`** (async)
```
파라미터:
  websocket: WebSocketClientProtocol
반환: None
로직:
  - while True: msg = await websocket.recv() → await handle_message(msg)
  - ConnectionClosed → break
  - Exception → logger.error + break
```

---

## Phase 9 — `scanner_service._evaluate_single_ticker` (55줄)

### 현재 구조
```
_evaluate_single_ticker(ticker, token, oversold, trend_breakout, analyst_strong_buy)
  ├─ KisFetcher.fetch_overseas_price()      ← 책임 A: 가격 조회
  ├─ DataService.get_price_history()        ← 책임 A: 역사 조회
  ├─ IndicatorService.compute_latest()     ← 책임 A: 지표 계산
  ├─ rsi < RSI_MAX → oversold.append()     ← 책임 B: oversold 판정
  ├─ ema200 돌파 → trend_breakout.append() ← 책임 C: 추세 돌파 판정
  └─ analyst_target > 현재가 → append()   ← 책임 D: 애널리스트 판정
```

### 분리 후 구조
```
_evaluate_single_ticker(ticker, token, oversold, trend_breakout, analyst_strong_buy) — ~15줄
  ├─ data = _fetch_ticker_scan_data(ticker, token) → Optional[dict] | None 시 return
  ├─ _check_oversold_candidate(ticker, data, oversold)
  ├─ _check_trend_breakout_candidate(ticker, data, trend_breakout)
  └─ _check_analyst_candidate(ticker, data, analyst_strong_buy)
```

### 추출할 함수

**`_fetch_ticker_scan_data(ticker, token) -> Optional[dict]`**
```
파라미터:
  ticker: str
  token: str
반환: Optional[dict]    — {price_info, rsi, ema200, prev_close, pbr, market_cap, analyst_target_price, name}
                           조회 실패 시 None
로직:
  - KisFetcher.fetch_overseas_price(token, ticker) → price_info (없으면 None)
  - DataService.get_price_history(ticker, days=SCAN_HISTORY_DAYS) (empty면 None)
  - IndicatorService.compute_latest_indicators_snapshot(hist["Close"]) → rsi, ema200
  - prev_close = hist["Close"].iloc[-2] if len > 1 else current_price
  - 모두 조합해서 dict 반환
```

**`_check_oversold_candidate(ticker, data, oversold) -> None`**
```
파라미터:
  ticker: str
  data: dict             — _fetch_ticker_scan_data 반환값
  oversold: list         — append 대상 (in-place)
반환: None
로직:
  - data["rsi"] < SCAN_RSI_OVERSOLD_MAX AND market_cap > MIN AND pbr < MAX
  - 조건 충족 시 oversold.append(OversoldCandidate(...))
```

**`_check_trend_breakout_candidate(ticker, data, trend_breakout) -> None`**
```
파라미터: ticker, data, trend_breakout
반환: None
로직:
  - ema200 > 0 AND prev_close < ema200 AND current_price > ema200
  - 조건 충족 시 trend_breakout.append(TrendBreakoutCandidate(...))
```

**`_check_analyst_candidate(ticker, data, analyst_strong_buy) -> None`**
```
파라미터: ticker, data, analyst_strong_buy
반환: None
로직:
  - analyst_target_price AND target > current_price * SCAN_ANALYST_UPSIDE_RATIO
  - 조건 충족 시 analyst_strong_buy.append(AnalystStrongBuyCandidate(...))
```

---

## 분리 보류 (현재 구조 적절)

| 함수 | 이유 |
|------|------|
| `macro_service._get_market_regime` (39줄) | 오케스트레이터 패턴, 각 라인이 하위 함수 호출로 이미 분리됨 |
| `macro_service._calc_growth_signal` (32줄) | 단일 책임 (성장 신호 계산). if/elif 수는 규칙 수에 비례 |
| `kis_service.get_balance` (47줄) | 내부에 `_balance_retry_loop` 이미 분리됨. 페이지루프 구조 동일 |
| `position_service._execute_collected_signals` (42줄) | 오케스트레이터, 각 단계가 이미 하위 함수 위임 |
| `kis_ws_service.get_approval_key` (32줄) | 실제 로직 32줄, 단일 책임 (승인키 획득) |

---

## 공통 원칙

- **기존 공개 API 변경 없음**
- **추출 함수는 모두 `_private`**
- **한 번에 한 Phase씩**: 완료 후 동작 확인 → 다음 진행

---

**작성일**: 2026-03-15
**상태**: Phase 1~9 ✅ 전체 완료 (2026-03-15)

## 수정 완료 내역

| Phase | 추출 함수 | 파일 |
|-------|-----------|------|
| 1 | `_paginate_trade_history` | `kis_service.py` |
| 2 | `_process_unmonitored_holding` | `position_service.py` |
| 3 | `_log_signal_evaluation`, `_route_signal` | `position_service.py` |
| 4 | `_rebalance_market` | `asset_management_service.py` |
| 5 | `_compute_weighted_score`, `_blend_regime_score`, `_build_regime_schema` | `macro_service.py` |
| 6 | `_compute_rsi_and_dates`, `_run_simulation_loop`, `_build_backtest_result` | `backtest_service.py` |
