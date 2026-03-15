# SRP 위반 함수 리팩토링 계획 (2026-03-15)

> 30줄 이상 함수 전수 검토 후 SRP 위반 심각도 기준 우선순위 정렬.
> **방침**: 기존 동작 100% 유지. 추출(Extract Method)만 사용. 로직 변경 없음.

---

## 대상 함수 요약

| 순위 | 함수 | 파일 | 줄수 | 실제 책임 수 | 비고 |
|------|------|------|------|------------|------|
| 1 | `get_overseas_balance` | kis_service.py | 66 | 3 | 중첩 루프 + 파싱 + fallback |
| 2 | `_full_api_warmup` | market_data_service.py | 46 | 4 | Phase 1~4 명시적 주석 |
| 3 | `sync_with_kis` | portfolio_service.py | 36 | 3 | 거의 분리됨, 1개 추출 |
| 4 | `_process_single_signal` | position_service.py | 40 | 1 | 오케스트레이터 패턴 — 정상 |
| 5 | `_execute_trade_v2` | execution_service_v2.py | 32 | 2 | 거의 분리됨 |
| 6 | `format_portfolio_report` | report_service.py | 22 | 1 | 정상 — 수정 불필요 |

> **4, 6번은 수정 불필요**: 이미 핸들러/헬퍼로 위임하는 오케스트레이터 패턴.
> 수정 대상: **1, 2, 3, 5번** (4개 함수).

---

## Phase 1 — `kis_service.get_overseas_balance` (최우선)

### 현재 구조
```
get_overseas_balance()
  ├─ TR ID 로딩 + URL 구성
  └─ for tr_id in tr_ids:             ← 책임 A: TR ID 순회 + fallback
       └─ for page in range(10):      ← 책임 B: 페이지네이션 루프
            ├─ HTTP GET               ← 책임 C: HTTP 요청 + 상태 검증
            ├─ rt_cd 체크             ←        비즈니스 에러 검증
            ├─ output1/2 파싱         ← 책임 D: 응답 파싱
            └─ ctx_area 토큰 처리     ←        페이지 연속 처리
  └─ stale cache fallback             ← 책임 A 계속
```

### 분리 후 구조
```
get_overseas_balance()                          [책임 A만] — ~15줄
  └─ _fetch_all_pages_for_tr_id(tr_id, url, base_params) → Optional[dict]
                                                [책임 B: 페이지 루프] — ~25줄
       └─ _fetch_one_page(tr_id, url, params, page_num) → Optional[dict]
                                                [책임 C+D: 요청+파싱] — ~15줄
```

### 추출할 함수

**`_fetch_one_page(tr_id, url, params, page_num) -> Optional[dict]`**
```
- HTTP GET 실행
- status >= 500 → None
- rt_cd != "0" → None
- output1/output2 파싱하여 {"holdings": [...], "summary": [...], "ctx_fk": ..., "ctx_nk": ...} 반환
```

**`_fetch_all_pages_for_tr_id(tr_id, url, base_params) -> Optional[dict]`**
```
- 페이지 루프 (range(10))
- _fetch_one_page 호출 → None이면 break
- holdings 누적, ctx 토큰으로 params 업데이트
- all_holdings 비어있으면 None 반환
```

**`get_overseas_balance()` 수정 후**
```
- TR ID + URL 로딩
- for tr_id in tr_ids: _fetch_all_pages_for_tr_id → 성공 시 캐시 저장 후 return
- 모두 실패 시 stale cache fallback
```

---

## Phase 2 — `market_data_service._full_api_warmup`

### 현재 구조
```
_full_api_warmup(ticker, state)          — 46줄
  ├─ # Phase 1: 기본 데이터 저장         ← 책임 A: 기본 메트릭 → DB
  │     _build_partial_metrics(...)
  │     StockMetaService.save_financials(...)
  ├─ # Phase 2: 지표 계산                ← 책임 B: 지표 계산 + state 반영
  │     IndicatorService.compute_...
  │     DcfService.calculate_dcf(...)
  │     state.update_indicators(...)
  ├─ # Phase 3: 최종 메트릭 저장         ← 책임 C: 최종 메트릭 → DB
  │     StockMetaService.save_financials(merged metrics)
  └─ # Phase 4: 타겟 가격 계산           ← 책임 D: EMA200 기반 타겟 가격
        _update_target_prices_from_snapshot(...)
```

### 분리 후 구조
```
_full_api_warmup(ticker, state)          — ~20줄 (오케스트레이터만)
  ├─ _warmup_fetch_data(ticker)          → (basic_info, df) — 데이터 로딩
  ├─ _warmup_save_basic(ticker, state, basic_info, df) → partial_metrics
  ├─ _warmup_compute_indicators(ticker, state, df)     → (snapshot, dcf_val)
  ├─ _warmup_save_final(ticker, partial_metrics, snapshot, dcf_val)
  └─ _update_target_prices_from_snapshot(state, snapshot)  ← 기존 함수 재사용
```

### 추출할 함수

**`_warmup_fetch_data(ticker) -> tuple[dict, DataFrame]`**
```
- api_ticker 정규화
- _fetch_basic_price(ticker)
- DataService.get_price_history(api_ticker, days=300)
- df.empty 시 경고 + (None, empty df) 반환
```

**`_warmup_save_basic(ticker, state, basic_info, df) -> dict`**
```
- _build_partial_metrics(state, basic_info, df)
- StockMetaService.save_financials (Phase 1)
- partial_metrics 반환
```

**`_warmup_compute_indicators(ticker, state, df) -> tuple[snapshot, float]`**
```
- state.prev_close, state.current_price 설정
- IndicatorService.compute_latest_indicators_snapshot
- DcfService.calculate_dcf
- state.update_indicators
- (snapshot, dcf_val) 반환
```

**`_warmup_save_final(ticker, partial_metrics, snapshot, dcf_val)`**
```
- StockMetaService.save_financials(merged dict) (Phase 3)
```

---

## Phase 3 — `portfolio_service.sync_with_kis`

### 현재 구조
```
sync_with_kis(user_id)                          — 36줄
  ├─ KisService.get_balance()                   ← 국내 API 조회
  ├─ load_portfolio + sector_map 구성           ← 기존 포트폴리오 로딩
  ├─ _parse_balance_holdings(...)               ← 파싱 (분리됨)
  ├─ KisService.get_overseas_balance()          ← 해외 API 조회
  ├─ _resolve_us_holdings(...)                  ← US 파싱 (분리됨)
  ├─ _extract_kr_cash_from_summary(...)         ← 현금 추출 (분리됨)
  ├─ _enrich_summary_with_overseas(...)         ← 요약 보강 (분리됨)
  ├─ get_usd_cash_balance(...)                  ← USD 현금 조회
  ├─ cls._last_balance_summary = summary        ← 클래스 상태 저장
  ├─ save_portfolio(...)                        ← DB 저장
  └─ for h in holdings: MarketDataService...    ← 인메모리 가격 동기화 ← 유일한 문제
```

### 분리 후
대부분 이미 헬퍼로 잘 분리되어 있음. 인메모리 동기화 1개만 추출.

**`_sync_in_memory_prices(holdings: List[HoldingSchema]) -> None`**
```
- for h in holdings: price = float(h.current_price or 0)
- price > 0이면 MarketDataService.update_price_from_sync(h.ticker, price)
```

`sync_with_kis()` 마지막 루프 → `cls._sync_in_memory_prices(holdings)` 한 줄로 교체.

---

## Phase 4 — `execution_service_v2._execute_trade_v2`

### 현재 구조
```
_execute_trade_v2(...)                          — 32줄
  ├─ 로깅
  ├─ _check_market_hours                        ← 분리됨
  ├─ MarketDataService.get_state(ticker)        ← 상태 조회 (인라인)
  │    change_rate = getattr(state, ...)        ← 단순 추출
  ├─ _execute_buy_order / _execute_sell_order   ← 분리됨
  └─ _send_trade_alert(...)                     ← 분리됨
```

### 분리 후
`change_rate` 조회만 추출.

**`_get_change_rate(ticker) -> float`**
```
- state = MarketDataService.get_state(ticker)
- return getattr(state, "change_rate", 0.0)
```

`_execute_trade_v2` 내 2줄 → `change_rate = cls._get_change_rate(ticker)` 한 줄.

> 효과가 가장 작음 — Phase 1~3 완료 후 진행.

---

## 작업 순서

```
Phase 1: get_overseas_balance    ← 가장 복잡, 중첩 루프 분리
Phase 2: _full_api_warmup        ← Phase 주석이 이미 분리 경계를 알려줌
Phase 3: sync_with_kis           ← 1개 함수만 추출, 간단
Phase 4: _execute_trade_v2       ← 1줄 추출, 가장 마지막
```

---

## 공통 원칙

- **기존 공개 API 변경 없음**: `get_overseas_balance()`, `sync_with_kis()` 시그니처 유지
- **추출 함수는 모두 `_private`**: 외부 호출 없음
- **테스트 기준**: 수정 전후 동일 입력 → 동일 출력 확인 (로그 메시지 포함)
- **한 번에 한 Phase씩**: Phase 1 완료 후 동작 확인 → Phase 2 진행

---

**작성일**: 2026-03-15
**상태**: ✅ 전체 완료 (2026-03-15)
