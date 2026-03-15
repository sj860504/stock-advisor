# 리팩토링 계획서 (2026-03-14)

> 2026-03-13~14 전략 전체 코드리뷰 세션에서 도출된 개선 계획.
> 상세 배경은 `.claude/BUSINESS_LOGIC.md` 각 섹션 참고.

---

## 구현 순서 원칙

> **Top-down 설계**: 상위 오케스트레이터(자산관리 서비스)부터 설계 후,
> 하위 서비스 인터페이스를 거기에 맞춰 정렬.

```
Phase 1 — 자산관리 서비스 설계     ← 상위 레이어 인터페이스 정의
Phase 2 — SRP 분리                 ← 자산관리 서비스가 호출할 구조로 재편
Phase 3 — 버그/허점 수정           ← 분리된 구조 위에서 수정
Phase 4 — 기능 개선                ← 트레일링 스탑, 레짐 연동 등
Phase 5 — 제거                     ← 섹터 리밸런서, 틱 트레이딩
Phase 6 — 포맷 개선                ← Slack 리포트
```

---

## Phase 1 — 자산관리 서비스 설계

> 전체 시스템의 최상위 오케스트레이터. 여기서 정의하는 인터페이스가
> 하위 서비스(PositionService)의 시그니처를 결정함.

### 1-1. AssetManagementService (신규)

**신규 파일**: `services/strategy/asset_management_service.py`

**역할 분리 원칙**:
- `AssetManagementService`: **예산 관리자** — 자산 비중 보고 "얼마 살 수 있다 / 얼마 확보해야 한다" 명령
- `PositionService`: **트레이더** — 예산 범위 내에서 score 기반 종목 선택 + 실행
- `SignalService`: **분석가** — score 계산 (독립 동작, 변경 없음)
- `TradeExecutorService`: **브로커** — 실제 KIS 주문

**분리 후 구조**:

```
run(user_id, holdings, kr_cash, usd_cash, macro_data) -> None
  1. _get_target_cash_ratio(macro_data, holdings) → target_ratio
  2. exchange_rate = MacroService.get_exchange_rate()
  3. _calc_totals(holdings, exchange_rate) → (kr_total, us_total_usd)
  4. is_kr_open → _calc_cash_gap(kr_cash, kr_stock_total, target_ratio) → kr_gap
     is_us_open → _calc_cash_gap(usd_cash, us_stock_total_usd, target_ratio) → us_gap
  5. kr_gap > 0 (KR 열린 시장):
       signals = SignalService.get_latest_signals()
       PositionService.execute_buy_budget(user_id, budget_krw=kr_gap, budget_usd=0, signals)
  5. us_gap > 0 (US 열린 시장):
       signals = SignalService.get_latest_signals()
       PositionService.execute_buy_budget(user_id, budget_krw=0, budget_usd=us_gap, signals)
  6. kr_gap < 0 or us_gap < 0:
       candidates = _select_sell_candidates(holdings)
       candidates 비어있으면 매도 스킵 (수익 종목 없음)
       PositionService.execute_sell_for_cash(user_id, abs(kr_gap), abs(us_gap), candidates)
  - 조율만, 종목 선택/주문 로직 없음

_get_target_cash_ratio(regime, fear_greed, holdings) -> float
  - MarketRegimeSchema + fear_greed + 개별 종목 수익률 기반 목표 현금 비중 반환
  - fear_greed < 10 → 0.0  (최우선)
  - BEAR   → 기본 0.20, 개별 종목 수익률 ≥ 3% 초과 종목이 보유의 30%+ → 상향
  - NEUTRAL→ 기본 0.40, 개별 종목 수익률 ≥ 5% 초과 종목이 보유의 30%+ → 상향
  - BULL   → 기본 0.40, 개별 종목 수익률 ≥ 7% 초과 종목이 보유의 30%+ → 상향
  - 상향 폭: +10%p 고정, 상한 60%
  - KR/US 공통 적용 (단일 비율 반환)
  - 순수 계산 함수, I/O 없음

_calc_cash_gap(cash, stock_total, target_ratio) -> float  # 단일 시장용
  - 양수 = 여유 (매수 가능 예산), 음수 = 부족 (현금 확보 필요)
  - run()에서 열린 시장에 대해서만 각각 호출
  - 순수 계산 함수, I/O 없음

_select_sell_candidates(holdings) -> list
  - 수익 종목(profit_pct > 0)만 필터링
  - 수익률 내림차순 정렬
  - 수익 종목 없으면 빈 리스트 반환 → 호출자가 매도 스킵
  - 순수 함수, I/O 없음
```

**PositionService 신규 메서드** (Phase 2에서 구현):

```
execute_buy_budget(user_id, budget_krw, budget_usd, signals) -> None
  - signals를 score 오름차순 정렬
  - budget 소진될 때까지 TradeExecutorService._execute_trade_v2("buy") 호출

execute_sell_for_cash(user_id, need_krw, need_usd, candidates) -> None
  - candidates 순서대로 need 충족될 때까지 TradeExecutorService._execute_trade_v2("sell") 호출
```

**호출 위치**: `trading_strategy_service.py` → `run_strategy()` 내

```python
# run_strategy() 내부 순서
1. _validate_preconditions()
2. _load_and_sync_portfolio()
3. _run_signals_and_execute()          ← SignalService + PositionService (기존 score 기반 매매)
4. AssetManagementService.run(...)     ← 비중 기반 예산 명령 (신규)
5. _send_portfolio_report()
```

---

## Phase 2 — SRP 분리

> 자산관리 서비스가 호출할 인터페이스에 맞게 하위 메서드 구조 재편.
> 기존 코드에서 발견된 단일 책임 원칙 위반 항목. 심각도 순 정렬.

### 2-1. `TradingStrategyService.run_strategy()` ★★★ (6개 관심사)

**파일**: `services/strategy/trading_strategy_service.py`

**현재 수행 관심사**: 전략 활성화 체크 / 시장 개장 판단 / 유니버스 업데이트 / KIS 동기화 / 거래 실행 / 리포트 발송

**분리 후 구조**:

```
_validate_preconditions(user_id) -> bool
  - cls._enabled 체크 → False면 즉시 False 반환
  - KR/US 시장 개장 여부 확인 → 둘 다 닫혀있으면 False 반환
  - 반환값만 있고 부수효과 없음 (순수 조건 판단)

_load_and_sync_portfolio(user_id) -> tuple[list, float, float]
  - PortfolioService.sync_with_kis() 호출
  - 보유종목 리스트, KRW 현금, USD 현금 반환
  - 동기화 실패 시 기존 캐시 반환 (예외 삼킴 금지)

_run_signals_and_execute(user_id, holdings, kr_cash, usd_cash, macro, user_state)
  -> tuple[bool, set]
  - SignalService._collect_trading_signals() 호출
  - PositionService._execute_collected_signals() 호출
  - (trade_executed: bool, executed_tickers: set) 반환

run_strategy(user_id) -> None  ← 조율만
  - _validate_preconditions() → False면 return
  - _update_target_universe() 호출 (기존 유지)
  - macro, user_state 로드
  - _load_and_sync_portfolio() 호출
  - _run_signals_and_execute() 호출
  - AssetManagementService.run() 호출  ← Phase 1 연동
  - _send_portfolio_report() 호출
```

---

### 2-2. `TradeExecutorService._execute_buy_order()` ★★★ (6개 관심사)

**파일**: `services/strategy/execution_service_v2.py`

**현재 수행 관심사**: 조건 체크 / 총액 계산 / 수량 계산 / US 실시간 가격 갱신 / KIS 주문 / DB 기록
*(1~3번은 이미 별도 메서드로 분리됨. 4~6번이 혼재)*

**분리 후 구조**:

```
_refresh_us_price(ticker) -> float
  - is_kr(ticker)이면 즉시 0.0 반환 (호출자가 기존 price 사용)
  - KisFetcher로 현재가 조회
  - 조회 실패 시 0.0 반환 (호출자가 fallback 처리)
  - 가격 조회만, 상태 변경 없음

_place_and_record(ticker, side, qty, price, reason, user_id) -> bool
  - KisService.send_order(ticker, qty, price, side) 호출
  - 성공 시 OrderService.record_trade() 호출
  - 성공/실패 bool 반환
  - 매수/매도 공통 사용 → _execute_sell_order()도 재사용 (중복 제거)

_execute_buy_order(ticker, reason, score, current_price, ...)
  -> tuple[bool, float, float]  ← 조율만
  - _check_buy_cash_and_entry_conditions() → 실패 시 (False, 0, 0)
  - _compute_buy_market_totals() 호출
  - _calculate_buy_quantity() 호출 → qty
  - _refresh_us_price() → price 갱신
  - _place_and_record() 호출
  - (executed, spent_krw, spent_usd) 반환
  ※ _last_buy_spent_krw 클래스 변수 제거 (Phase 3 A-3과 연동)
```

---

### 2-3. `PositionService._execute_collected_signals()` ★★★ (5개 관심사)

**파일**: `services/strategy/position_service.py`

**현재 수행 관심사**: 설정값 9개 로드 / 신호 정렬 / 루프 처리 / KR 현금 추적 / 미모니터 검사 위임

**분리 후 구조**:

```
@dataclass
ExecutionConfig:
  buy_max: int          # STRATEGY_BUY_THRESHOLD_MAX
  sell_min: int         # STRATEGY_SELL_THRESHOLD_MIN
  take_profit_pct: float
  stop_loss_pct: float
  add_rsi_limit: float
  add_score_limit: int
  exchange_rate: float
  today: str

_load_execution_config() -> ExecutionConfig
  - SettingsService에서 설정값 8개 일괄 조회
  - MacroService.get_exchange_rate() 호출
  - ExecutionConfig dataclass로 반환
  - 설정 조회만, 부수효과 없음

_sort_signals_by_priority(signals, split_orders) -> list
  - 우선순위: 신규 미보유(0) > 기존 보유(1) > split tranche(2)
  - 입력 변경 없이 새 리스트 반환 (순수 함수)

_execute_collected_signals(user_id, prepared_signals, holdings, ...) -> tuple[bool, set]
  ← 조율만
  - _load_execution_config() 호출
  - user_state에서 cooldown/split_orders 로드
  - _sort_signals_by_priority() 호출
  - _expire_split_orders() 호출 (Phase 3 A-2)
  - 루프: _process_single_signal() + cash/usd 차감 (Phase 3 A-3)
  - _check_unmonitored_holdings() 위임
  - (trade_executed, executed_tickers) 반환
```

---

### 2-4. `PositionService._process_single_signal()` ★★ (5개 관심사)

**파일**: `services/strategy/position_service.py`

**현재 수행 관심사**: sig dict 언패킹 / 로깅 포맷 생성 / 손익률 계산 / 시장 총액 선택 / 핸들러 호출

**분리 후 구조**:

```
@dataclass
UnpackedSignal:
  ticker: str
  state: TickerState
  holding: Optional[dict]
  score: int
  reason_str: str
  forced_sell: bool       # stop_loss_hit 여부 (Phase 3 A-1 연동)
  profit_pct: float       # 보유 시 (current - buy) / buy * 100, 미보유 시 0.0
  market_total: float     # KR이면 kr_total, US이면 us_total_krw

_unpack_signal(sig, kr_total, us_total_krw) -> UnpackedSignal
  - sig dict에서 ticker, state, holding, score, reasons 추출
  - reason_str = ", ".join(reasons)
  - forced_sell = "stop_loss_hit" in reasons
  - profit_pct: holding의 buy_price 기반 계산 (없으면 0.0)
  - market_total: is_kr(ticker) 분기
  - 순수 함수, I/O 없음

_process_single_signal(sig, cfg, ...) -> tuple[bool, Optional[str], float, float]
  - _unpack_signal() 호출 → UnpackedSignal
  - logger.info(ticker, score, rsi, dcf)  ← 로깅은 여기서만
  - forced_sell=True → _handle_forced_sell() 즉시 호출 후 return (Phase 3 A-1)
  - _handle_profit_take_signal() 체크
  - _handle_trailing_stop() 체크 (Phase 4 B-1 신규)
  - _handle_add_buy_signal() 체크
  - _handle_score_trade() 체크
  - (executed, ticker, spent_krw, spent_usd) 반환
```

---

### 2-5. `SignalService._collect_trading_signals()` ★★ (5개 관심사)

**파일**: `services/strategy/signal_service.py`

**현재 수행 관심사**: KR/US 개장 판단 / 분석 시장 결정 / RSI 하드게이트 / 현금 하드게이트 / 점수 계산 + 신호 구성

**분리 후 구조**:

```
_determine_analysis_markets(allow_extended) -> tuple[bool, bool]
  - MarketHourService.is_kr_market_open() 호출
  - MarketHourService.is_us_market_open() 호출
  - MarketHourService.is_us_strategy_window() 호출
  - (analyze_kr: bool, analyze_us: bool) 반환
  - 판단만, 부수효과 없음

_apply_hard_gates(ticker, ticker_state, holding, cash_balance, usd_cash,
                  kr_total, us_total_krw, target_cash_kr, target_cash_us,
                  macro_data, exchange_rate) -> bool
  - holding이 있으면 즉시 False (보유 종목은 게이트 없음)
  - RSI >= STRATEGY_RSI_BUY_BLOCK → True (차단)
  - 현금 비율 < target → _is_fear_market_exception() 체크
    → 예외면 False (통과), 아니면 True (차단)
  - True = 이 종목 시그널 수집 스킵
  - Phase 4 B-2 공포장 예외 로직도 여기서 처리

_cached_signals: list = []  ← 클래스 변수 (신규)

get_latest_signals() -> list  ← 신규
  - _cached_signals 반환 (재계산 없음)
  - AssetManagementService에서 호출용

_collect_trading_signals(holdings, macro_data, user_state, ...) -> list  ← 조율만
  - _determine_analysis_markets() 호출
  - all_states 순회
  - 시장/ready 필터 → _apply_hard_gates() → calculate_score() → 신호 append
  - cls._cached_signals = signals  ← 캐시 저장
  - 준비된 signals 리스트 반환
```

---

### 2-6. `ReportService._compute_portfolio_totals()` ★★★ (6개 관심사)

**파일**: `services/notification/report_service.py`

**현재 수행 관심사**: 설정/환율 로드 / KR·US 분류 / KIS fallback 선택 / KR 손익 / US 손익 / 비율 계산

**분리 후 구조**:

```
_load_portfolio_context(summary) -> dict
  - SettingsService에서 initial_principal, usd_cash 로드
  - MacroService.get_exchange_rate() 호출
  - summary dict에서 KIS 요약값(_sf() 헬퍼) 추출
  - {"exchange_rate", "initial_principal", "usd_cash",
     "kis_scts_evlu", "kis_pchs_amt", ...} 반환

_calc_kr_portfolio(kr_holdings, kis_pchs_amt, kis_evlu_amt, kis_evlu_pfls, cash_krw) -> dict
  - KIS 요약값 우선, 없으면 holdings 직접 계산 (fallback)
  - {"stock_val", "invested", "profit", "profit_pct", "total"} 반환

_calc_us_portfolio(us_holdings, usd_cash, exchange_rate) -> dict
  - holdings 기반 평가액/매입액 계산
  - {"stock_usd", "invested_usd", "profit_usd", "profit_pct",
     "total_usd", "total_krw", "cash_krw"} 반환

_compute_portfolio_totals(holdings, cash, summary) -> dict  ← 조율만
  - _load_portfolio_context() 호출
  - filter_kr/filter_us로 분류
  - _calc_kr_portfolio(), _calc_us_portfolio() 호출
  - total_eval, 비율, principal_profit 계산 후 최종 dict 반환
```

---

### 2-7. `TradingStrategyService._send_portfolio_report()` ★ (7개 관심사)

**파일**: `services/strategy/trading_strategy_service.py`

**현재 수행 관심사**: KIS 재동기화 / 보유·현금 로드 / 스냅숏 비교 / 변경 종목 필터 / 실행 종목 교차 필터 / 포맷팅 위임 / Slack 발송

**분리 후 구조**:

```
_load_latest_portfolio(user_id) -> tuple[list, float, dict]
  - PortfolioService.sync_with_kis() 재호출 (최신 잔고 반영)
  - 보유종목 리스트, KRW 현금, KIS summary dict 반환
  - 동기화만, 포맷팅/발송 없음

_filter_report_changes(before_snapshot, after_snapshot, executed_tickers)
  -> tuple[set, list]
  - before vs after 비교 → 수량 변경된 ticker 추출
  - executed_tickers와 교차 → 실제 체결된 변경만 필터
  - (changed_tickers: set, changed_holdings: list) 반환
  - 순수 함수, I/O 없음

_send_portfolio_report(user_id, before_snapshot, executed_tickers) -> None  ← 조율만
  - _load_latest_portfolio() 호출
  - after_snapshot 구성
  - _filter_report_changes() 호출
  - ReportService.format_trade_result_report() 위임
  - ReportService.format_portfolio_report() 위임
  - AlertService.send_slack_alert() 발송
```

---

### 2-8. `MacroService.calculate_historical_regime()` ★ (코드 중복)

**파일**: `services/market/macro_service.py`

**현재 문제**: `get_market_regime()`과 `calculate_historical_regime()` 두 메서드가
5개 점수 컴포넌트 계산 코드를 각각 독립 보유. 한쪽 수정 시 다른 쪽도 수정 필요.

**분리 후 구조**:

```
_calculate_all_regime_components(
  close: pd.Series, vix: float, fear_greed: int,
  econ_indicators, ndx_1m_hist=None
) -> tuple[int, int, int, int, int, dict, bool]
  - _calc_technical_20(), _calc_vix_20(), _calc_fng_20()
    _calc_econ_20(), _calc_other_20() 순서대로 호출
  - (technical_20, vix_20, fng_20, econ_20, other_20, ema_map, extreme_fear) 반환
  - 순수 계산 함수, I/O 없음

get_market_regime()
  - 데이터 수집 → _calculate_all_regime_components() → 결과 조합

calculate_historical_regime(date)
  - 히스토리 데이터 수집 → _calculate_all_regime_components() → DB 저장
```

---

### SRP 위반 심각도 요약

| 순위 | 메서드 | 파일 | 관심사 수 | Phase |
|------|--------|------|-----------|-------|
| 1 | `run_strategy()` | trading_strategy_service | 6 | 2-1 |
| 2 | `_execute_buy_order()` | execution_service_v2 | 6 | 2-2 |
| 3 | `_compute_portfolio_totals()` | report_service | 6 | 2-6 |
| 4 | `_send_portfolio_report()` | trading_strategy_service | 7 | 2-7 |
| 5 | `_execute_collected_signals()` | position_service | 5 | 2-3 |
| 6 | `_process_single_signal()` | position_service | 5 | 2-4 |
| 7 | `_collect_trading_signals()` | signal_service | 5 | 2-5 |
| 8 | `calculate_historical_regime()` | macro_service | 중복 | 2-8 |

---

## Phase 3 — 버그/허점 수정

### 3-1. 강제 손절 전량 즉시 매도

**파일**: `services/strategy/position_service.py`

| 메서드 | 변경 내용 |
|--------|-----------|
| `_collect_trading_signals()` (signal_service) | signal dict에 `forced_sell` 플래그 추가: `"forced_sell": "stop_loss_hit" in reasons` |
| `_process_single_signal()` | `sig.get('forced_sell')=True` 이면 `_handle_forced_sell()` 즉시 호출 후 return (Phase 2 UnpackedSignal과 연동) |
| `_handle_forced_sell()` _(신규)_ | 전량 즉시 1회 `_execute_trade_v2(forced_qty=holding_qty)` 호출. `sell_split_orders`, `split_orders` 해당 항목 즉시 제거 |
| `_check_unmonitored_holdings()` | 손절 → `_handle_forced_sell()` 호출로 통일. 익절 → `_handle_profit_take_signal()` 재사용 (현재 `_execute_trade_v2()` 직접 호출 중복) |

**현재**: score=100 → `_handle_score_trade()` → `_handle_sell_signal()` → 분할 매도
**변경**: `forced_sell=True` → `_handle_forced_sell()` → 전량 즉시 1회
**부수효과**: 손절 후 `sell_split_orders` 방치 이슈 자동 해결

---

### 3-2. split_orders TTL 만료 기한

**파일**: `services/strategy/position_service.py`

| 메서드 | 변경 내용 |
|--------|-----------|
| `_expire_split_orders(split_orders)` _(신규)_ | `start_date` 기준 N일 초과 항목 제거 |
| `_execute_collected_signals()` | 루프 진입 전 `_expire_split_orders()` 호출 |

**신규 설정키**: `STRATEGY_SPLIT_EXPIRE_DAYS = 5`

```python
def _expire_split_orders(cls, split_orders: dict) -> None:
    expire_days = SettingsService.get_int("STRATEGY_SPLIT_EXPIRE_DAYS", 5)
    today = datetime.now(tz).date()
    expired = [t for t, so in split_orders.items()
               if (today - datetime.strptime(so.start_date, "%Y-%m-%d").date()).days >= expire_days]
    for t in expired:
        split_orders.pop(t)
        logger.info(f"⏰ {t} split_order TTL 만료 → 제거")
```

---

### 3-3. _execute_trade_v2() 반환값 tuple화 (USD 현금 차감 포함)

**파일**: `services/strategy/execution_service_v2.py`, `services/strategy/position_service.py`

> ⚠️ 기존 `_last_buy_spent_krw` 클래스 변수는 side-effect 안티패턴 → 반환값으로 교체

| 메서드 | 변경 내용 |
|--------|-----------|
| `TradeExecutorService._execute_trade_v2()` | 반환 `bool` → `(executed: bool, spent_krw: float, spent_usd: float)` tuple |
| `TradeExecutorService._last_buy_spent_krw` | 클래스 변수 제거 |
| `_process_single_signal()` | tuple에서 `spent_krw`, `spent_usd` 직접 읽어 반환 |
| `_execute_collected_signals()` | KR: `cash_balance -= spent_krw`, US: `usd_cash -= spent_usd` 즉시 차감 |

---

### 3-4. split tranche를 add_buy_cooldown 체크에서 제외

**파일**: `services/strategy/position_service.py`

| 메서드 | 변경 내용 |
|--------|-----------|
| `_handle_buy_split()` | `has_pending_splits=True`이면 `_is_buy_cooldown_active()` 체크 스킵 |
| `_execute_split_tranche()` | `add_buy_cooldown` 세팅은 유지 (신규 매수 차단용) |

```python
# _handle_buy_split() 내부
if not has_pending_splits:  # 신규 진입만 쿨다운 체크
    if cls._is_buy_cooldown_active(...):
        return False
```

---

### 3-5. 분할 매도 수량 트리거 시점 고정

**파일**: `services/strategy/position_service.py`, `models/schemas.py`

| 메서드 | 변경 내용 |
|--------|-----------|
| `SplitSellOrderState` | `tranche_qty: int` 필드 추가 |
| `_get_sell_split_qty()` | 초기화 시 `tranche_qty = ceil(total_qty / split_count)` 고정 저장. 이후 호출은 `sso.tranche_qty` 반환 (마지막 트랜치는 `remaining_qty`) |

---

## Phase 4 — 기능 개선

### 4-1. 레짐 연동 익절 + 트레일링 스탑

**파일**: `services/strategy/position_service.py`, `models/strategy_state.py`

> ⚠️ 익절(고정 %)과 트레일링 스탑(고점 추적)은 반드시 별도 메서드로 분리

| 메서드/모델 | 변경 내용 |
|-------------|-----------|
| `StrategyState` | `trailing_high = Column(Text, default="{}")` 컬럼 추가 |
| `StrategyStateRepo` | `_USER_FIELDS`에 `"trailing_high"` 추가, dict 직렬화 등록 |
| `_get_take_profit_pct(macro)` _(신규)_ | BULL→5.0 / NEUTRAL→3.0 / BEAR→2.0 반환 |
| `_get_trailing_stop_pct(macro)` _(신규)_ | BULL→-7.0 / NEUTRAL/BEAR→-5.0 반환 |
| `_update_trailing_high(ticker, price, trailing_high)` _(신규)_ | `trailing_high[ticker] = max(기존값, price)` 고점 갱신만 |
| `_handle_profit_take_signal()` | 고정 pct → `_get_take_profit_pct()` 호출로 교체. **트레일링 로직 포함 금지** |
| `_handle_trailing_stop()` _(신규)_ | 고점 추적 손절 전담. 조건 충족 시 `_handle_forced_sell()` 호출 |
| `_process_single_signal()` | 루프 초입 `_update_trailing_high()` → `_handle_profit_take_signal()` → `_handle_trailing_stop()` 순서 |

---

### 4-2. 공포장 매수 허용 — 하드게이트 예외

**파일**: `services/strategy/signal_service.py`

> ⚠️ 현금 하드게이트 중복 구조 유지: `_collect_trading_signals()` 인라인(조기 차단)과
> `_is_cash_ratio_sufficient()`(주문 직전 최종 확인)은 역할이 다르므로 제거하지 않음

| 메서드 | 변경 내용 |
|--------|-----------|
| `_is_fear_market_exception(macro, score)` _(신규)_ | `fear_greed < 20 and regime=="Bear" and score < 15` → True 반환 |
| `_apply_hard_gates()` (Phase 2-5에서 분리된 메서드) | 현금 게이트 직전 `_is_fear_market_exception()` 호출 → True면 게이트 스킵 |

---

### 4-3. 레짐 스코어 5개 개선

**파일**: `services/market/macro_service.py`

> ⚠️ `MacroService`에서 DB 직접 조회 금지. 30일 평균은 호출자가 조회 후 파라미터로 전달

| 메서드 | 변경 내용 |
|--------|-----------|
| `_calc_technical_20()` | EMA200 단순 거리 → `close.tail(20)` 평균 거리 |
| `_calc_fng_20()` | `(score, extreme_fear: bool)` tuple 반환 |
| `get_market_regime()` | `extreme_fear=True` → `status="Bear"` 강제. `historical_avg_score` 파라미터 추가 |
| `_determine_economic_phase()` | `phase_modifier` 클램프 ±10 → ±15 |
| `run_strategy()` (trading_strategy_service) | `MarketRegimeHistoryRepo.get_30d_avg()` 조회 후 `get_market_regime(historical_avg_score=avg)` 전달 |
| `COMPONENT_WEIGHTS` _(신규 상수)_ | `technical:20, vix:25, fng:20, econ:20, other:15` |

---

### 4-4. DCF 3개 개선

**파일**: `services/analysis/financial_service.py`, `services/analysis/dcf_service.py`

| 메서드 | 변경 내용 |
|--------|-----------|
| `GROWTH_RATE_CAP_BY_SECTOR` _(신규 상수)_ | 섹터별 FCF 성장률 상한 dict |
| `get_dcf_data()` | `growth_rate = min(growth_rate, cap)` |
| `_dcf_from_analyst_target()` | `analyst_count` 조회 → `DcfInputData.analyst_count` 추가 |
| `calculate_dcf()` | `analyst_count >= 5` 이면 analyst_target weighted blend |
| `sync_daily_market` 잡 | `_build_ticker_universe()`에 보유종목 포함 |

---

## Phase 5 — 제거

### 5-1. 섹터 리밸런서 전체 제거

| 파일 | 제거 대상 |
|------|-----------|
| `services/strategy/sector_rebalancer_service.py` | 파일 전체 삭제 |
| `services/strategy/trading_strategy_service.py` | import, `get_sector_rebalance_status()`, `run_sector_rebalance()` 제거 |
| `services/base/scheduler_service.py` | `run_sector_rebalance` 잡 제거 |
| `routers/trading.py` | 섹터 리밸런스 엔드포인트 확인 후 제거 |

---

### 5-2. 틱 트레이딩 전체 제거

| 파일 | 제거 대상 |
|------|-----------|
| `services/strategy/trading_strategy_service.py` | `_run_tick_trade()` 외 7개 메서드 제거 |
| `services/base/scheduler_service.py` | `report_tick_trade_status` 잡 제거 |
| `services/strategy/execution_service_v2.py` | `_send_tick_alert()` 제거 |
| `models/strategy_state.py` | `tick_trade` 컬럼 제거 |
| DB Settings | `STRATEGY_TICK_*` 키 8개 정리 |

> ⚠️ **DB 마이그레이션**: SQLite 컬럼 DROP 불가 → 테이블 재생성 또는 컬럼 방치 후 코드만 무시
> `scripts/migrate_drop_tick_trade.py` 작성 필요

---

## Phase 6 — 포맷 개선

### 6-1. 매수/매도 Slack 리포트 compact화

**파일**: `services/notification/report_service.py`

| 메서드 | 변경 내용 |
|--------|-----------|
| `_format_changed_ticker_line()` | BUY: `🔵 BUY {ticker} {qty}sh @{price}` / SELL: `🔴 SELL {ticker} {qty}sh @{price} \| {pct:+.1f}% {profit}` |
| `format_trade_result_report()` | 헤더 제거, 총자산 라인: `💰 총평가 {total:,.0f} \| 현금 {cash:,.0f}` |

**완료**: 보유종목 2줄 compact (`_format_kr/us_holding_line`) ✅

---

## 낮은 우선순위 이슈

| 이슈 | 내용 | 파일 |
|------|------|------|
| 환율 실시간화 | `get_exchange_rate()`가 yfinance 5일 히스토리 마지막 종가 사용 (전일 종가 기준). 실시간이 필요하면 KIS API로 교체 필요. fallback 1400.0 | `services/market/macro_service.py:148` |
| `holding: dict` → `HoldingSchema` 전면 교체 | `load_portfolio()`가 `List[dict]` 반환 → `List[HoldingSchema]`로 변경 필요. 영향 범위: `PortfolioRepo.load_holdings`, `PortfolioService.load_portfolio`, `execution_service_v2`, `position_service`, `signal_service`, `trading_strategy_service`, `sector_rebalancer_service`, `scheduler_service`, `routers/`, `main.py`, `scripts/` (10+개 파일, 모든 `.get()` → 속성 접근 교체). **별도 브랜치에서 진행** | `repositories/portfolio_repo.py`, `services/trading/portfolio_service.py` 외 다수 |

---

## 완료 항목

| 항목 | 완료일 | 비고 |
|------|--------|------|
| 보유종목 Slack 리포트 2줄 compact | 2026-03-14 | `_format_kr/us_holding_line` |
| BUSINESS_LOGIC.md 전체 현행화 | 2026-03-14 | Section 1~13 |
| Phase 1-1: `AssetManagementService` 신규 구현 | 2026-03-14 | `run`, `_get_target_cash_ratio`, `_calc_totals`, `_calc_cash_gap`, `_select_sell_candidates` |
| Phase 2-1: `run_strategy()` SRP 분리 | 2026-03-14 | `_validate_preconditions`, `_load_and_sync_portfolio`, `_load_macro_and_assets`, `_load_user_state`, `_run_signals_and_execute` 신규 |
| `PositionService.execute_buy_budget/sell_for_cash` 신규 | 2026-03-14 | AssetManagementService 위임 메서드 |
| `SignalService.get_latest_signals()` + `_cached_signals` 신규 | 2026-03-14 | 재계산 없이 캐시 반환 |
| `_migrate_json_to_db()` 제거 | 2026-03-14 | 마이그레이션 완료, `json` import 제거 |
| Phase 2-2: `_execute_buy_order()` → `TradeResult` 반환 | 2026-03-14 | `_last_buy_spent_krw` 제거, `TradeResult(executed, spent_krw, spent_usd)` |
| Phase 2-3: `ExecutionConfig` 모델 + `_load_execution_config()` + `_sort_signals_by_priority()` | 2026-03-14 | SettingsService 9개 → 모델 스냅샷 |
| Phase 2-4: `UnpackedSignal` 모델 + `_unpack_signal()` | 2026-03-14 | sig dict 정형화, `forced_sell` 플래그 포함 |
| Phase 2-5: `SignalService` — `MacroDataSnapshot` 교체 + `_determine_analysis_markets()` + `_apply_hard_gates()` | 2026-03-14 | `macro: dict` → 속성 접근 전면 교체 |
| Phase 2-6: `ReportService` — `PortfolioContext/KrPortfolio/UsPortfolio` + 3개 헬퍼 분리 | 2026-03-14 | `_load_portfolio_context`, `_calc_kr_portfolio`, `_calc_us_portfolio` |
| Phase 2-8: `MacroService` — `RegimeComponents` + `_calculate_all_regime_components()` | 2026-03-14 | `_get_market_regime`/`calculate_historical_regime` 중복 제거 |
| Phase 3-1: `_handle_forced_sell()` 신규 + `_check_unmonitored_holdings()` + `_process_single_signal()` 라우팅 | 2026-03-14 | 손절→`_handle_forced_sell()` 즉시 전량, `split_orders/sell_split_orders` 즉시 제거, 익절→`_handle_profit_take_signal()` 재사용 |
| 잔존 tuple 언패킹 수정 | 2026-03-14 | `_handle_sell_signal`, `_handle_score_trade(→TradeResult)`, `execute_buy/sell_for_cash` |
| Phase 3-2: `_expire_split_orders()` 신규 + `_execute_collected_signals()` 진입 전 호출 | 2026-03-14 | `STRATEGY_SPLIT_EXPIRE_DAYS=5` 기본값, 루프 전 TTL 만료 항목 자동 제거 |
| Phase 3-3: USD 현금 차감 루프 | 2026-03-14 | `_process_single_signal()` 4-tuple, `_execute_collected_signals()` `usd_cash` 파라미터 추가, US 매수 시 USD 실시간 차감 |
| Phase 3-4: split tranche 쿨다운 제외 | 2026-03-14 | `_handle_buy_split()` — 기존 pending split은 쿨다운 체크 스킵, 신규 진입만 체크 |
| Phase 3-5: `SplitSellOrderState.tranche_qty` 고정 | 2026-03-14 | 초기화 시 `ceil(total_qty/split_count)` 고정 저장, 이후 호출 시 고정값 사용 (마지막 트랜치는 remaining_qty) |
| Phase 4-2: `_is_fear_market_exception()` + `_apply_hard_gates()` 공포장 예외 | 2026-03-14 | `fear_greed < 20 AND Bear` 시 현금 게이트 스킵, `macro` 파라미터 추가 |
| Phase 5-1: 섹터 리밸런서 전체 제거 | 2026-03-14 | `SectorRebalancerService` import/wrapper 제거, scheduler job/메서드 제거, router 엔드포인트 제거 |
| Phase 5-2: 틱 트레이딩 전체 제거 | 2026-03-14 | `_run_tick_*` 8개 메서드 제거, `_run_signals_and_tick` 제거, scheduler job/메서드 제거, `_send_tick_alert` 제거 |
| Phase 6-1: 매수/매도 Slack 리포트 compact화 | 2026-03-14 | `BUY {ticker} {name} {qty}sh @{price}` / `SELL ... \| {pct:+.1f}% {profit}`, 헤더 제거, `💰 총평가 \| 현금` 라인 |
| Phase 2-7: `_send_portfolio_report()` SRP 분리 | 2026-03-14 | `_load_latest_portfolio()` (KIS 재동기화+로드), `_filter_report_changes()` (순수함수, 교차필터), `_send_portfolio_report()` 조율만 |
| Phase 4-1: 트레일링 스탑 | 2026-03-14 | `_get_trailing_stop_pct` (BULL→-7% / NEUTRAL·BEAR→-5%), `_update_trailing_high`, `_handle_trailing_stop` 신규. `trailing_high` DB 컬럼 추가 (`tick_trade` 코드 제거). 마이그레이션: `scripts/migrate_trailing_high.py` |
| Phase 4-3: 레짐 스코어 5개 개선 | 2026-03-14 | `_calc_technical_20` close.tail(20) 평균 거리, `_calc_fng_20` → (score, extreme_fear) tuple, `extreme_fear=True` 시 Bear 강제, `ECONOMIC_PHASES` modifier 확대(-12~+8), `COMPONENT_WEIGHTS` 상수 신규, `historical_avg_score` blending (현재 60% + 과거 40%), `StockMetaRepo.get_30d_avg_regime_score()` 신규 |
| Phase 4-4: DCF 3개 개선 | 2026-03-14 | `GROWTH_RATE_CAP_BY_SECTOR` 상수 신규, `get_dcf_data()` 섹터별 성장률 클램프, `_dcf_from_analyst_target()` analyst_count < 3 skip, `DcfInputData.analyst_count` 필드 추가, `calculate_dcf()` analyst_count >= 5 시 DCF 70% + analyst_target 30% blend |
| Phase 4-4 잡 개선: `sync_daily_market_data` 보유종목 포함 | 2026-03-14 | `_get_holding_tickers()` 신규, `PortfolioRepo.load_holdings("sean")` 조회 후 top 리스트에 없는 보유종목 추가 |
