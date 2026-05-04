# 2026-05-04 — 미국 현금 90% 해소 구현 계획

> 분석 보고서: `.claude/completions/2026-05-04-us-cash-90pct-investigation.md`
> 등록된 작업: Task #17~#22

## 목표

US 현금 비중 90% → target(NEUTRAL 40%) 근처로 회복.
잉여 현금 ~$50,000을 score 기반 매수에 빠르게 투입.

## 우선순위별 작업

### Phase 0 — 즉시 임시 조치 (코드 수정 없음, settings API)

**Task #22**: 운영 환경에서 settings API로 변경

```bash
# 1) per-trade-ratio 0.05 → 0.10 (회당 5% → 10%)
curl -X PUT http://server:8000/api/trading/settings \
     -H "Content-Type: application/json" \
     -d '{"key":"STRATEGY_PER_TRADE_RATIO","value":"0.10"}'

# 2) buy threshold 40 → 50 (매수 후보 풀 확대)
curl -X PUT http://server:8000/api/trading/settings \
     -H "Content-Type: application/json" \
     -d '{"key":"STRATEGY_BUY_THRESHOLD","value":"50"}'
```

**예상 효과**: 매수 빈도 2배 증가, score ≤ 50 종목까지 매수. 1주일 내 60% 정도까지 회복 예상.
**검증**: 매일 dashboard에서 US 현금 비중 추이 모니터링.

---

### Phase 1 — 핵심 코드 수정 (3~5일)

#### Task #17 — AssetMgmt cash-gap-aware buy threshold

**파일**: `services/strategy/asset_management_service.py:60` (`_rebalance_market`)

**변경 안**:
```python
def _rebalance_market(cls, user_id, market, cash, stock_total, target_ratio, holdings, user_state):
    gap = cls._calc_cash_gap(cash, stock_total, target_ratio)
    
    if gap > 0:
        signals = SignalService.get_latest_signals()
        market_signals = [s for s in signals if (is_kr(s.ticker) if market=="KR" else not is_kr(s.ticker))]
        
        # NEW: gap 크기에 따른 동적 BUY 임계 완화
        cash_ratio = cash / (cash + stock_total) if (cash + stock_total) > 0 else 0
        gap_pct = max(0, (cash_ratio - target_ratio) * 100)
        base_threshold = SettingsService.get_int("STRATEGY_BUY_THRESHOLD", 40)
        relax_step = SettingsService.get_int("STRATEGY_GAP_RELAX_STEP", 5)
        relax_max = SettingsService.get_int("STRATEGY_GAP_RELAX_MAX", 20)
        relaxed_threshold = base_threshold + min(relax_max, int(gap_pct / 5) * relax_step)
        
        eligible = [s for s in market_signals if s.score <= relaxed_threshold]
        logger.info(f"[AssetMgmt] {market} gap={gap_pct:.1f}%pa, threshold {base_threshold}→{relaxed_threshold}, eligible={len(eligible)}")
        
        budget_krw = gap if market == "KR" else 0.0
        budget_usd = 0.0 if market == "KR" else gap
        PositionService.execute_buy_budget(
            user_id, budget_krw=budget_krw, budget_usd=budget_usd,
            signals=eligible,  # ← 필터된 후보만
            user_state=user_state,
        )
    elif gap < 0:
        ...  # 기존 매도 로직
```

**예상 효과**: gap 50%pa → threshold 40+20=60 → 후보 ~80개 → 매수 적극화
**리스크**: 너무 관대하면 약신호 종목 잡거래. 캡 +20으로 제한해서 score 60 이상은 매수 안 함.

#### Task #18 — Cooldown gap-aware

**파일**: `services/strategy/position_service.py:117` (`_is_buy_cooldown_active`) + `:796` (cooldown 설정 시점)

**변경 안**:
```python
@classmethod
def _is_buy_cooldown_active(cls, ticker, today, current_price, add_buy_cooldown, gap_pct=0):
    cd = add_buy_cooldown.get(ticker)
    if not cd: return False
    
    # NEW: gap 크면 cooldown 1시간만
    high_gap_threshold = SettingsService.get_int("STRATEGY_COOLDOWN_HIGH_GAP_PCT", 30)
    if gap_pct >= high_gap_threshold:
        # 시간 단위 cooldown 체크 (cd.timestamp 필요)
        cooldown_hours = SettingsService.get_int("STRATEGY_BUY_COOLDOWN_HOURS_HIGH_GAP", 1)
    else:
        cooldown_hours = SettingsService.get_int("STRATEGY_BUY_COOLDOWN_HOURS", 24)
    
    elapsed = (datetime.now() - cd.created_at).total_seconds() / 3600
    if elapsed >= cooldown_hours:
        return False
    
    # 기존 -5% 예외
    if cd.price > 0 and current_price <= cd.price * 0.95:
        return False
    return True
```

**필요 변경**: `BuyCooldownEntry`에 `created_at` 필드 추가 (현재는 `date` 만 있음).

**리스크**: 시간 단위 추적은 더 세밀한 영속 필요. DB 마이그레이션 또는 JSON 컬럼 확장.

#### Task #19 — Per-trade ratio dynamic multiplier

**파일**: `services/strategy/execution_service_v2.py:359` (`_calculate_buy_quantity`)

**변경 안**:
```python
@classmethod
def _calculate_buy_quantity(cls, score, cash_balance, current_price, exchange_rate, is_kr_flag, market_total_krw=0.0, usd_cash_krw=0.0, gap_pct=0.0):
    per_trade_ratio = SettingsService.get_float("STRATEGY_PER_TRADE_RATIO", 0.05)
    
    base_assets = market_total_krw
    score_multiplier = 2.0 if score >= 90 else (1.5 if score >= 80 else 1.0)
    
    # NEW: gap 비례 multiplier (cap 3배)
    gap_multiplier = 1 + min(2, int(gap_pct / 25))
    
    target_invest_krw = base_assets * per_trade_ratio * score_multiplier * gap_multiplier
    cash_limit = (usd_cash_krw if not is_kr_flag and usd_cash_krw > 0 else cash_balance)
    actual_invest_krw = min(target_invest_krw, cash_limit)
    ...
```

**호출 체인 변경**: `_execute_buy_order` → `_calculate_buy_quantity`에 `gap_pct` 전달 필요.
gap_pct 계산을 `_compute_buy_market_totals` 또는 `_check_buy_cash_and_entry_conditions`에서 함께.

#### Task #20 — Market hour guards 통합

**파일**:
- `services/strategy/execution_service_v2.py:313` (`_check_market_hours` 제거)
- `services/strategy/execution_service_v2.py:653` (호출부 변경)

**변경 안**:
```python
# _check_market_hours 메서드 삭제

# _execute_trade_v2 진입부
market = "KR" if is_kr(ticker) else "US"
if MarketHourService.is_weekend() or not MarketHourService.is_trading_active(market):
    logger.info(f"⏭️ {ticker} {market} not active. Order skipped.")
    return TradeResult.no_op()
```

`_execute_buy_order`/`_execute_sell_order` 진입부의 가드는 **중복이지만 안전망**으로 유지 (외부에서 직접 호출되는 경우 대비).

---

### Phase 2 — 검증 (1~2일)

#### Task #21 — 백테스트

새 로직을 60일 데이터에 적용:
```python
# scripts/backtest_gap_aware.py
- 기존 (5%/40 고정) vs 새 (gap_pct 기반 동적)
- 매수 빈도 비교
- 현금 비중 추이 곡선 (일별)
- 누적 P&L 비교
- 약신호 매수 비율 vs 강신호
```

**검증 지표**:
- 현금 90% → 50% 도달 일수 (기존 vs 새)
- 평균 매수 score (새가 약간 높을 것)
- 누적 수익률
- MDD

---

### Phase 3 — 신규 DB Settings (Phase 1과 함께)

```sql
INSERT INTO settings (key, value, description) VALUES
  ('STRATEGY_GAP_RELAX_STEP', '5', '갭 5%pa당 BUY threshold +5'),
  ('STRATEGY_GAP_RELAX_MAX', '20', 'BUY threshold 최대 완화'),
  ('STRATEGY_COOLDOWN_HIGH_GAP_PCT', '30', '쿨다운 단축 임계 (gap %pa)'),
  ('STRATEGY_BUY_COOLDOWN_HOURS', '24', '기본 매수 쿨다운 (시간)'),
  ('STRATEGY_BUY_COOLDOWN_HOURS_HIGH_GAP', '1', '높은 갭 시 매수 쿨다운 (시간)');
```

`SettingsService.DEFAULT_SETTINGS`에 동일 5개 추가.

---

## 진행 순서 권장

1. **Phase 0** 즉시 적용 (settings API 명령 2개) — 위험도 낮음
2. **Phase 2 백테스트 먼저** (Task #21) — 새 로직 효과 검증
3. **Phase 1 코드 수정** 백테스트 결과 확인 후
   - Task #17 (gap-aware threshold) — 단순, 안전
   - Task #19 (per-trade multiplier) — 단순
   - Task #20 (가드 통합) — 리스크 낮음
   - Task #18 (cooldown 시간 단위) — 가장 복잡, BuyCooldownEntry 모델 변경
4. **Phase 3** Settings 키는 Phase 1과 동시에

## 성공 기준

- 적용 후 1주 내 US 현금 비중 90% → 50% 미만
- 누적 수익률 기존 대비 같거나 우월
- 매수 빈도 2~3배 증가하되 score 60 초과는 ≤ 10%
