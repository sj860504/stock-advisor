# AssetManagementService 쿨다운 누락 수정 (2026-03-17)

> AssetManagementService(자산관리 2단계)에서 매수/매도 쿨다운을 체크하지 않아,
> 1단계(신호 실행)에서 매수/매도한 종목이 같은 루프에서 다시 매수/매도될 수 있던 버그 수정.

---

## 1. 발견된 버그

| 항목 | 내용 |
|------|------|
| 증상 | 동일 종목이 같은 루프에서 1단계(신호 실행) + 2단계(자산관리)에서 중복 매수/매도 |
| 원인 | `AssetManagementService.run()` → `execute_buy_budget()` / `execute_sell_for_cash()`가 `user_state`의 `add_buy_cooldown` / `sell_cooldown`을 전혀 참조하지 않음 |
| 영향 | 매수: 같은 종목 당일 2회 매수. 매도: 익절 후 즉시 자산관리에서 재매도 시도 |

---

## 2. 수정 내용

### 2-1. user_state 전달 경로 추가

```
TradingStrategyService.run_strategy()
  → AssetManagementService.run(user_state=user_state)        ← 추가
    → _rebalance_market(user_state=user_state)               ← 추가
      → PositionService.execute_buy_budget(user_state=...)   ← 추가
      → PositionService.execute_sell_for_cash(user_state=...)← 추가
```

### 2-2. 변경 파일

| 파일 | 변경 |
|------|------|
| `services/strategy/trading_strategy_service.py` | `AssetManagementService.run()`에 `user_state` 전달 |
| `services/strategy/asset_management_service.py` | `run()`, `_rebalance_market()`에 `user_state` 파라미터 추가 및 하위 전달 |
| `services/strategy/position_service.py` | `execute_buy_budget()`에 `add_buy_cooldown` 체크 + 성공 시 쿨다운 설정 |
| `services/strategy/position_service.py` | `execute_sell_for_cash()`에 `sell_cooldown` 체크 + 성공 시 쿨다운 설정 |

### 2-3. 동작 변화

```
[Before]
1단계: AAPL 매수 → add_buy_cooldown[AAPL] = today
2단계: AAPL 다시 매수 시도 → 쿨다운 체크 없음 → 중복 매수 ❌

[After]
1단계: AAPL 매수 → add_buy_cooldown[AAPL] = today
2단계: AAPL 매수 시도 → "쿨다운 활성 → 스킵" ✅

1단계: 005930 익절 매도 → sell_cooldown[005930] = today
2단계: 005930 매도 시도 → "매도 쿨다운 활성 → 스킵" ✅
```

---

**Last Updated**: 2026-03-17
