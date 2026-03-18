# Business Logic

**매매 전략·점수 계산·리스크 관리 전체 레퍼런스**

---

## 목차

1. [전략 실행 플로우](#1-전략-실행-플로우)
2. [점수 계산 로직 (8개 컴포넌트)](#2-점수-계산-로직)
3. [매수/매도 임계값](#3-매수매도-임계값)
4. [분할 매수/매도](#4-분할-매수매도)
5. [손절/익절 로직](#5-손절익절-로직)
6. [현금 비중 관리](#6-현금-비중-관리)
7. [섹터 비중 관리](#7-섹터-비중-관리)
8. [시장 레짐 판정](#8-시장-레짐-판정)
9. [DCF 밸류에이션](#9-dcf-밸류에이션)
10. [틱 트레이딩 플로우](#10-틱-트레이딩-플로우)
11. [스케줄러 작업표](#11-스케줄러-작업표)
12. [주요 설정값 (DB Settings)](#12-주요-설정값-db-settings)
13. [Slack 알림 포맷](#13-slack-알림-포맷)

---

## 1. 전략 실행 플로우

```
run_strategy(user_id) [매 1분 실행]
  │
  ├─ 0. 미체결 주문 확인 (_verify_pending_orders)  ★ 2026-03-16
  │      ├─ DB에서 status='pending' 주문 조회
  │      ├─ KIS 미체결 API로 체결 여부 확인
  │      └─ 체결 완료 → status='filled' 업데이트
  │
  ├─ 1. 유니버스 갱신 (_update_target_universe)
  │      └─ Top100 변경 감지 → MarketDataService.prune_states()
  │
  ├─ 2. 데이터 로드
  │      ├─ PortfolioService.sync_with_kis()  → 보유종목, 현금
  │      └─ MacroService.get_macro_data()     → VIX, F&G, 레짐
  │
  ├─ 3. 신호 수집 (_collect_trading_signals)
  │      └─ 모니터링 티커 전체 → SignalService.calculate_score()
  │             └─ 점수 ≤ BUY_THRESHOLD → BUY 신호
  │             └─ 점수 ≥ SELL_THRESHOLD → SELL 신호
  │
  ├─ 4. 신호 실행 (_execute_collected_signals)
  │      ├─ 우선순위 정렬: 신규종목(0) > 기존보유(1) > split tranche(2)
  │      ├─ _place_and_record(): 미체결 있으면 스킵  ★ 2026-03-16
  │      ├─ 익절/트레일링 스탑 신호 우선 처리
  │      ├─ 손절 신호 처리 → _handle_forced_sell (전량 즉시 매도)
  │      ├─ 매수 신호 처리 (분할 매수)
  │      └─ 미모니터링 보유종목 손절/익절 체크
  │
  ├─ 5. 자산관리 서비스 (AssetManagementService.run)
  │      └─ 현금 비중 gap 계산 → 여유 시 매수 / 부족 시 매도
  │
  └─ 6. 포트폴리오 리포트 (_send_portfolio_report)
         └─ 매매 실행 시 Slack 전송
```

---

## 2. 점수 계산 로직

**파일**: `services/strategy/signal_service.py`

### 기본 구조

```
score = BASE_SCORE(50) + [A] + [B] + [C] + [D] + [E~G]
```

점수가 낮을수록 매수 신호, 높을수록 매도 신호.

### [A] 기술적 지표

**RSI**

| 조건 | 점수 변화 |
|------|----------|
| RSI ≤ 30 (극도 과매도) | -20 ~ -10 |
| RSI 30~50 (과매도) | -10 ~ -5 (±5 미만이면 무시) |
| RSI 50~70 (과매수) | +5 ~ +10 (±5 미만이면 무시) |
| RSI > 70 (극도 과매수) | +10 ~ +20 |

**급락/급등**

| 조건 | 점수 변화 |
|------|----------|
| 당일 등락률 ≤ -5% (급락) | -15 |
| 당일 등락률 ≥ +5% (급등) | +15 |

**DCF 밸류에이션** (`undervalue_pct = (DCF - 현재가) / 현재가 × 100`)

| 조건 | 점수 변화 |
|------|----------|
| undervalue_pct ≥ 20% (크게 저평가) | -25 |
| undervalue_pct ≥ 10% | -15 |
| undervalue_pct ≥ 5% | -10 |
| undervalue_pct ≥ -5% (공정가치) | -5 |
| undervalue_pct ≥ -15% (약간 고평가) | +10 |
| undervalue_pct < -15% (크게 고평가) | +20 |
| DCF 데이터 없음 | +10 (패널티) |

**EMA 지지선**

| 조건 | 점수 변화 |
|------|----------|
| EMA200 ~ EMA200×1.02 구간 (지지) | -10 |

### [B] 포트폴리오 상태

| 조건 | 점수 변화 |
|------|----------|
| profit_pct ≥ TAKE_PROFIT_PCT (3%) | +30 (익절 신호) |
| profit_pct ≤ -5% AND profit_pct > STOP_LOSS_PCT | -10 (추매 신호 — 손실 구간에서만) |
| profit_pct ≤ STOP_LOSS_PCT (-8%) | forced_sell = True, score = 100 (이하 컴포넌트 계산 생략) |

### [C] 거시 환경

| 조건 | 점수 변화 |
|------|----------|
| VIX ≥ 25 또는 F&G ≤ 30 (공포장) | -30 (매수 유리) |
| VIX ≤ 15 또는 F&G ≥ 70 (과열) | +15 |
| Regime = BULL | -15 (매수 유리) + **+10 익절 넛지** = 순 -5 — BULL에서 홀드/매수 기조 유지하되 익절 약하게 촉진 |
| Regime = BEAR | **0** (점수 변화 없음) — 약세장 매도 억제, 저점 강제 청산 방지 |

### [D] 목표가 설정

| 조건 | 점수 변화 |
|------|----------|
| 현재가 ≤ target_buy_price | -15 (매수 트리거) |
| 현재가 ≥ target_sell_price | +30 (매도 트리거) |

### [E~G] 보너스

| 조건 | 점수 변화 |
|------|----------|
| 시총 Top 10 종목 | -10 (우량주 보너스) |
| 사용자 비중 오버라이드 | ±커스텀값 |
| 섹터 비중 부족 (underweight) | -10 |
| 섹터 비중 초과 (overweight) | +10 |

### [H] 현금 패널티 (사후 적용)

| 조건 | 점수 변화 |
|------|----------|
| 현금비중 < 목표 && score > 50 | +15 (매수 억제) |

---

## 3. 매수/매도 임계값

| 설정키 | 기본값 | 의미 |
|--------|--------|------|
| `STRATEGY_BUY_THRESHOLD_MAX` | 30 | score ≤ 30 → BUY 신호 |
| `STRATEGY_SELL_THRESHOLD_MIN` | 70 | score ≥ 70 → SELL 신호 |

> 설정은 DB Settings 테이블에서 런타임 변경 가능.

### 신호 수집 전 하드 게이트 (score 계산 이전)

미보유 종목(신규 매수 후보)에만 적용. 조건 불충족 시 score 계산 자체를 건너뜀.

```
_collect_trading_signals() 내부:

1. RSI 하드 게이트
   rsi >= STRATEGY_RSI_BUY_BLOCK (기본 75)
   → 즉시 skip ("overbought buy block")

2. 현금비중 하드 게이트
   available_cash / market_total < target_cash_ratio
   → 즉시 skip ("cash shortage")
   (KR: cash_balance / kr_total, US: usd_cash×exchange_rate / us_total)
```

> 보유 종목(holding 있음)은 위 게이트를 통과하지 않음 — 익절/손절/추매는 항상 score 계산.

### 보유 종목 RSI/현금 처리 방식

하드 게이트는 없지만 별도 필터가 존재한다.

| 항목 | 미보유 | 보유 |
|------|--------|------|
| RSI 차단 | RSI ≥ 75 하드 게이트 | 없음 |
| 현금 차단 | 현금비중 하드 게이트 | 없음 |
| 추매 RSI 체크 | 해당 없음 | `add_rsi_limit(60)` — `_handle_add_buy_signal()` 내부 2차 필터 |
| 추매 score 체크 | 해당 없음 | `add_score_limit(55)` — `_handle_add_buy_signal()` 내부 2차 필터 |
| 현금 부족 | 하드 차단 | [H] +15 패널티로 score 억제 |

손절/익절은 현금·RSI 무관하게 동작. 의도적 설계 — 현금이 없어도 손절은 실행돼야 함.

### ⚠️ 알려진 허점 — 미모니터링 보유 종목

**현상**: `all_states`(모니터링 유니버스)에 없는 보유 종목은 score 계산 없이 `_check_unmonitored_holdings()`에서 **profit_pct만 보고** 손절/익절 판단.

```
모니터링 종목  → score 계산 → [A]~[H] 복합 신호 → 분할 매도
미모니터링 종목 → profit_pct 단순 비교 → 단일 트랜치 즉시 매도
```

**문제점**:
- RSI, DCF, 레짐 등 복합 신호 반영 없이 단순 수익률만 보고 매도 결정
- 분할 매도(SplitSellOrderState) 없이 단일 트랜치 실행 — 대량 보유 시 슬리피지 위험

**수정 방향**:
- 보유 종목이 `all_states`에 없을 경우 강제로 모니터링 유니버스에 등록 후 정규 score 경로 통과
- 또는 `_check_unmonitored_holdings()` 내부에서도 `calculate_score()` 호출 후 분할 매도 로직 적용

---

## 4. 분할 매수/매도

### 분할 매수 (`SplitOrderState`)

```
초회 진입 → _init_split_order()
  - total_qty = 1회 매수 수량 (점수 기반 산출)
  - split_count = STRATEGY_SPLIT_COUNT (기본 3)
  - 각 트랜치: total_qty / split_count (ceiling division)

매 실행 → _execute_split_tranche()
  - remaining_qty -= 트랜치 수량
  - remaining_qty ≤ 0 → 분할 완료, 상태 삭제
```

### 분할 매도 (`SplitSellOrderState`)

```
초회 익절/점수매도 → SplitSellOrderState 생성
  - total_qty = 트리거 시점 holding_qty
  - split_count = STRATEGY_SELL_SPLIT_COUNT (기본 5)
  - 매 실행: _get_sell_split_qty() = ceiling(remaining_qty / splits_left)
    → 앞 트랜치에 더 많이 할당 (예: 10주 5분할 → [2,2,2,2,2], 중간 재계산 시 앞에 몰림)

매 실행 → _update_sell_split_state()
  - splits_done += 1
  - remaining_qty -= sold_qty
  - remaining_qty ≤ 0 → 완료, 상태 삭제
```

### 분할 상태 상호 취소

```
점수 SELL 신호 발생 시:
  → split_orders[ticker].pop()  # 진행 중인 분할 매수 취소

점수 BUY로 회복 시 (score <= buy_max):
  → sell_split_orders[ticker].pop()  # 진행 중인 분할 매도 취소
```

### ✅ 구현 완료 — split_orders 만료 기한 (2026-03-14)

`SplitOrderState.start_date` 기준으로 `STRATEGY_SPLIT_EXPIRE_DAYS`(기본 5일) 초과 시 `_expire_split_orders()` 내에서 자동 파기.
`_execute_collected_signals()` 루프 진입 전 매번 호출.

### 추매와 split tranche 간 쿨다운 처리

`_process_single_signal()` 처리 순서상 추매(`_handle_add_buy_signal()`)가 성공하면 즉시 `return`되어 split tranche는 같은 루프에서 실행되지 않음.

`_handle_buy_split()`에서 `has_pending_splits=True`인 경우 `_is_buy_cooldown_active` 체크를 **건너뜀**. 즉, split tranche는 add_buy_cooldown에 차단되지 않으며 추매와 독립적으로 처리됨. (정상 동작)

---

### ⚠️ 알려진 허점 — 분할 매도 수량 트리거 시점 고정

**현상**: `SplitSellOrderState.total_qty`가 SELL 트리거 시점 보유 수량으로 고정됨.

```
트리거 후 추매 발생 → 실제 보유 수량 증가 → 초과분 매도 계획에서 누락
트리거 후 손절 발생 → 실제 보유 수량 감소 → 분할 매도 수량 > 실제 보유 → KIS 주문 실패
```

**수정 계획**: `_get_sell_split_qty()` 호출 시점에 실제 `holding_qty`를 재조회하여 `remaining_qty` 보정.

### ✅ 구현 완료 — USD 루프 내 현금 차감 (2026-03-15)

`_deduct_loop_cash(ticker, spent_krw, spent_usd, cash_balance, usd_cash)` 헬퍼가 KR/US 모두 처리.
- KR 매수 후: `cash_balance -= spent_krw`
- US 매수 후: `usd_cash -= spent_usd`
- `_execute_collected_signals` 루프 내 매 신호 처리 후 자동 호출, 다음 신호에 반영됨.

---

### sell_cooldown 설정 시점

```
_handle_profit_take_signal() 및 _handle_sell_signal() 공통:
  → sell_cooldown[ticker] = today
  → 주문 성공/실패 불문 항상 설정 (당일 재시도 차단)
  → 동일 ticker 익절·점수매도 모두 공유 (하나가 설정되면 둘 다 차단)
```

### committed cash 관리

~~분할 매수 예약된 미집행 금액을 현금 계산 시 차감~~ → **2026-03-12 제거됨**

미집행 트랜치 금액을 미리 차감하면 내일/모레 쓸 돈이 오늘 신규 매수를 차단하는 비효율 발생.
현재는 `_calculate_committed_cash`를 호출하지 않으며, 현금은 실제 잔고 원본 그대로 사용.
현금 부족 시 split tranche는 `qty=0`으로 자연스럽게 실패 후 다음날 재시도.

### ✅ 미체결 주문 관리 (2026-03-16)

```
주문 접수 (KIS "success")
  ├─ 현금: 즉시 차감 (KIS 예수금 잠금과 동일)
  ├─ DB: trade_history에 status='pending' 기록
  └─ 프론트: 잔고 카드에 "미체결 자산" 섹션 표시

다음 루프 시작 시:
  ├─ _verify_pending_orders() → KIS 미체결 API로 체결 확인
  │    ├─ 체결 완료 → status='filled'
  │    └─ 미체결 유지 → pending 유지
  └─ _place_and_record() → 동일 종목+방향 pending 있으면 스킵
```

### 매수 수량 점수 승수

```python
# _calculate_buy_quantity() 내부 (execution_service_v2.py)
multiplier = 2.0 if score >= 90 else (1.5 if score >= 80 else 1.0)
target_invest_krw = market_total × STRATEGY_PER_TRADE_RATIO × multiplier
```

> score가 90 이상(강한 BUY 신호)일 때 2배, 80 이상일 때 1.5배 투자.
> 통상적인 BUY 신호(score ≤ 30)는 1배. forced_sell(score=100)로 매수 시 2배 적용.

### 루프 내 cash_balance 실시간 차감

```
_execute_collected_signals() 루프:
  매 신호 실행 후 sig_spent_krw(매수에 사용된 KRW)를 cash_balance에서 차감
  → 다음 신호 처리 시 남은 현금 기준으로 계산
  → KR 종목 매수에만 적용 (USD는 별도 조회)
```

### 신호 실행 우선순위 (`_execute_collected_signals`)

`for sig in prepared_signals` 루프 진입 전 정렬:

```
0 (최우선): 신규 종목 — holding=None, split_orders에 없음
1          : 기존 보유 — 익절/손절/추매
2 (후순위) : split tranche 연속 — split_orders에 있음
```

---

## 5. 손절/익절 로직

### 강제 손절 (Stop-Loss)

```
profit_pct ≤ STRATEGY_STOP_LOSS_PCT (-8%)
  → [B] _score_portfolio() 즉시 return (0, ["stop_loss_hit"], forced_sell=True)
  → _apply_score_components()에서 감지 → score=100 반환, 이하 컴포넌트 계산 생략
  → _process_single_signal()에서 forced_sell 감지 → _handle_forced_sell() 호출
  → _execute_trade_v2(side="sell", forced_qty=holding.quantity) 호출
  → _execute_sell_order(forced_qty=holding_qty) → sell_qty = holding_qty (전량 즉시 매도)
  → split_orders / sell_split_orders 즉시 제거, 쿨다운 없음
  → ✅ _set_panic_lock(ticker, user_state) → 재매수 3일 차단 (2026-03-16)
```

### ✅ 손절 후 재매수 차단 (panic_lock) — 2026-03-16

```
손절 실행 성공 시:
  → _set_panic_lock(ticker, user_state)
  → panic_locks[ticker] = "2026-03-16" (당일 날짜)
  → DB StrategyState에 영속

다음 루프 calculate_score() 진입 시:
  → ticker in panic_locks → score=50 반환 (중립, 매수/매도 트리거 안 됨)
  → RSI < oversold_rsi(30)이면 → score=20 (강한 반등 시에만 재진입 허용)

만료:
  → _clear_expired_panic_locks(user_state, expire_days=3)
  → 3일 경과 후 자동 해제
  → _execute_collected_signals() 루프 시작 시 매번 호출
```

### 점수 범위 규칙

| 점수 | 의미 |
|------|------|
| 0 | ❌ 사용 불가 (매수 차단 예약, 시스템 내부용) |
| 1~30 | 매수 신호 (낮을수록 강한 매수) |
| 31~69 | 중립 (홀드) |
| 70~99 | 매도 신호 (높을수록 강한 매도) |
| 100 | 강제 손절 (forced_sell) |

> `max(1, min(100, score))` — 최소 1점, 0점은 no_price_data 등 비정상 상태 예약.
> `no_price_data` 시 score=50(중립) 반환 — 가격 없는 종목 매수/매도 방지.

### 익절 (Take-Profit) — ✅ 레짐별 분기 (2026-03-18)

```
profit_pct ≥ take_profit_pct (레짐별 상이)
  → [B] score += PROFIT_TAKE_TARGET (+30)
  → _process_single_signal() 1순위 체크: _handle_profit_take_signal()
  → _get_sell_split_qty()로 SplitSellOrderState 생성 (split_count=5)
  → 분할 매도 (트랜치당 ceiling(remaining/splits_left))
  → sell_cooldown[ticker] = today (성공/실패 무관)
```

레짐별 익절 임계값:

| 레짐 | 익절 기준 | 설정키 |
|------|----------|--------|
| BULL | 7% | `STRATEGY_TAKE_PROFIT_PCT_BULL` |
| NEUTRAL | 5% | `STRATEGY_TAKE_PROFIT_PCT_NEUTRAL` |
| BEAR | 3% | `STRATEGY_TAKE_PROFIT_PCT_BEAR` |

> `_get_take_profit_pct_by_regime()` — `position_service.py`, `signal_service.py` 양쪽에서 사용.
> `_load_execution_config(macro_data)` 및 `calculate_score()` 내부에서 레짐별 값 적용.

### 트레일링 스탑 — ✅ 구현 완료 (2026-03-14)

레짐별 트레일링 스탑 임계값:

| 레짐 | 트레일링 스탑 | 동작 |
|------|------------|------|
| BULL | -7% (고점 대비) | 상승 추세 끝까지 따라가다 반전 시 매도 |
| NEUTRAL/BEAR | -5% (고점 대비) | 고점 대비 5% 하락 시 즉시 매도 |

트레일링 스탑 동작 (position_service.py):
```
매 신호 처리 시:
  1. _update_trailing_high(ticker, current_price, trailing_high)
     → trailing_high[ticker] = max(기존값, current_price)  # 항상 고점 추적
  2. _handle_trailing_stop(ticker, holding, current_price, trailing_high, macro)
     → drawdown = (current_price - high) / high × 100
     → drawdown <= _get_trailing_stop_pct(macro) → _handle_forced_sell() 전량 즉시 매도
```

> `trailing_high`: `StrategyState.trailing_high` DB 컬럼 (JSON dict {ticker: float})으로 영속.

### 추매 쿨다운 (Add-Buy Cooldown)

```
매수 실행 후 add_buy_cooldown[ticker] = BuyCooldownEntry(date, price)
재매수 조건:
  1. 쿨다운 날짜 이후 (다음 거래일)
  2. OR 현재가 < cooldown_price × 0.95 (-5% 추가 하락)
```

### 미국 주식 주문 직전 현재가 재조회

```
매수/매도 모두 적용:
  _execute_buy_order(), _execute_sell_order() 내부
  → is_kr(ticker) == False 인 경우
  → _fetch_fresh_us_price(ticker, fallback) 호출
  → KisFetcher.fetch_overseas_price() 로 실시간 가격 재조회
  → 실패 시 기존 current_price(fallback) 사용
  (국내 종목은 재조회 없이 상태 캐시 가격 그대로 사용)
```

### ✅ 수정 완료 — 손절 전량 즉시 매도 (2026-03-15)

`_handle_forced_sell()`이 `forced_qty=holding.quantity`를 전달하여 `_execute_sell_order`에서 전량 즉시 매도. sell_split_orders 잔존 문제 해결.

---

## 6. 현금 비중 관리

### 목표 현금 비중 (레짐별)

| 레짐 | KR 현금 비중 | US 현금 비중 |
|------|------------|------------|
| BULL | `STRATEGY_TARGET_CASH_RATIO_KR_BULL` (0.20) | 0.20 |
| NEUTRAL | `STRATEGY_TARGET_CASH_RATIO_KR_NEUTRAL` (0.40) | 0.40 |
| BEAR | `STRATEGY_TARGET_CASH_RATIO_KR_BEAR` (0.50) | 0.50 |

### 검사 로직

```python
_is_cash_below_target(ticker, holdings, cash_balance, ...)
  → KR 또는 US 시장별 현재 현금비중 계산
  → 현재비중 ≤ 목표비중 → True 반환 → 매수 차단
  → 패닉장(VIX≥25 OR F&G≤30) → False 반환 → 매수 허용
```

### ⚠️ 알려진 이슈 — 하드 게이트와 주문 직전 체크의 불일치

공포장(VIX ≥ 25)에서 현금 부족 시:
- [1] 하드 게이트 → 미보유 종목 skip (공포장 예외 없음)
- [3] 주문 직전 → 공포장이면 현금 부족 무시

[1]에서 이미 차단되어 [3]까지 도달하지 못함 → 공포장 매수 허용 로직이 실제로 동작 안 할 수 있음.

---

## 6-1. ✅ 자산관리 서비스 (구현 완료 2026-03-14)

> 기존 score 루프의 **상위 레이어**로 동작. 포트폴리오 전체 자산 배분을 능동적으로 관리.
> `AssetManagementService._get_target_cash_ratio()` 는 Section 6의 execution_service_v2 비율과 **별도**로 동작:
> - extreme fear (fear_greed < 10) → 0.0% (전액 투자)
> - BEAR → base 20% (저가 매수 공격적 대응)
> - NEUTRAL → base 40%
> - BULL → base 40% (보유 종목 30%이상이 수익 초과 시 +10%p, 상한 60%)

### 동작 방식

```
[자산관리 레이어] — 매 루프 실행
  │
  ├─ 1. 현재 현금 비중 계산
  │      현금 비중 = cash / (보유종목 시장가 + cash)
  │      목표 현금 비중 = 레짐별 (BEAR 20% / NEUTRAL 40% / BULL 40%)
  │
  ├─ 2. 여유/부족 판단 (임계값 ±5% 이내면 조정 안 함)
  │      현금 비중 > 목표 + 5%  → 여유 (매수 여력 있음)
  │      현금 비중 < 목표 - 5%  → 부족 (현금 조달 필요)
  │      그 외                  → 유지 (조정 없음)
  │
  ├─ 3. 현금 여유 시 → 매수
  │      score 낮은 순(BUY 신호 강한 순) 후보 선정
  │      → 여유 현금 소진될 때까지 순서대로 매수
  │
  ├─ 4. 현금 부족 시 → 매도 후 매수
  │      매도 우선순위: score × 이익률 복합 정렬
  │        ① score 높을수록 우선 (매도 신호 강한 것)
  │        ② 이익률 높을수록 우선 (실현 손실 최소화)
  │        → 부족분 현금 확보될 때까지 순서대로 일부 매도
  │      → 확보된 현금으로 매수 후보 실행
  │
  └─ [기존 score 루프] — 하위에서 독립 실행
       손절 / 익절 / 추매 등 개별 종목 신호 처리

```

### 섹터 리밸런서 통합

기존 `sector_rebalancer_service.py`(매주 월요일 1회) 제거.
자산관리 서비스가 매 루프마다 현금 비중과 함께 섹터 편차도 매수 우선순위 산정에 반영하여 대체.

### ✅ 구현 완료 (2026-03-14)

- `services/strategy/asset_management_service.py` 신규 서비스 작성
- `trading_strategy_service.py` 에서 score 루프 실행 **이후** AssetManagementService.run() 호출
- `weekly_sector_rebalance` 스케줄러 잡 제거 완료

> 현재 AssetManagementService는 score 루프 **이후** 실행되어 잔여 예산 소진/현금 확보 목적으로 동작.

### ✅ 쿨다운 연동 (2026-03-17)

`run_strategy()` → `AssetManagementService.run(user_state=user_state)` 로 쿨다운 상태 전달.
- `execute_buy_budget()`: `add_buy_cooldown` 체크 후 매수, 성공 시 쿨다운 설정
- `execute_sell_for_cash()`: `sell_cooldown` 체크 후 매도, 성공 시 쿨다운 설정
- 1단계(신호 실행)에서 매수/매도한 종목이 2단계(자산관리)에서 중복 실행되지 않음

### ✅ 쿨다운 영속 버그 수정 (2026-03-18)

**버그**: `_save_state(state)`가 `AssetManagementService.run()` **이전**에만 호출되어, 자산관리에서 설정한 쿨다운이 DB에 저장되지 않았음 → 매 루프마다 동일 종목 반복 매도.
**수정**: `AssetManagementService.run()` **이후** `_save_state(state)` 추가 (2차 저장).

### ✅ 에셋 확보 매도 최소 수익률 (2026-03-18)

`_select_sell_candidates()`: 수익률 `>= 1%` 이상인 종목만 매도 대상.
수수료(약 0.25~0.5%) 고려하여 수익률 0% 근처 종목의 무의미한 매도 방지.

---

## 7. 섹터 비중 관리 — ❌ 제거 예정

> **자산관리 서비스(Section 6-1)로 대체. 아래 항목 전체 제거.**

### 제거 대상

| 항목 | 파일/위치 | 비고 |
|------|---------|------|
| 섹터 리밸런서 서비스 | `services/strategy/sector_rebalancer_service.py` | 파일 전체 삭제 |
| 스케줄러 잡 | `weekly_sector_rebalance` (매주 월 09:20) | 스케줄러에서 제거 |
| 섹터 보너스 점수 [G] | `signal_service.py` `_score_bonuses()` | 섹터 편차 ±10 로직 제거 |
| 섹터 비중 검사 | `execution_service_v2.py` `_check_sector_group_limit()` | 함수 제거 |
| 섹터 비중 계산 | `execution_service_v2.py` `_get_sector_group_weights()` | 함수 제거 |
| 섹터 관련 상수 | `execution_service_v2.py` `SECTOR_GROUP_MAP`, `SECTOR_TARGET_WEIGHT`, `SECTOR_REBAL_THRESHOLD` | 제거 |
| 섹터 관련 설정키 | `SECTOR_TARGET_{MARKET}_{GROUP}` 등 | DB Settings에서 제거 |

---

## 8. 시장 레짐 판정

**파일**: `services/market/macro_service.py`
**캐시**: In-Memory 1시간 TTL (`_cache['macro']`)

---

### 전체 흐름

```
get_macro_data()  [1시간 캐시]
  │
  ├─ _get_vix()                    ← KIS API → yfinance ^VIX 폴백
  ├─ _get_fear_greed_index()        ← CNN Fear & Greed API
  ├─ _get_economic_indicators()     ← FRED 14개 지표 (병렬 조회)
  ├─ _get_us_10y_yield()            ← yfinance ^TNX
  │
  └─ _get_market_regime(vix, fng, econ, yield)
       │
       ├─ [1] technical_20   SPX 2년 일봉 — EMA 정렬 + 모멘텀 + ATH 하락률
       ├─ [2] vix_20         VIX 수준 + 1개월 변화 속도
       ├─ [3] fng_20         Fear & Greed 6단계
       ├─ [4] econ_20        FRED 경제지표 가중합산
       ├─ [5] other_20       금리/금리차/DXY/BTC/금/오일
       │
       ├─ [6] 경제 국면 판정 → phase_modifier (±8점)
       │
       └─ regime_score = [1]~[5] 합산 + phase_modifier
            ≥ 65            → Bull
            ≤ bear_threshold → Bear  (동적, 기본 40)
            그 외            → Neutral

  → DB 저장 (MarketRegimeHistory, 일 1회 스냅샷)
```

---

### 최종 판정 공식

```
regime_score = technical_20 + vix_20 + fng_20 + econ_20 + other_20 + phase_modifier
               (범위: 0 ~ 100)

regime_score ≥ 65            → Bull
regime_score ≤ bear_threshold → Bear
그 외                         → Neutral
```

---

### [1] Technical (0~20점)

SPX 2년 일봉(`^GSPC`), NDX 1개월(`^NDX`) yfinance 조회.

#### EMA 정렬 점수 (현재가 위치)

| 기준 | 현재가 위 | 현재가 아래 |
|------|----------|------------|
| EMA5 | +12 | -12 |
| EMA20 | +16 | -16 |
| EMA60 | +16 | -16 |
| EMA120 | +20 | -20 |
| EMA200 | +24 | -24 |
| 완전 정배열 (5>20>60>120>200) | +12 | — |
| 완전 역배열 (5<20<60<120<200) | — | -12 |
| EMA20/60/120 기울기 상향 | 각 +4 | 각 -4 |

#### SPX/NDX 모멘텀 점수

| 지표 | 조건 | 점수 |
|------|------|------|
| SPX 1개월 수익률 | > +3% | +10 |
| | > +1% | +5 |
| | < -1% | -5 |
| | < -3% | -10 |
| NDX 1개월 수익률 | > +3% | +5 |
| | > +1% | +2 |
| | < -1% | -2 |
| | < -3% | -5 |
| SPX 2주 수익률 | > +2% | +6 |
| | > +0.5% | +3 |
| | < -0.5% | -3 |
| | < -2% | -6 |
| SPX 52주 ATH 대비 하락률 | -5% ~ 0 | 0 |
| | -10% ~ -5% | -2 |
| | -20% ~ -10% | -4 |
| | < -20% | -8 |

→ 전체 합산 후 `_to_20(raw, max_abs)` 로 0~20 정규화

---

### [2] VIX (0~20점)

KIS API 우선, 실패 시 yfinance `^VIX` 폴백.

#### VIX 수준 점수 (±8)

| VIX 값 | 점수 |
|--------|------|
| ≤ 13 (극도 안정) | +8 |
| ≤ 18 | +4 |
| 18 < VIX < 22 | 0 |
| ≥ 22 | -1 |
| ≥ 25 | -3 |
| ≥ 30 | -5 |
| ≥ 35 (극도 공포) | -8 |

#### VIX 속도 보정 (1개월 변화율, ±4)

| 1개월 VIX 변화율 | 점수 |
|----------------|------|
| > +40% (급등) | -4 |
| > +20% | -2 |
| < -30% (급락) | +2 |
| < -15% | +1 |

→ 합산 후 `_to_20(raw, 12)` 로 0~20 정규화

---

### [3] Fear & Greed (0~20점)

CNN Fear & Greed API (0~100 지수) 6단계 변환:

| F&G 값 | 의미 | 점수 |
|--------|------|------|
| ≤ 15 | 극도 공포 | -10 |
| ≤ 25 | 공포 | -6 |
| ≤ 40 | 약한 공포 | -3 |
| 41~54 | 중립 | 0 |
| ≥ 55 | 약한 탐욕 | +3 |
| ≥ 70 | 탐욕 | +6 |
| ≥ 85 | 극도 탐욕 | +10 |

→ `_to_20(raw, 10)` 로 0~20 정규화

---

### [4] Economic / FRED (0~20점)

FRED 14개 지표를 **ThreadPoolExecutor(max_workers=8)** 병렬 조회.
각 지표는 **직전값 대비 개선/악화**로 판단, 가중치 합산.

| 지표 | FRED ID | 방향 | 가중치 |
|------|---------|------|--------|
| CPI | CPIAUCSL | 낮을수록 좋음 | 3 |
| 실업률 | UNRATE | 낮을수록 좋음 | 3 |
| 비농업 고용(NFP) | PAYEMS | 높을수록 좋음 | 3 |
| PMI (제조업) | IPMAN | 높을수록 좋음 | 2 |
| 소비자신뢰지수 | UMCSENT | 높을수록 좋음 | 2 |
| PPI | PPIACO | 낮을수록 좋음 | 2 |
| 소매판매 | RSXFS | 높을수록 좋음 | 2 |
| 내구재 주문 | DGORDER | 높을수록 좋음 | 2 |
| 실업수당 청구 | ICSA | 낮을수록 좋음 | 2 |
| 산업생산 | INDPRO | 높을수록 좋음 | 1 |
| 설비가동률 | TCU | 높을수록 좋음 | 1 |
| 평균 시급 | CES0500000003 | 낮을수록 좋음 | 1 |
| 주택착공 | HOUST | 높을수록 좋음 | 1 |
| 건축허가 | PERMIT | 높을수록 좋음 | 1 |

→ `_to_20(econ_score, 10)` 로 0~20 정규화

---

### [5] Other / 복합자산 (0~20점)

yfinance + FRED(DGS2) 기반. 최대 합산 ±28점 → 0~20 정규화.

| 항목 | 조건 | 점수 |
|------|------|------|
| 미국 10년 금리 | ≤ 3.5% | +8 |
| | ≤ 4.0% | +4 |
| | ≤ 4.5% | 0 |
| | ≤ 5.0% | -4 |
| | > 5.0% | -8 |
| 장단기 금리차 (10Y-2Y) | > +1.0% (정상) | +6 |
| | > +0.3% | +3 |
| | < -0.3% (역전) | -3 |
| | < -1.0% (심각) | -6 |
| DXY 1개월 등락 | > +3% (강달러) | -4 |
| | > +1% | -2 |
| | < -1% (약달러) | +2 |
| | < -3% | +4 |
| BTC 1개월 등락 | > +20% | +3 |
| | > +10% | +1 |
| | < -12% | -1 |
| | < -25% | -3 |
| 금(Gold) 1개월 | > +5% (안전자산 선호) | -4 |
| | > +2% | -2 |
| | < -2% | +2 |
| | < -5% | +4 |
| 오일(WTI) 1개월 | > +20% | -3 |
| | > +10% | -2 |
| | > +5% | -1 |
| | < -5% | +1 |
| | < -15% | +2 |

---

### [6] 경제 국면 5단계 분류 (phase_modifier)

인플레이션 압력과 성장 신호를 조합해 5단계로 분류, **regime_score에 직접 가산**.

#### 인플레이션 압력 계산 (-10 ~ +10)

```
오일 1개월 상승률:
  > +20%: +2,  > +10%: +1,  > +5%: +1
  < -15%: -2,  < -5%: -1

CPI MoM (가중치 3):
  > 0.4%: +3,  > 0.2%: +2,  > 0.1%: +1,  < 0: -2

PPI MoM (가중치 2):
  > 0.4%: +2,  > 0.2%: +1,  > 0.1%: +1,  < 0: -1
```

#### 성장 신호 계산 (-10 ~ +10)

```
SPX 1개월 수익률:
  > +3%: +3,  > +1%: +1,  < -1%: -1,  < -3%: -2,  < -5%: -3

VIX (절반 반영):
  ≥ 30: -2,  ≥ 25: -1,  ≥ 20: -1,  ≤ 13: +2,  ≤ 18: +1

F&G (절반 반영):
  ≤ 20: -1,  ≤ 35: -1,  ≥ 65: +1,  ≥ 80: +1

FRED 경제지표: (econ_20 - 10) 직접 가산
```

#### 국면 판정 및 점수 보정

| 국면 | 조건 | phase_modifier |
|------|------|----------------|
| Stagflation | 인플레 ≥ 5 AND 성장 ≤ -4 | **-8** |
| Deflation | 인플레 ≤ -3 AND 성장 ≤ -3 | **-5** |
| Inflation | 인플레 ≥ 3 | **-3** |
| Reflation | 인플레 > 0 AND 성장 ≥ 0 | **+2** |
| Goldilocks | 인플레 ≤ 0 AND 성장 ≥ 3 | **+5** |
| Neutral | 그 외 | **0** |

---

### Bear 임계값 — 동적 조정

```
_get_bear_threshold():
  기본값: 40
  최근 2개월 이력(DB) 확인:
    직전 1개월 Bear:      → 44  (한 번 Bear면 탈출 더 어렵게)
    직전 2개월 모두 Bear: → 48  (두 달 연속 Bear면 더욱 보수적)
```

Bear 임계값이 높아질수록 Bear → Neutral 전환이 더 어려워짐.

---

### 공포장 판정 (별도)

```python
_is_panic_market(macro) → bool
  → VIX ≥ 25 OR Fear&Greed ≤ 30
```

레짐 판정(regime_score)과 **독립적**. 공포장 시 매수 억제 해제(PANIC_MARKET_BUY -30 적용).

---

### 캐시 갱신 시점

| 트리거 | 동작 |
|--------|------|
| 1시간 TTL 만료 | 자동 전체 재계산 |
| FRED 경제지표 발표 감지 (08:31 / 09:16 / 10:01 ET) | `refresh_on_release()` → 캐시 초기화 → 재계산 → Slack 알림 |
| 수동 | `invalidate_cache()` → 다음 호출 시 재계산 |

재계산 후 결과는 `MarketRegimeHistory` DB에 일 1회 스냅샷 저장 (`_save_regime_snapshot`).
SPX 데이터 조회 실패 시 DB 저장 생략.

---

### ⚠️ 개선 계획 — 레짐 스코어

**1. 레짐 스코어 EMA 평활화**

```
현재: 매 재계산 시 이전 값과 완전 독립 → 하루 단위 레짐 급변 가능

개선:
  regime_score = 현재 × 0.4 + 이전 스코어 × 0.6
  → 급격한 레짐 전환 방지, 현금 목표비중/익절기준 안정화
  → MacroService에서 직전 regime_score를 캐시에 보존 후 적용
```

**2. 극단적 공포 시 Bear 즉시 강제 지정**

```
현재: VIX 60이어도 다른 컴포넌트가 높으면 Neutral 가능

개선: VIX ≥ 40 OR F&G ≤ 10 → 스코어 무관하게 Bear 강제 지정
  → _assemble_regime_result() 에서 스코어 판정 전 사전 체크
```

**3. phase_modifier 범위 ±8 → ±15**

```
현재: Stagflation -8, Goldilocks +5 — 전체 스코어 대비 영향 미미

개선:
  Stagflation → -15
  Deflation   → -10
  Inflation   → -5
  Reflation   → +3
  Goldilocks  → +8
  → 경제 국면이 레짐 판정에 실질적 영향
```

**4. Bull 임계값 동적화**

```
현재: Bull 임계값 65 고정 (Bear 임계값만 동적)

개선: Bear 지속 시 Bull 진입 기준도 상향
  직전 1개월 Bear → Bull 임계값 67
  직전 2개월 Bear → Bull 임계값 70
  → Bear 탈출 후 섣부른 Bull 진입 방지, Neutral 충분히 거치도록
  → _get_bear_threshold()와 동일 패턴으로 _get_bull_threshold() 추가
```

**5. 컴포넌트 가중치 재조정 (실시간 우선)**

```
현재: Technical/VIX/F&G/FRED/복합자산 각 0~20 동일 비중

개선: 실시간 데이터 가중치 상향, FRED(지연 데이터) 하향
  Technical  0~25 (현재가 기반, 실시간)
  VIX        0~25 (실시간)
  F&G        0~20 (실시간)
  FRED 경제  0~15 (월/주 발표, 지연)
  복합자산   0~15 (일별, 준실시간)
  합계: 0~100 유지
```

---

## 9. DCF 밸류에이션

**파일**: `services/analysis/dcf_service.py`, `services/analysis/financial_service.py`

### 2단계 DCF 모델

```
Stage 1 (고성장, DCF_STAGE1_YEARS=10년):
  각 년도 FCF = 직전 FCF × (1 + growth_rate)
  PV = FCF / (1 + discount_rate)^n

Terminal Value:
  TV = FCF_10 × (1 + DCF_TERMINAL_GROWTH) / (discount_rate - DCF_TERMINAL_GROWTH)
  PV_TV = TV / (1 + discount_rate)^10

Fair Value = (sum(Stage1_PV) + PV_TV) / shares_outstanding
```

### 할인율 결정

```
Base: DCF_DEFAULT_DISCOUNT_RATE (10%)
+ 현금흐름 변동성 반영 (최대 +6%)
범위: DCF_DISCOUNT_RATE_FLOOR(6%) ~ DCF_DISCOUNT_RATE_CEIL(15%)
```

### 데이터 소스 우선순위 (폴백)

```
1. DcfOverride (사용자 수동 오버라이드 — DB DcfOverride 테이블)
2. 5년 EPS CAGR (_dcf_from_eps_history)
   └─ DB Financials 이력에서 연도별 EPS 추출 → CAGR 계산 → 할인율 산출
   └─ 5년치 미만이면 skip
3. yfinance FCF (_dcf_from_yfinance)
   └─ fcf_per_share > 0 일 때만 사용
4. 기관 컨센서스 목표주가 (_dcf_from_analyst_target)
   └─ yfinance target_mean_price (애널리스트 평균 목표주가)
   └─ FCF 음수 / 적자 기업 등 DCF 계산 불가 종목 대상
   └─ fallback_fair_value로 설정 → DCF 계산 없이 목표주가 자체를 공정가치로 사용
   └─ 이 단계가 성공하면 DCF 없음 패널티(+10) 발생하지 않음
5. EPS × PER 추정 (_dcf_from_eps_per_fallback)
   └─ DB 최신 EPS × PER → fallback_fair_value
6. KIS API (_dcf_from_kis_api, 최후 수단 — 항상 반환)
   └─ KIS 재무 데이터 → EPS × PER 또는 FCF 그대로 사용
```

> **캐시**: 메모리 30분 TTL (`_dcf_input_by_ticker`). 오버라이드는 캐시 우선 확인 없이 항상 DB 조회.

---

### ⚠️ 개선 계획 — DCF 밸류에이션

**1. 성장률 상한 25% 캡핑 문제**

```
현재: CAGR 계산 후 min(25%, cagr) 클리핑
  → 고성장 초기 기업(50%+ 성장)은 25%로 고정
  → 실제 성장 포텐셜 과소 평가 → 저평가 종목을 공정가치로 판단

개선:
  yfinance analyst growth estimate 우선 반영
  또는 업종별 상한 차등 적용 (tech: 40%, value: 20%)
```

**2. 기관 목표주가 커버리지 가중치 없음**

```
현재: target_mean_price만 사용, 애널리스트 수 무시
  → 커버리지 1명짜리 목표주가와 30명짜리 동일 신뢰도

개선:
  number_of_analyst_opinions < 3 → analyst_target 폴백 skip
  → EPS×PER(5번)으로 내려가도록
```

**3. Top100 외 종목 DCF 업데이트 주기 없음**

```
현재: sync_daily_market(04:00)은 Top100만 갱신
  → Top100 밖 보유 종목은 수개월 된 EPS로 DCF 계산 가능

개선:
  보유 종목은 Top100 여부 무관하게 daily 갱신 대상에 포함
  → trading_strategy_service.py 의 _update_target_universe()에서 보유 종목 강제 포함
```

---

## 10. 틱 트레이딩 플로우 — ✅ 제거 완료 (2026-03-14)

아래 항목 모두 제거됨:

| 항목 | 위치 | 상태 |
|------|------|------|
| 틱 트레이딩 로직 | `trading_strategy_service.py` `_run_tick_trade()` 및 관련 메서드 | ✅ 삭제 완료 |
| 틱 트레이딩 상태 | `StrategyState.tick_trade` 컬럼 | ✅ 제거 완료 |
| 스케줄러 잡 | `report_tick_trade_status` (매 10분) | ✅ 제거 완료 |
| 틱 관련 설정키 | `STRATEGY_TICK_ENABLED`, `STRATEGY_TICK_TICKER` 등 8개 | ⚠️ DB Settings 잔존 (무효화)

---

## 11. 스케줄러 작업표

| 잡 ID | 시간 | 주기 | 역할 |
|--------|------|------|------|
| sync_daily_market | 04:00 KST | 매일 | Top100 시세/지표/DCF DB 동기화 |
| manage_subscriptions | 08:30 KST | 매일 | WebSocket 구독 갱신 |
| run_trading_strategy | - | 매 1분 | 전략 실행 루프 |
| kr_close_report | 15:35 KST | 매일 | KR 장마감 리포트 |
| us_close_report | 06:05 KST | 매일 | US 장마감 리포트 |
| report_daily_trade_history | 09:00 KST | 매일 | 일일 매매 내역 Slack 전송 |
| run_rebalancing | 09:10 KST | 매일 | 포트폴리오 리밸런싱 |
| ~~weekly_sector_rebalance~~ | ~~월 09:20 KST~~ | ~~매주~~ | ~~섹터 리밸런싱~~ ❌ 제거 |
| refresh_low_tier_prices | - | 매 5분 | LOW tier 종목 가격 폴링 |
| sync_portfolio_periodic | - | 매 10분 | KIS 잔고 동기화 |
| ~~report_tick_trade_status~~ | ~~매 10분~~ | ~~매 10분~~ | ~~틱 트레이딩 상태~~ ❌ 제거 |
| econ_0830/0915/1000 | 08:31/09:16/10:01 ET | 매일 | FRED 경제지표 발표 확인 |
| vix_spike_check | 09:00~15:00 ET | 30분 (월-금) | VIX 급등 감지 |

---

## 12. 주요 설정값 (DB Settings)

> `SettingsService.get_float()`, `get_int()`으로 런타임 조회

### 전략 임계값

| 설정키 | 기본값 | 설명 |
|--------|--------|------|
| `STRATEGY_BUY_THRESHOLD_MAX` | 30 | 매수 점수 임계값 |
| `STRATEGY_SELL_THRESHOLD_MIN` | 70 | 매도 점수 임계값 |
| `STRATEGY_BASE_SCORE` | 50 | 기본 점수 |
| `STRATEGY_TAKE_PROFIT_PCT_BULL` | 7.0 | 익절 기준 BULL (%) |
| `STRATEGY_TAKE_PROFIT_PCT_NEUTRAL` | 5.0 | 익절 기준 NEUTRAL (%) |
| `STRATEGY_TAKE_PROFIT_PCT_BEAR` | 3.0 | 익절 기준 BEAR (%) |
| `STRATEGY_STOP_LOSS_PCT` | -8.0 | 손절 기준 (%) |
| `STRATEGY_ADD_POSITION_BELOW` | -5.0 | 추매 기준 하락율 (%) |
| `STRATEGY_OVERSOLD_RSI` | 30 | 과매도 RSI |
| `STRATEGY_OVERBOUGHT_RSI` | 70 | 과매수 RSI |
| `STRATEGY_DIP_BUY_PCT` | -5.0 | 급락 매수 기준 (%) |
| `STRATEGY_RSI_BUY_BLOCK` | 75.0 | 신규 매수 하드 차단 RSI (score 계산 이전) |
| `STRATEGY_ADD_BUY_RSI_LIMIT` | 60.0 | 추매 허용 최대 RSI |
| `STRATEGY_ADD_BUY_SCORE_LIMIT` | 55 | 추매 허용 최대 score |
| `STRATEGY_TOP10_BONUS` | 10 | 시총 Top10 매수 보너스 (점수 -N) |
| `STRATEGY_ALLOW_EXTENDED_HOURS` | 1 | 연장 거래 시간 허용 여부 |

### 매수 수량/비중

| 설정키 | 기본값 | 설명 |
|--------|--------|------|
| `STRATEGY_PER_TRADE_RATIO` | 0.05 | 1회 매매 비중 (5%) |
| `STRATEGY_SPLIT_COUNT` | 3 | 분할 매수 횟수 |
| `STRATEGY_SELL_SPLIT_COUNT` | 5 | 분할 매도 횟수 |
| `STRATEGY_SPLIT_EXPIRE_DAYS` | 5 | 분할 매수 만료 기한 (일) |
| `STRATEGY_MAX_SECTOR_RATIO` | 0.30 | 섹터 최대 비중 (30%) |

### 현금 비중 (레짐별)

| 설정키 | 기본값 |
|--------|--------|
| `STRATEGY_TARGET_CASH_RATIO_KR_BULL` | 0.20 |
| `STRATEGY_TARGET_CASH_RATIO_KR_NEUTRAL` | 0.40 |
| `STRATEGY_TARGET_CASH_RATIO_KR_BEAR` | 0.50 |
| `STRATEGY_TARGET_CASH_RATIO_US_BULL` | 0.20 |
| `STRATEGY_TARGET_CASH_RATIO_US_NEUTRAL` | 0.40 |
| `STRATEGY_TARGET_CASH_RATIO_US_BEAR` | 0.50 |

### 틱 트레이딩 — ❌ 제거됨 (2026-03-14)

> `STRATEGY_TICK_*` 설정키 8개는 DB에 잔존하나 코드에서 미사용.

---

## 13. Slack 알림 포맷

> `services/notification/report_service.py`, `services/strategy/execution_service_v2.py`

### 13-1. 자산 현황 리포트 (`format_portfolio_report`)

**보유종목 라인 — 1줄 compact 포맷 (2026-03-18 변경)**

KR:
```
  🔴 삼성전자 ₩72,000×10 | +5.9% (₩+40,000)
```

US:
```
  🔴 AAPL $185.20×5 | +8.9% ($+76.00)
```

---

### 13-2. 체결 알림 (`_send_trade_alert`) — ✅ 2026-03-18 리뉴얼

**개별 체결 시 즉시 발송. 트리거 이유 + 총자산/여유 현금 포함.**

매수:
```
🔵 *[B] HD한국조선해양* (₩421,000 × 1) | 분할 매수 | 총자산: ₩11,975,217 (여유: ₩3,768,117)
```

매도:
```
🔴 *[S] KT&G* (₩158,500 × 1) | Profit: ₩+3,700 (+2.39%) | 익절 | 총자산: ₩11,975,217 (여유: ₩3,768,117)
```

**트리거 라벨 매핑** (`_classify_reason()`):

| 내부 reason | 표시 라벨 |
|-------------|----------|
| `stop_loss(...)` | 손절 |
| `take_profit_zone(...)` | 익절 |
| `trailing_stop(...)` | 트레일링스탑 |
| `asset_management_cash_rebalance` | 에셋 확보 |
| `budget_buy [...]` | 예산 매수 |
| `add_position(...)` | 추매 |
| `score N [...] (M/N split)` | 분할 매수 |
| `score N [...]` | 점수기반 |

> `reason` 문자열은 DB `trade_history.result_msg`에도 저장됨.
> 기존 `format_trade_result_report` 요약 리포트는 개별 알림과 중복되어 비활성화 (2026-03-18).

---

**Last Updated**: 2026-03-18 (레짐별 익절, 트리거 정보 추가, Slack 포맷 리뉴얼, 쿨다운 영속 버그 수정, 에셋 확보 최소 수익률)
