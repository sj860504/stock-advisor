# 리팩토링 계획 (2026-03-15)

> 코드 리뷰 + 문서 현행화 후 도출된 전체 수정 목록.
> 우선순위: **Phase A (버그)** → **Phase B (기술 부채)** → **Phase C (미구현 기능)**

---

## Phase A — 버그 수정 (운영 영향 있음)

### A-1: `_handle_forced_sell` — 손절 시 분할 매도 발생

**파일**: `services/strategy/position_service.py:443-448`

**증상**: 손절 신호 발생 시 전량 즉시 매도가 아닌 `STRATEGY_SELL_SPLIT_COUNT` 분할 매도 실행.

**원인**: `_handle_forced_sell`이 `TradeExecutorService._execute_trade_v2`를 호출할 때 `forced_qty` 인자를 전달하지 않음.
→ `_execute_sell_order(forced_qty=None)` → `sell_qty = holding_qty / split_count`

**수정**:
```python
# 현재 (position_service.py:443)
return TradeExecutorService._execute_trade_v2(
    ticker, "sell", f"stop_loss({profit_pct:.2f}%)", profit_pct, True, 0,
    current_price, market_total, cash_balance, exchange_rate,
    holdings=holdings, user_id=user_id, holding=holding, macro=macro_data,
    target_cash_ratio_kr=target_cash_kr, target_cash_ratio_us=target_cash_us,
)

# 수정 후 — forced_qty 전달
holding_qty = holding.quantity
return TradeExecutorService._execute_trade_v2(
    ticker, "sell", f"stop_loss({profit_pct:.2f}%)", profit_pct, True, 0,
    current_price, market_total, cash_balance, exchange_rate,
    holdings=holdings, user_id=user_id, holding=holding, macro=macro_data,
    target_cash_ratio_kr=target_cash_kr, target_cash_ratio_us=target_cash_us,
    forced_qty=holding_qty,  # ← 추가
)
```

**연관 수정**: `_execute_sell_order` (execution_service_v2.py:544) — forced_qty != None 시 msg가 `"partial_sell(split)"` → `"stop_loss(full)"` 로 변경해야 로그 명확해짐.

---

### A-2: KIS 토큰 파일 기반 캐시 — DB 기반 전환 필요

**파일**: `services/kis/kis_service.py:78-99, 139-159`

**증상**: `kis_token.json`, `kis_real_token.json` 파일로 토큰 캐싱 중. CLAUDE.md 규칙 "KIS 토큰은 DB에서 조회하여 사용할 것. 파일 기반 토큰 사용 금지" 위반.

**수정 방향**:
1. DB `settings` 테이블에 `kis_access_token`, `kis_token_expiry` 키 추가
2. `_load_cached_token()` → `SettingsRepo.get("kis_access_token")` 로 대체
3. `_request_new_token()` 발급 후 DB 저장, 파일 저장 제거
4. `_load_cached_real_token()` / `_request_new_real_token()` 동일 패턴 적용

**참고**: 현재 in-memory cache (`cls._access_token`)는 유지해도 무방. 파일 I/O만 DB로 교체.

---

## Phase B — 기술 부채 (운영 영향 낮음)

### B-1: `kis_fetcher.py` — "FHKST03030100" TR ID 하드코딩 (C-2-3)

**파일**: `services/kis/fetch/kis_fetcher.py:364, 445`

**현황**:
- `fetch_overseas_daily_price()`는 `StockMetaService.get_api_info("해외주식_기간별시세")`로 TR ID를 가져옴 (올바름)
- 그러나 `_build_overseas_daily_params()`와 fallback 조건에서 `"FHKST03030100"`을 하드코딩으로 비교

**수정**: 상수 `OVERSEAS_DAILY_TR_LEGACY = "FHKST03030100"` 정의 후 사용, 또는 StockMetaService 조회값과 비교.

---

### B-2: `kis_service.py` — 해외 잔고조회 TR ID 배열 하드코딩 (C-2-4)

**파일**: `services/kis/kis_service.py:336`

**현황**:
```python
tr_ids = ["VTTS3012R", "TTTS3012R", "VTTT3012R", "TTTT3012R"]
```
직접 하드코딩. KIS 환경 변경 시 코드 수정 필요.

**수정 방향**: `StockMetaService`에 `"해외주식_잔고조회"` API 등록 후 조회. 또는 Config 기반 단일 TR ID 사용 (현재는 4개 순차 시도하는 fallback 구조).

---

### B-3: `kis_service.py:425` — AAPL/NASD 하드코딩

**파일**: `services/kis/kis_service.py:423-426`

**현황**:
```python
# Default to AAPL/NASD if no holdings (cash balance is ticker-independent)
item_cd = "AAPL"
excg_cd = "NASD"
```
현금 잔고 조회를 위한 더미 종목 하드코딩.

**수정 방향**: Config에 `KIS_DEFAULT_OVERSEAS_TICKER = "AAPL"`, `KIS_DEFAULT_OVERSEAS_EXCHANGE = "NASD"` 추가.

---

### B-4: `_execute_sell_order` — stop_loss 경로 로그 오류

**파일**: `services/strategy/execution_service_v2.py:544-548`

**현황**:
```python
if forced_qty is not None and forced_qty > 0:
    sell_qty = min(forced_qty, holding_qty)
    msg = "partial_sell(split)"   # ← stop_loss 경로에서도 이 msg 사용
else:
    sell_qty = max(1, int(holding_qty / split_count))
    msg = "partial_sell(take_profit)"
```

A-1 수정 후 forced_qty가 전달되면 `"partial_sell(split)"` 대신 `"stop_loss(full)"` 로 표시되어야 로그/히스토리에서 구분 가능.

**수정**: `msg = "stop_loss(full)"` 또는 호출자에서 reason 문자열로 구분.

---

## Phase C — 미구현 기능

### C-1: `portfolio_service.rebalance_portfolio` — 미구현

**파일**: `services/trading/portfolio_service.py`

**현황**: 함수가 `pass`로만 구현됨.

**필요성**: `AssetManagementService`에서 현금 비중 조정을 수행하지만, 개별 종목 비중 리밸런싱은 별도로 필요할 수 있음.

**구현 방향**: 현재 보유 종목의 목표 비중(섹터/종목별) 대비 실제 비중 계산 → 초과 비중 매도 → 부족 비중 매수. AssetManagementService와의 역할 분리 먼저 설계 필요.

---

### C-2: `news_service.py` — KIS 뉴스 API 미구현

**파일**: `services/market/news_service.py`

**현황**: KIS 뉴스 API 호출 부분이 placeholder 상태.

**구현 방향**: KIS `StockMetaService`에 뉴스 API 등록, `fetch_news(ticker)` 구현. 현재 `NewsService`는 대체 뉴스 소스(yfinance 등)를 활용 중인지 먼저 확인 필요.

---

## 작업 순서 (권장)

```
A-1 (forced sell bug)          ← 즉시 수정. 운영 손실 방지.
A-2 (token DB migration)       ← 다음 스프린트. 규칙 준수.
B-4 (sell log msg)             ← A-1 수정과 함께 처리.
B-3 (AAPL hardcode)            ← 간단. 30분.
B-1, B-2 (TR ID hardcode)      ← StockMetaService 등록 후 처리.
C-1 (rebalance)                ← 설계 선행 필요.
C-2 (news API)                 ← 낮은 우선순위.
```

---

**작성일**: 2026-03-15
**상태**: ✅ 전체 완료 (2026-03-15)
