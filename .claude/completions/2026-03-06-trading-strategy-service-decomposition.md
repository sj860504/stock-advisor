# 완료: trading_strategy_service.py 모듈 분해

**날짜**: 2026-03-06
**작업**: 1,880줄 모놀리식 클래스 → 5개 파일 분리

---

## 결과

### 생성된 파일
| 파일 | 줄수 | 클래스 | 메서드 수 |
|------|------|--------|----------|
| `execution_service_v2.py` | 592 | `TradeExecutorService` | 28 |
| `signal_service.py` | 348 | `SignalService` | 16 |
| `position_service.py` | 341 | `PositionService` | 9 |
| `sector_rebalancer_service.py` | 277 | `SectorRebalancerService` | 12 |
| `trading_strategy_service.py` (리팩토링) | 548 | `TradingStrategyService` | 33 |

### 검증
- ✅ 모든 파일 신택스 검증 (`ast.parse`)
- ✅ TradingStrategyService 공개 API 13개 모두 유지
- ❌ 통합 임포트 테스트: `kis_fetcher.py`의 **기존 버그** (`Optional` 미임포트)로 실패 (우리 코드 문제 아님)

---

## 임포트 계층 (순환 없음)
```
[외부 서비스] → execution_service_v2 (TradeExecutorService)
                     ↑ imports
             signal_service (SignalService)
             position_service (PositionService)
             sector_rebalancer_service (SignalService도 임포트)
                     ↑ imports
             trading_strategy_service (TradingStrategyService) - 오케스트레이터
```

---

## 주요 설계 결정

1. **TradeContext 변환 보류**: `_execute_trade_v2` 시그니처 유지 (9곳 호출 부담 → 별도 작업)
2. **WEIGHTS/SECTOR_* 상수**: `TradeExecutorService`에 통합 (signal_service가 `TradeExecutorService.WEIGHTS` 참조)
3. **`get_top_weight_overrides`**: `TradeExecutorService`로 이동 (signal과 sector 양쪽서 필요, circular 방지)
4. **`_calculate_total_assets`**: `TradeExecutorService`로 이동 (sector_rebalancer도 필요)
5. **섹터 그룹 비중 계산 전체**: `TradeExecutorService`로 이동 (`_get_sector_group_weights` 등)

---

## 외부 호출자 영향
- 없음. `TradingStrategyService` 공개 API 동일 유지 (delegation wrapper 패턴)
