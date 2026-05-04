# 2026-05-04 — 미국 현금 90% 누적 원인 분석 및 수정 계획

## 현상

- US USD 현금: $90,819
- US 보유 가치: $9,301 (PEP $4,879 / REGN $2,805 / WFC $1,616)
- US 현금 비중: **90.7%** (목표 40% NEUTRAL 대비 50%pa 초과)
- 5/1~5/2 동안 KO 64주 매도(-$5046) 후 그 현금이 다시 매수로 안 들어감
- 점수 분포: score ≤ 30 강매수 후보 21개 / score 31-40 매수 후보 12개 — **후보 자체는 풍부**
- 실제 매수: 5/2 02:20 KST에 PEP 1건 (score 44, HOLD 영역)

## Root Cause 분석

### Issue #1 — AssetMgmt 매수 후보 무차별 순회 ⚠️
**위치**: `services/strategy/position_service.py:773`
```python
sorted_signals = sorted(signals, key=lambda s: s.score)
for sig in sorted_signals:  # score 필터 없음
```
- score 필터 미적용 → score 60+ 약신호까지 매수 시도
- 실제로 PEP score 44에 매수됨 (BUY_THRESHOLD 40 이상)
- 결과: 자금이 약신호에 분산되어 강신호 도달 전 cooldown 락

### Issue #2 — `add_buy_cooldown` 24h 락이 budget gap 해소 막음 ⚠️⚠️
**위치**: `services/strategy/position_service.py:796`
```python
if result.executed:
    add_buy_cooldown[ticker] = BuyCooldownEntry(date=today, price=current_price)
```
- 매수 1번 성공 시 동일 티커 24h 차단 (price -5% 예외 외)
- gap $50k인데 1회 $5k 매수 후 종목당 cooldown → **종목 다양성 부족 시 budget 미소진**
- AssetMgmt는 gap 크기와 무관하게 종목별 1회 룰 따름

### Issue #3 — STRATEGY_PER_TRADE_RATIO 5% 하드코딩 ⚠️
**위치**: `services/strategy/execution_service_v2.py:359`
```python
target_invest_krw = base_assets * per_trade_ratio * multiplier  # 5%
```
- gap 50%pa인데 1회 5%만 투자 → 10회 매수 필요
- cooldown 24h × 10회 = 10일 걸림
- 시장 변동성 고려하면 비효율적

### Issue #4 — 시장 시간 가드 이중 적용 불일치 ⚠️
**위치**:
- `services/strategy/execution_service_v2.py:653` (`_check_market_hours` → `is_us_market_open`)
- `services/strategy/execution_service_v2.py:566, 600` (`is_us_trading_active`)

| 함수 | 윈도우 | 출처 |
|------|------|------|
| `is_us_market_open(extended=True)` | 04:00~20:00 ET | 옛 |
| `is_us_trading_active` | 04:00~16:30 ET | 2026-04-30 추가 |

- 16:00~16:30 ET 30분에서 두 가드 결과 다를 수 있음
- 또한 가드 함수 호출이 중복 (성능 + 가독성)

### Issue #5 (정상 동작 확인) — `_is_cash_below_target`
- cash 90% > target 40% → 차단 X (정상)
- 본 이슈의 원인 아님

## 수정 계획

### 우선순위 1 — AssetMgmt cash-gap-aware 매수 (즉시)

**`AssetManagementService._rebalance_market`**:
```python
gap = cls._calc_cash_gap(cash, stock_total, target_ratio)
if gap > 0:
    signals = SignalService.get_latest_signals()
    
    # NEW: gap 크기에 따라 score 임계 동적 완화
    base_threshold = SettingsService.get_int("STRATEGY_BUY_THRESHOLD", 40)
    cash_ratio = cash / (cash + stock_total) if (cash+stock_total) > 0 else 0
    target = target_ratio
    gap_pct = (cash_ratio - target) * 100
    relaxed_threshold = base_threshold + min(20, int(gap_pct / 5) * 5)
    # 예: gap 50%pa → threshold 40 + 20 = 60
    
    eligible = [s for s in signals if s.score <= relaxed_threshold]
    
    PositionService.execute_buy_budget(...)
```

**`PositionService.execute_buy_budget`**: signals 인자에 이미 필터된 candidates 전달.

### 우선순위 2 — Cooldown 갭 기반 완화 (1주 내)

gap이 크면 cooldown 1일 → 1시간으로 단축 OR per-trade-ratio 동적 상향:

```python
# execute_buy_budget 내
if gap_pct > 30:  # 갭 30%pa 이상이면
    cooldown_minutes = 60  # 1시간만 cooldown
else:
    cooldown_minutes = 24*60  # 1일 (기존)
```

또는 PER_TRADE_RATIO 갭 기반 multiplier:
```python
gap_multiplier = 1 + min(2, int(gap_pct / 25))  # 갭 50% → 3배 = 15%
target_invest_krw = base_assets * per_trade_ratio * gap_multiplier
```

### 우선순위 3 — 시장 시간 가드 통합 (1주 내)

`_check_market_hours` 제거 → `is_kr/us_trading_active`로 통일.

```python
# execution_service_v2.py:653
if not cls._check_market_hours(ticker):  # 옛
    
# →
market = "KR" if is_kr(ticker) else "US"
if not MarketHourService.is_trading_active(market):  # 신
```

### 우선순위 4 — Settings DB 추가

```
STRATEGY_GAP_AWARE_BUY=1                  # 갭 기반 매수 활성화
STRATEGY_GAP_THRESHOLD_RELAX_STEP=5       # 갭 5%pa당 임계 +5
STRATEGY_GAP_THRESHOLD_MAX_RELAX=20       # 최대 +20 (40 → 60)
STRATEGY_BUY_COOLDOWN_HOURS=24            # 기본 쿨다운
STRATEGY_BUY_COOLDOWN_HOURS_HIGH_GAP=1    # 갭 30%pa+ 시 쿨다운
```

### 백테스트

새 로직을 60일 데이터에 적용해 매수 빈도/현금 비중 변화 검증:
- 기존 vs 새 매수 비중 (5% per trade vs gap-multiplier)
- 현금 비중 추이 곡선
- 누적 P&L 비교

## 즉시 임시 조치 (코드 수정 전)

1. **STRATEGY_PER_TRADE_RATIO 0.05 → 0.10** (settings API):
```bash
curl -X PUT http://server/api/trading/settings -H "Content-Type: application/json" \
     -d '{"key":"STRATEGY_PER_TRADE_RATIO","value":"0.10"}'
```

2. **STRATEGY_BUY_THRESHOLD 40 → 50** (settings API): 매수 후보 풀 확대
```bash
curl -X PUT http://server/api/trading/settings -H "Content-Type: application/json" \
     -d '{"key":"STRATEGY_BUY_THRESHOLD","value":"50"}'
```

3. **수동 매수 트리거**: dashboard에서 강매수 후보 (NOW, BKNG, IBM 등) 직접 매수
