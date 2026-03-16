# 미체결 주문 확인 시스템 구현 (2026-03-16)

> 주문 접수 후 체결되지 않은 미체결 주문을 추적하여, 동일 종목에 대한 중복 주문을 방지하는 시스템.
> 발단: 458030(WON 국공채머니마켓액티브) 매도 주문 5건이 미체결 상태로 매 1분마다 반복 시도되던 버그.

---

## 1. 발견된 버그 (3건) 및 수정

### 버그 1: DB 스키마 누락

| 항목 | 내용 |
|------|------|
| 증상 | `StrategyStateRepo.load(sean)` 시 `sqlite3.OperationalError: no such column: strategy_state.sell_split_orders` |
| 원인 | ORM 모델에 `sell_split_orders`, `trailing_high` 컬럼 정의되어 있으나 DB에 마이그레이션 미적용 |
| 수정 | `ALTER TABLE strategy_state ADD COLUMN sell_split_orders/trailing_high` 실행 |

### 버그 2: `_handle_trailing_stop()` macro_data 중복 전달

| 항목 | 내용 |
|------|------|
| 증상 | `PositionService._handle_trailing_stop() got multiple values for argument 'macro_data'` |
| 원인 | `position_service.py` line 396에서 `macro_data`를 positional arg + `**common_kwargs` 이중 전달 |
| 수정 | 호출부에서 positional `macro_data` 제거, 함수 시그니처에서 keyword arg로 이동 |
| 파일 | `services/strategy/position_service.py` |

### 버그 3: 매도 시 TradeResult.spent_krw/spent_usd 미반영

| 항목 | 내용 |
|------|------|
| 증상 | `execute_sell_for_cash()`에서 `need_krw -= result.spent_krw`가 항상 0 차감 → 수익 종목 전부 매도 시도 |
| 원인 | `_execute_trade_v2()`의 sell 분기에서 `TradeResult(executed=executed)`만 반환, 매도 금액 미기록 |
| 수정 | 매도 성공 시 `trade_qty * current_price`를 KR/US 구분하여 `spent_krw`/`spent_usd`에 반영 |
| 파일 | `services/strategy/execution_service_v2.py` |

> **위 3개 버그로 인해 전략 실행이 매 루프마다 예외 발생 → `AssetManagementService.run()`까지 도달하지 못하고 있었음.**

---

## 2. 미체결 주문 확인 시스템

### 2-1. 추가된 모델 (`models/schemas.py`)

```python
class UnfilledOrder(BaseModel):
    """KIS 미체결 주문 1건."""
    ticker: str
    order_type: str       # 'buy' or 'sell'
    order_qty: int
    filled_qty: int
    remaining_qty: int
    order_price: float
    order_date: str
    order_time: str

class UnfilledOrdersResult(BaseModel):
    """KIS 미체결 조회 결과."""
    orders: List[UnfilledOrder]
    error: Optional[str]
    has_pending(ticker, order_type) -> bool
    pending_qty(ticker, order_type) -> int

class OrderVerificationResult(BaseModel):
    """주문 체결 확인 결과."""
    ticker: str
    order_type: str
    is_filled: bool
    remaining_qty: int
    message: str          # 사용자 표시용
```

### 2-2. DB 변경

| 테이블 | 변경 |
|--------|------|
| `trade_history` | `status VARCHAR(20) NOT NULL DEFAULT 'filled'` 컬럼 추가 |
| `api_tr_meta` | `국내주식_미체결조회`, `해외주식_미체결조회` 항목 추가 |
| `api_tr_meta` | `국내주식_체결조회` TR ID 구→신 업데이트 (`TTTC8001R` → `TTTC0081R`) |

### 2-3. 변경 파일 및 역할

| 파일 | 변경 내용 |
|------|-----------|
| `models/trade_history.py` | `status` 컬럼 추가 (`pending` / `filled`) |
| `models/schemas.py` | `UnfilledOrder`, `UnfilledOrdersResult`, `OrderVerificationResult` 모델 추가 |
| `repositories/trade_history_repo.py` | `get_pending_orders(ticker)`, `update_status(id, status)`, `mark_filled(id)` 추가. `record()`에 `status` 파라미터 추가 |
| `services/kis/kis_service.py` | `get_unfilled_orders_kr()`, `get_unfilled_orders_us()`, `_fetch_unfilled_orders()` 추가 |
| `services/trading/order_service.py` | `verify_and_update_pending_orders()`, `has_pending_order(ticker, order_type)` 추가. `record_trade()`의 기본 status를 `pending`으로 변경 |
| `services/strategy/execution_service_v2.py` | `_has_pending_order()` 추가, `_place_and_record()`에 미체결 체크 게이트 추가 |
| `services/strategy/trading_strategy_service.py` | `_verify_pending_orders()` 추가, `run_strategy()`에서 루프 시작 시 호출 |
| `services/market/stock_meta_service.py` | `init_api_tr_meta()`에 미체결조회 TR ID 2건 추가 |

### 2-4. 동작 흐름

```
run_strategy() 시작
  │
  ├─ _verify_pending_orders()
  │    ├─ DB에서 status='pending' 주문 조회
  │    ├─ pending 있을 때만 KIS 미체결 API 호출 (KR/US 분리)
  │    ├─ KIS 미체결 목록에 없음 → status='filled' 업데이트
  │    └─ KIS 미체결 목록에 있음 → pending 유지, 로그 출력
  │
  ├─ 신호 수집 + 매매 실행
  │    └─ _place_and_record(ticker, side, ...)
  │         ├─ _has_pending_order(ticker, side) → True면 스킵 + 로그
  │         ├─ KIS 주문 전송
  │         └─ 성공 → status='pending'으로 DB 기록
  │
  └─ AssetManagementService.run()
       └─ 동일하게 _place_and_record() 경유 → 미체결 체크 적용
```

### 2-5. KIS API 연동

| 시장 | API명 | TR ID (실전/모의) | 핵심 파라미터 |
|------|-------|------------------|--------------|
| KR | 국내주식_미체결조회 | TTTC0081R / VTTC0081R | `CCLD_DVSN=02` (미체결만) |
| US | 해외주식_미체결조회 | JTTT3001R / VTTT3001R | `CCLD_NCCS_DVSN=01` (미체결만) |

응답에서 사용하는 필드:

| 필드 | 설명 | KR | US |
|------|------|----|----|
| 종목코드 | ticker | `pdno` | `ovrs_pdno` |
| 매도/매수 | order_type | `sll_buy_dvsn_cd` (01=매도, 02=매수) | 동일 |
| 주문수량 | order_qty | `ord_qty` | `ft_ord_qty` |
| 체결수량 | filled_qty | `tot_ccld_qty` | `ft_ccld_qty` |
| 잔여수량 | remaining_qty | `rmn_qty` | `nccs_qty` |

---

## 3. 검증 결과

```
# KIS 미체결 조회 테스트
📋 [KR] 미체결 조회: 5건
  458030 sell qty=7 remaining=7  × 5건 (총 35주 미체결)

# _has_pending_order 체크
458030 sell pending: True   ← 스킵 대상
005930 buy pending: False   ← 정상 주문 가능

# verify_and_update_pending_orders
458030 sell: filled=False, remaining=35, msg=458030 미체결 35주 대기 중
```

---

## 4. 추가 수정사항

### 4-1. 매도 spent_krw/spent_usd 반영 (`execution_service_v2.py`)
- `_execute_trade_v2()`의 sell 분기에서 `TradeResult`에 매도 금액 미반영 → 수정
- `AssetManagementService.execute_sell_for_cash()`에서 `need_krw` 차감이 동작하지 않던 버그 해결

### 4-2. 포트폴리오 리포트 시장 필터링 (`report_service.py`)
- `format_portfolio_report()`에 `show_kr`/`show_us` 파라미터 추가
- 미개장 시장 섹션을 리포트에서 제외
- 호출부 3곳 수정: `main.py`, `scheduler_service.py` (hourly, close)

### 4-3. KIS 동기화 엔드포인트 신규 (`routers/portfolio.py`)
- `POST /portfolio/{user_id}/sync` 추가 — `PortfolioService.sync_with_kis()` 호출
- 기존 프론트 `syncPortfolio()`는 `GET /portfolio/sean` (DB만 읽기)이라 실제 KIS 동기화 미수행
- 프론트 `syncPortfolio()` → 새 sync 엔드포인트 호출 + 잔고/보유종목 갱신

### 4-4. 보유종목 US 자산 원화 환산 버그 수정

**증상**: 보유종목 요약에서 US 주식 평가액이 USD 숫자를 그대로 KRW로 표시 (예: $244,234 → ₩246,483)

**원인**: `fetchPortfolioFull()`에서 US 종목의 `current_value`(USD)를 환율 변환 없이 원화 합산

**수정**:
- `GET /portfolio/{user_id}/full-report` 응답 구조 변경: `List` → `{holdings: List, exchange_rate: float}`
- 프론트 `fetchPortfolioFull()`에서 US 종목은 `exchange_rate`를 곱해 원화 환산
- US 자산 표시: `$244,234 (₩365,294,883, 97.1%)` 형태로 달러+원화 동시 표시

| 파일 | 변경 |
|------|------|
| `routers/portfolio.py` | `full-report` 반환 구조 `{holdings, exchange_rate}` |
| `static/index.html` | `fetchPortfolioFull()` 환율 적용 + US 달러/원화 동시 표시 |

### 4-5. 미체결 자산 표시 (잔고 카드)

**설계 결정**: 매수 주문 접수 시 현금은 즉시 차감 (KIS가 예수금 잠금하므로 실제와 일치). 미체결 주문은 별도 섹션으로 표시.

**수정**:
- `/trading/balance` 응답에 `pending` 필드 추가: `{orders, buy_krw, buy_usd, sell_krw, sell_usd, count}`
- 프론트 잔고 카드에 미체결 섹션 표시 (주황색 박스, 종목별 매수/매도 상세)
- 미체결 없으면 해당 섹션 숨김

| 파일 | 변경 |
|------|------|
| `routers/trading.py` | `/trading/balance` 응답에 `pending` 데이터 추가 |
| `static/index.html` | `renderBalanceContent()`에 미체결 표시 UI 추가 |

**표시 예시**:
```
⏳ 미체결 3건 (매수 ₩1,500,000 / 매도 $1,200.00)
🔵005930 10주 @₩72,000 (10:04:34)  🔴AAPL 5주 @$185.20 (10:05:26)
```

### 4-6. 손절 후 재매수 차단 (panic_lock) + 점수 범위 수정

**문제**: 손절 실행 후 해당 종목이 다음 루프에서 신규 매수 후보로 재진입 (DCF 저평가 + 공포장으로 score가 낮게 계산)

**수정**:

| 파일 | 변경 |
|------|------|
| `services/strategy/position_service.py` | `_set_panic_lock()`, `_clear_expired_panic_locks()` 추가. `_route_signal()`에서 forced_sell 성공 시 호출. `_execute_collected_signals()`에서 만료 정리 |
| `services/strategy/signal_service.py` | score 최소값 `max(0,...)` → `max(1,...)`. `no_price_data` 시 score=0 → score=50 |

**동작**:
- 손절 성공 → `panic_locks[ticker] = 당일 날짜` (DB 영속)
- `calculate_score()` 진입 시 panic_lock 확인 → score=50(중립) 반환, 매수 트리거 안 됨
- 3일 후 자동 해제 (`_clear_expired_panic_locks`)

---

## 5. 향후 고려사항

- **미체결 주문 자동 취소**: 일정 시간 경과 후 미체결 주문을 KIS API로 취소하는 기능 (현재 미구현)
- **장 종료 후 정리**: 장 마감 시 미체결 주문 일괄 취소 + DB 정리

---

**Last Updated**: 2026-03-16
