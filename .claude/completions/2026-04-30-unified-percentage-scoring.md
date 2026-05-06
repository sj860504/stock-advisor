# 2026-04-30 — 점수 시스템 통합 비율(%) 기반으로 전환

## 배경

60일치(2026-03-01 ~ 2026-04-30) trade_history 분석 결과:
- KR 전체: 955 round-trip, 승률 46.5%, **누적 -1,786,290원**
- 큰 손실일(03-23 -1.2M, 04-27 -484k, 04-21 -168k)에 알고리즘 매수 폭주
- 핵심 원인: 옛 단계함수(step) 점수가 **컴포넌트 1개의 한방 트리거**로 매수 임계 통과
  - 예: `extreme_fear_buy_opportunity(-30)` + `target_entry_price_hit(-15)` + `EMA200_support(-10)`
    조합만으로 -55점 → 다른 신호 무시하고 매수

## 변경 — 모든 시그널 컴포넌트를 비율(linear) 기반으로 통일

| 컴포넌트 | 옛 (step) | 새 (linear) | 캡 |
|---------|----------|------------|----|
| **DCF_deviation** | 6단계 -25/-15/-10/-5/+10/+20 | `int(-undervalue_pct)` | ±25 |
| **RSI_deviation** | 4단계 ±5~±20 | `int(rsi - 50)` | ±15 |
| **EMA200_deviation** | target_buy(-15) / target_sell(+30) / EMA200_support(-10) | `int((curr-ema200)/ema200*100)` | ±15 |
| **change_deviation** | sharp_drop(-15) / sharp_surge(+15) 임계 ±5% | `int(change_rate * 3)` | ±15 |
| **VIX_deviation** | extreme_fear(-30) 또는 overheated(+15) | `int(-(vix - 20))` | ±10 |
| **FNG_deviation** | (위와 OR 조건) | `int((fng - 50) / 5)` | ±10 |
| **Regime_deviation** | bull -15/+10, bear 0 | `int(-(regime_score - 50) / 3)` | ±10 |

### 의미
- 모든 컴포넌트가 base 50 기준 ±N 비율로 가산
- 단방향 최대 누적: `-25-15-15-15-10-10-10 = -100점` → `score=1`까지 도달 가능
- 한 컴포넌트의 한방으로 매수 트리거되지 않음 — **여러 신호 동시 양호 시에만 매수**

### 그대로 유지
- `_score_portfolio` (forced_sell @ -8% 손절은 binary, take_profit/add_position은 보유 종목 한정)
- `_score_bonuses` (top10 -10, sector ±10 — 카테고리/임계 기반)
- 하드 게이트 (RSI ≥ 75 차단, 현금 부족 차단)

## 백테스트 결과 (60일, KR 전체)

### Threshold별 (`STRATEGY_BUY_THRESHOLD_MAX`)

| threshold | pairs | blocked | win% | total | Δ vs orig |
|-----------|-------|---------|------|------|-----------|
| 25 | 240 | 691 | 50.8% | +179,670 | +1,965,960 |
| **30** ⭐ | **273** | **656** | **51.6%** | **+190,095** | **+1,976,385** |
| 35 | 334 | 593 | 51.2% | +31,045 | +1,817,335 |
| 40 | 398 | 534 | 50.8% | +19,195 | +1,805,485 |
| 45 | 447 | 484 | 49.9% | -10,255 | +1,776,035 |
| 50 | 680 | 239 | 49.6% | -776,690 | +1,009,600 |

**최적 threshold = 30 (현행 유지)**, 통합 비율 시스템만으로 60일간 **+1.97M원 개선**.

### 손실일 방어 효과

| 일자 | orig | new | 개선 |
|-----|------|-----|------|
| 03-23 | -1,200,500 | -139,500 | **+1,061,000** |
| 04-27 | -484,375 | +13,225 | +497,600 |
| 04-21 | -168,450 | +13,800 | +182,250 |
| 04-16 | -306,450 | -221,200 | +85,250 |

수익일은 약간 덜 먹지만(보수적), **손실일 방어로 순효과 +1.97M**.

### 새 점수 분포

```
[  0~  9]: 105건  강매수
[ 10~ 19]:  76건
[ 20~ 29]:  84건
[ 30~ 39]: 121건  중립 시작
[ 40~ 49]: 294건  ← 최다, "신중" 영역
[ 50~ 59]:  66건
[ 60~ 69]:  39건
[ 70~ 79]:  43건  매도 시작
[ 80~ 89]:  89건
[ 90~]    :   6건
```

옛 시스템에서는 30 이하에 매수 트리거가 폭주했으나, 새 시스템은 40-49 중립 영역에 가장 많이 분포 → **무거래 비중 증가, 매수가 정말 강한 신호일 때만 발화**.

## 코드 반영 현황

### ✅ 적용 완료 — `services/strategy/signal_service.py`

1. `_score_rsi()` — 단계 4구간 → `clamp(int(rsi - 50), -15, +15)` 선형
2. `_score_dcf()` — 6단계 step → `clamp(int(-undervalue_pct), -25, +25)` 선형
3. `_score_target_prices()` — target_buy/target_sell binary → `clamp(int(ema200_dev_pct), -15, +15)`
4. `_score_technical()` — `EMA200_support` 블록 제거, `change_rate`도 비율화 `clamp(int(change_rate*3), -15, +15)`
5. `_score_market_context()` — `extreme_fear_buy_opportunity(-30)` / `bull_market_advantage(-15)` step → VIX/FNG/Regime 각각 비율 ±10

### Reason 문자열 변화

| 옛 reason | 새 reason 예시 |
|----------|-------------|
| `RSI_oversold(37.2,-6)` | `RSI_deviation(37.2,-13)` |
| `DCF_high_undervalue(36.3%)` | `DCF_deviation(+36.3%,-25)` |
| `target_entry_price_hit($478046.47)` | `EMA200_deviation(-3.7%,-3)` |
| `EMA200_support` | (제거됨, EMA200_deviation에 통합) |
| `extreme_fear_buy_opportunity` | `VIX_deviation(26.8,-6)` + `FNG_deviation(15,-7)` |
| `bull_market_advantage` | `Regime_deviation(75,-8)` |
| `bear_market_hold` | (제거됨, Regime_deviation으로 표현) |
| `sharp_drop(-5.6%)` | `change_deviation(-5.6%,-15)` |

### ⚠️ 미정리 — execution_service_v2.py:WEIGHTS dict

옛 step 가중치 상수들은 코드에서 참조 안 됨:
- `RSI_OVERSOLD/OVERBOUGHT`
- `DIP_BUY_5PCT/SURGE_SELL_5PCT`
- `SUPPORT_EMA/RESISTANCE_EMA`
- `PANIC_MARKET_BUY/PROFIT_TAKE_TARGET` (PROFIT_TAKE_TARGET은 portfolio에서 여전히 사용)
- `BULL_MARKET_SECTOR`
- `DCF_UNDERVALUE_*`, `DCF_OVERVALUE_*`, `DCF_FAIR_VALUE`, `DCF_UNAVAILABLE` (`DCF_UNAVAILABLE`은 no_dcf 케이스에 여전히 사용)

→ 일부만 사용되므로 무리한 정리는 보류. 추후 cleanup PR.

## 코드 반영 계획 (남은 작업)

### Phase 1 — 검증 (즉시)

- [ ] **서버 재기동** (`uvicorn` 프로세스): 변경된 `signal_service.py` 로드
- [ ] **dashboard 검증**: 삼성화재(000810), 삼성SDI(006400), 삼성SDS(018260), 현대글로비스(086280) score breakdown이 새 reason 형식으로 출력되는지 확인
- [ ] **API 테스트**: `GET /api/analysis/score/{ticker}` 새 reasons 정상 출력
- [ ] **하루 운영 후 trade_history 점검**: 새 reason 형식이 DB에 정확히 기록되는지

### Phase 2 — 클린업 (1주 내)

- [ ] `WEIGHTS` dict에서 미사용 상수 제거 (PROFIT_TAKE_TARGET, ADD_POSITION_LOSS, DCF_UNAVAILABLE만 유지)
- [ ] `models/ticker_state.py`의 `target_buy_price` / `target_sell_price` 필드 삭제 (대시보드 표시 외 미사용 — 표시도 EMA200으로 대체 가능)
- [ ] `services/market/market_data_service.py:79-80, 140-141` target 가격 자동 계산 코드 제거
- [ ] 백테스트 분석 스크립트 → `scripts/`에 정식 명령으로 (`scripts/backtest_full_rescore.py` 정리)
- [ ] CLAUDE.md / `.claude/BUSINESS_LOGIC.md` 점수 계산 섹션 갱신

### Phase 3 — 후속 개선 (백테스트 후 결정)

- [ ] **Trailing stop 완화 시뮬**: NEUTRAL/BEAR -5% → -7%
- [ ] **장 초반 30분 trailing 비활성화**: 익일 갭다운 보호
- [ ] **RSI ≥ 60 매수 차단** (현재 RSI_BUY_BLOCK 75 → 60 강화)
- [ ] **분할 매도 후속 트랜치 가격 검증**: 매수가 이하 떨어지면 중단
- [ ] **portfolio 컴포넌트도 비율화 검토**: profit_pct → 비율 계속/익절도 선형 (forced_sell만 binary 유지)

### Phase 4 — 모니터링

- [ ] **2주간 운영 후 재백테스트**: 새 로직 실거래 데이터로 검증
- [ ] **점수 분포 모니터링**: 30 이하 / 40-49 / 70 이상 비중이 백테스트와 일치하는지

## Open Questions

1. `target_buy_price` / `target_sell_price` 필드 — 대시보드에서 사용자에게 보여줄 가치 있는가? 없으면 완전 제거.
2. 새 점수 분포에서 30-39 중립 영역(121건)이 홀드되는데, asset_management 매수 후보 풀에서 score 30~40도 포함할지?
3. `STRATEGY_BUY_THRESHOLD_MAX = 30` 고정 vs 레짐별 차등 (Bull 35, Bear 25 등)?

## 관련 파일

- `services/strategy/signal_service.py` (점수 계산 본체) ★ 수정됨
- `services/strategy/execution_service_v2.py:WEIGHTS` (옛 상수 일부 잔존)
- `services/market/market_data_service.py:79-80, 140-141` (target_buy/sell 자동 계산 — 제거 대상)
- `models/ticker_state.py` (target_buy/sell 필드)
- `scripts/backtest_history.py` (전체 분석)
- `scripts/backtest_rescored.py` (옛/새 비교 — score N만)
- `scripts/backtest_full_rescore.py` (옛/새 비교 — 전체, 임계값 매트릭스) ★

## 관련 메모리 업데이트 필요

- `MEMORY.md`의 "Regime 점수 시스템" 섹션 — 시그널 컴포넌트가 이제 비율 기반임을 추가
