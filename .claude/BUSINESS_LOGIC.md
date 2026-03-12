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

---

## 1. 전략 실행 플로우

```
run_strategy(user_id) [매 1분 실행]
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
  │      ├─ 익절 신호 우선 처리
  │      ├─ 손절 신호 처리 (forced_sell)
  │      ├─ 매수 신호 처리 (분할 매수)
  │      └─ 미모니터링 보유종목 손절/익절 체크
  │
  ├─ 5. 틱 트레이딩 (_run_tick_trade)
  │      └─ STRATEGY_TICK_ENABLED=1 일 때만 실행
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
  → _handle_sell_signal() 호출 (점수 매도 경로와 동일)
  → _get_sell_split_qty()로 수량 산출 → 첫 트랜치 ceiling division으로 매도
  → 주의: "전량 즉시 매도"가 아닌 분할 매도 첫 트랜치 실행
         (완전 청산은 매일 1트랜치씩 SELL_SPLIT_COUNT일 소요될 수 있음)
```

### 익절 (Take-Profit)

```
profit_pct ≥ STRATEGY_TAKE_PROFIT_PCT (3%)
  → [B] score += PROFIT_TAKE_TARGET (+30)
  → _process_single_signal() 1순위 체크: _handle_profit_take_signal()
  → _get_sell_split_qty()로 SplitSellOrderState 생성 (split_count=5)
  → 분할 매도 (트랜치당 ceiling(remaining/splits_left))
  → sell_cooldown[ticker] = today (성공/실패 무관)
```

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
_is_cash_ratio_sufficient(ticker, holdings, cash_balance, ...)
  → KR 또는 US 시장별 현재 현금비중 계산
  → 현재비중 < 목표비중 → 현금 부족 → 매수 억제 (+15점 패널티)
```

---

## 7. 섹터 비중 관리

### 섹터 그룹 (3개)

| 그룹 | 포함 섹터 | 기본 목표 비중 |
|------|---------|------------|
| tech | Technology, Software, Semiconductors 등 | 50% |
| value | Energy, Materials, Real Estate, Healthcare 등 | 30% |
| financial | Financial Services, Insurance, Banks 등 | 20% |

> 설정키: `SECTOR_TARGET_{MARKET}_{GROUP}` (DB로 오버라이드 가능)

### 리밸런싱 임계값

```
SECTOR_REBAL_THRESHOLD = 0.05 (5% 편차)
편차 > 5% → 리밸런싱 대상
```

### 주간 섹터 리밸런싱 (매주 월요일 09:20)

```
run_sector_rebalance(user_id)
  1. STEP 1: 초과 섹터 매도
     - 편차 내림차순 정렬
     - 초과 보유종목 중 점수 가장 낮은 것 매도

  2. STEP 2: 부족 섹터 매수
     - 점수 기반 후보 종목 스코어링
     - BUY_THRESHOLD 미만 최우량 종목 매수
```

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
1. DcfOverride (수동 오버라이드)
2. EPS CAGR (재무 이력 5개년)
3. yfinance FCF 데이터
4. EPS × PER 추정
5. KIS 재무 데이터
```

---

## 10. 틱 트레이딩 플로우

**파일**: `services/strategy/trading_strategy_service.py`

> 설정 `STRATEGY_TICK_ENABLED=1`, `STRATEGY_TICK_TICKER=AAPL` 등으로 활성화

### 진입 조건

```
_evaluate_tick_buy_conditions()
  - 초기 진입: 현재가 ≤ 당일 시가 × (1 + TICK_ENTRY_PCT(-1%))
  - 추매: 현재가 ≤ 직전 매수가 × (1 + TICK_ADD_PCT(-3%))
  - 재진입: 당일 매도 후 → 1시간 저가(low_1h) 추적 → 저가 대비 반등
```

### 청산 조건

```
_evaluate_tick_sell_conditions()
  - 익절: profit ≥ TICK_TAKE_PROFIT_PCT (1%)
  - 손절: profit ≤ TICK_STOP_LOSS_PCT (-5%)
  - EOD: 장종료 TICK_CLOSE_MINUTES(5분) 전 전량 매도
```

### 1시간 가격 윈도우

```
_update_price_window(trade_state, current_price)
  - 최근 60분 가격 슬라이딩 윈도우 유지
  - low_1h = 60분 내 최저가 (재진입 트리거)
```

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
| weekly_sector_rebalance | 월 09:20 KST | 매주 | 섹터 리밸런싱 실행 |
| refresh_low_tier_prices | - | 매 5분 | LOW tier 종목 가격 폴링 |
| sync_portfolio_periodic | - | 매 10분 | KIS 잔고 동기화 |
| report_tick_trade_status | - | 매 10분 | 틱 트레이딩 상태 Slack 전송 |
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
| `STRATEGY_TAKE_PROFIT_PCT` | 3.0 | 익절 기준 (%) |
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

### 틱 트레이딩

| 설정키 | 기본값 | 설명 |
|--------|--------|------|
| `STRATEGY_TICK_ENABLED` | 0 | 틱매매 활성화 여부 |
| `STRATEGY_TICK_TICKER` | - | 틱매매 대상 종목 |
| `STRATEGY_TICK_TAKE_PROFIT_PCT` | 1.0 | 틱 익절 (%) |
| `STRATEGY_TICK_STOP_LOSS_PCT` | -5.0 | 틱 손절 (%) |
| `STRATEGY_TICK_ADD_PCT` | -3.0 | 틱 추매 기준 (%) |
| `STRATEGY_TICK_ENTRY_PCT` | -1.0 | 틱 진입 기준 (%) |
| `STRATEGY_TICK_CASH_RATIO` | 0.20 | 트랜치당 현금 비중 |
| `STRATEGY_TICK_CLOSE_MINUTES` | 5 | EOD 청산 시간 (분전) |

---

**Last Updated**: 2026-03-12 (매도 로직 상세화 / BEAR 레짐 수정 / Section 8 레짐 판정 전체 상세화 / DCF 점수 오류 수정 / [B] 추매 조건 오류 수정 / 매수 수량 승수 오류 수정 / Section 12 설정키 5개 추가)
