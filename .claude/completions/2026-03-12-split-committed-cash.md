# Completion: 분할 매수 committed cash 제거 + 신규 종목 우선순위

**Date**: 2026-03-12
**Task**: committed cash 예약 제거, 신규 종목 신호 우선 실행

---

## 변경 파일

| 파일 | 변경 내용 |
|------|---------|
| `services/strategy/signal_service.py` | `_collect_trading_signals`: `kr_committed`/`us_committed` 계산 제거, `available` 계산에서 committed 차감 제거 |
| `services/strategy/position_service.py` | `_handle_add_buy_signal`: committed 3줄 제거, `cash_balance` 직접 전달 |
| `services/strategy/position_service.py` | `_init_split_order`: committed 계산/차감 제거, `cash_balance`·`usd_cash_krw` 원본 사용 |
| `services/strategy/position_service.py` | `_execute_collected_signals`: `for` 루프 직전 `_priority` 정렬 추가 |

---

## 변경 배경

분할 매수(3트랜치)는 `add_buy_cooldown`으로 하루 1트랜치씩 실행됨.
기존 코드는 미집행 트랜치 금액(`remaining_qty × entry_price`)을 즉시 committed cash로 예약
→ 내일/모레 쓸 돈이 오늘 신규 종목 매수를 차단하는 비효율.

---

## 핵심 동작 변경

### committed cash 제거
- `_calculate_committed_cash` 호출 전면 제거
- 현금 부족 시 split tranche는 `qty=0`으로 자연 실패 → 다음날 재시도

### 신호 우선순위 정렬
```
0 (최우선): 신규 종목 (holding=None, split_orders 미등록)
1          : 기존 보유 (익절/손절/추매)
2 (후순위) : split tranche 연속 (split_orders 등록)
```

루프 내 `cash_balance` 갱신 로직(`sig_spent_krw` 차감)이 이미 있어
신규 종목 처리 후 남은 현금으로 split tranche를 처리하는 흐름이 자연스럽게 동작.
