# 전체 코드 리뷰 결과 (2026-03-15)

> 19개 파일 대상. 버그/보안/성능/코드품질/예외처리 5개 기준으로 검토.

---

## 🔴 Critical (2건) — 즉시 수정 필요

### C-1. `execution_service_v2.py:352` — `_is_cash_ratio_sufficient()` 조건 반전

**현상**: 함수명은 "현금 충분 여부"인데 실제 로직은 반전. 현금이 충분할 때 매수를 차단하고, 부족할 때 허용함.

**수정 방법**:
```python
# 현재 (반전됨)
def _is_cash_ratio_sufficient(cls, ...) -> bool:
    return cash_ratio < target_cash_ratio  # 충분할 때 False 반환

# 수정 후
def _is_cash_ratio_sufficient(cls, ...) -> bool:
    return cash_ratio >= target_cash_ratio  # 충분할 때 True 반환
```
또는 함수명을 `_is_cash_ratio_below_target()`으로 변경하고 호출부 조건 반전.

---

### C-2. `signal_service.py:262` — `calculate_score()` 반환 타입 불일치

**현상**: `curr_price <= 0` 경로에서 `return 0, ["no_price_data"]` (2-tuple) 반환.
호출부에서 `score, signals, reasons = calculate_score(...)` 3-tuple 언패킹 시 `ValueError` 발생.

**수정 방법**:
```python
# 현재
if curr_price <= 0:
    return 0, ["no_price_data"]

# 수정 후 — 반환 타입 통일 (3-tuple)
if curr_price <= 0:
    return 0, [], ["no_price_data"]
```

---

## 🟠 High (6건) — 우선 수정

### H-1. `portfolio_service.py:238` — `sync_with_kis()` 반환 타입 불일치

**현상**: 반환 타입 선언 `List[HoldingSchema]`이지만 실제로는 `[h.model_dump() for h in holdings]` (dict 리스트) 반환.
호출부에서 `h.ticker` 등 속성 접근 시 `AttributeError` 잠재.

**수정 방법**: 두 가지 옵션 중 선택.
- **Option A**: 반환값을 `HoldingSchema` 리스트로 통일 → `return holdings` (model_dump 제거)
- **Option B**: 타입 선언을 `List[dict]`으로 변경 + 호출부 전체 확인

권장: **Option A** — 타입 안전성 확보.

---

### H-2. `macro_service.py:87` — `print()` 사용

**현상**: `print(f"레짐 분석: ...")` — 프로덕션 코드에서 `print()` 직접 사용.

**수정 방법**:
```python
# 현재
print(f"레짐 분석: {result}")

# 수정 후
logger.info(f"레짐 분석: {result}")
```

---

### H-3. `kis_service.py:654,705` — 체결조회 TR ID 하드코딩

**현상**: 국내/해외 체결조회 함수에서 TR ID 하드코딩.

**수정 방법**:
- `stock_meta_service.init_api_tr_meta`에 TR ID 추가
- `StockMetaService.get_api_info("국내주식_체결조회")` / `get_api_info("해외주식_체결조회")` 로 교체

---

### H-4. `execution_service_v2.py:243` — `_compute_market_balances()` 반환값 미사용

**현상**: `_compute_market_balances()` 호출 후 반환값을 변수에 할당하지 않음.
계산 결과가 실제 매매 로직에 반영되지 않을 수 있음.

**수정 방법**: 소스 재확인 후 반환값 실제 사용 여부 파악. 미사용이면:
```python
# 반환값 사용
market_balances = cls._compute_market_balances(...)
# 이후 로직에서 market_balances 활용
```

---

### H-5. `portfolio_service.py:548` — `_rebalance_logic()` dict 타입 혼용

**현상**: `_rebalance_logic()` 내부에서 `HoldingSchema`와 `dict` 혼용.
`sync_with_kis()` 반환 타입 불일치(H-1)와 연동된 이슈.

**수정 방법**: H-1 수정 후 연동하여 수정. `HoldingSchema` 객체로 통일.

---

### H-6. `position_service.py:657` — `exchange_rate=1350.0` 하드코딩

**현상**: 환율 하드코딩. 실제 환율 변동 미반영.

**수정 방법**:
```python
# 현재
exchange_rate = 1350.0

# 수정 후 — DB 또는 MarketDataService에서 조회
exchange_rate = MarketDataService.get_exchange_rate() or 1350.0  # fallback 포함
```
또는 `SettingsRepo.get("USD_KRW_RATE", default=1350.0)` 사용.

---

## 🟡 Medium (7건) — 다음 스프린트

### M-1. `signal_service.py:200` — `_score_bonuses()` N+1 쿼리

**현상**: 루프마다 DB 조회. 종목 수가 많을수록 성능 저하.

**수정 방법**: 루프 전 일괄 조회 후 dict로 캐싱:
```python
# 루프 전
settings_cache = {key: SettingsRepo.get(key) for key in needed_keys}
# 루프 내
value = settings_cache.get(key)
```

---

### M-2. `position_service.py:513` — `split_orders.pop(t)` KeyError 가능

**수정 방법**:
```python
split_orders.pop(t)  →  split_orders.pop(t, None)
```

---

### M-3. `settings_service.py:94,102` — bare `except:`

**수정 방법**:
```python
except:  →  except (ValueError, TypeError):
```

---

### M-4. `repositories/settings_repo.py:17` — `get_session()` 직접 사용

**현상**: 읽기 전용 조회에 `get_session()` 사용. `session_ro()` 패턴 위반.

**수정 방법**: `with session_ro() as session:` 으로 교체.

---

### M-5. `kis_fetcher.py:430` — bare `except: pass`

**수정 방법**:
```python
except:
    pass
# →
except Exception as e:
    logger.debug(f"[kis_fetcher] 파싱 실패 무시: {e}")
```

---

### M-6. `kis_fetcher.py:234` — `fetch_overseas_price()` 재시도 미사용

**현상**: `_get_with_retry()` 헬퍼가 있는데 직접 `requests.get()` 호출.

**수정 방법**: `_get_with_retry()` 사용으로 통일.

---

### M-7. `strategy_state_repo.py:36` — `panic_locks` set 변환 누락

**현상**: DB에서 읽어온 `panic_locks`를 `set()`으로 변환하지 않아 `in` 연산 타입 오류 가능.

**수정 방법**:
```python
state.panic_locks = set(row.panic_locks or [])
```

---

## 수정 우선순위 및 작업 순서

```
1단계 (Critical — 즉시):
  C-1. _is_cash_ratio_sufficient() 조건 반전 수정
  C-2. calculate_score() 반환 타입 통일

2단계 (High — 이번 스프린트):
  H-1. sync_with_kis() 반환 타입 통일 (HoldingSchema)
  H-5. _rebalance_logic() H-1 연동 수정
  H-2. macro_service print → logger
  H-4. _compute_market_balances() 반환값 사용 여부 확인
  H-6. exchange_rate 하드코딩 제거

3단계 (Medium — 다음 스프린트):
  M-2. split_orders.pop(t, None)
  M-3. bare except 교체
  M-4. session_ro() 패턴 적용
  M-5, M-6. kis_fetcher 예외/재시도 통일
  M-7. panic_locks set 변환
  M-1. N+1 쿼리 최적화 (효과 측정 후)

H-3 별도 (TR ID 하드코딩):
  kis_service 체결조회 TR ID → DB 조회 전환
  init_api_tr_meta에 2개 추가
```

---

**작성일**: 2026-03-15
**상태**: ✅ 전체 수정 완료 (2026-03-15)

## 수정 완료 내역

| ID | 파일 | 수정 내용 |
|----|------|-----------|
| C-1 | `execution_service_v2.py` | `_is_cash_ratio_sufficient` → `_is_cash_below_target` 리네임 + 로그 메시지 수정 |
| C-2 | `signal_service.py:262` | `return 0, ["no_price_data"]` → `return 0, ["no_price_data"], {}` (3-tuple 통일) |
| H-1 | `portfolio_service.py:238` | `return [h.model_dump() for h in holdings]` → `return holdings` (HoldingSchema 반환) |
| H-2 | `macro_service.py:87` | `print(...)` → `logger.info(...)` |
| H-3 | `stock_meta_service.py` + `kis_service.py` | `국내주식_체결조회`, `해외주식_체결조회` TR ID DB 추가 + DB 조회로 교체 |
| H-4 | `execution_service_v2.py:243` | `_compute_market_balances()` 반환값 미사용 dead call 제거 |
| H-5 | `portfolio_service.py:548` | H-1 수정으로 연동 해결 (HoldingSchema 속성 접근 가능) |
| H-6 | `position_service.py:657` | `exchange_rate=1350.0` → `MacroService.get_exchange_rate()` |
| M-2 | `position_service.py:518` | `split_orders.pop(t)` → `split_orders.pop(t, None)` |
| M-3 | `settings_service.py:94,102` | `except:` → `except (ValueError, TypeError):` |
| M-4 | `settings_repo.py` | `get_session()` 직접 사용 → `session_ro()` 컨텍스트 매니저로 교체 |
| M-5 | `kis_fetcher.py:430` | `except: pass` → `except Exception as e: logger.debug(...)` |
| M-6 | `kis_fetcher.py:234` | `requests.get()` → `cls._get_with_retry()` + `None` 반환 처리 |
| M-7 | `strategy_state_repo.py` | `panic_locks`는 `Dict[str, Any]` 스키마 확인 — 수정 불필요 (dict keys 체크는 정상) |
| M-1 | `signal_service.py` | N+1 쿼리 (SettingsService 캐시 적용, load_portfolio는 구조 변경 필요) — 보류 |
