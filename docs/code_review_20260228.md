# 코드 리뷰 리포트 — 2026-02-28

> 검사 대상: `routers/`, `services/`, `models/`, `repositories/` (scripts/ 제외)
> 도구: `lint_fastapi.py` + LLM 심층 분석

---

## 전체 요약

| 등급 | 건수 |
|------|------|
| 🔴 크리티컬 | **75건** |
| 🟡 경고 | **190건** |
| 🔵 개선 필요 | **65건** |
| **합계** | **330건** |

크리티컬 75건 중 **64건은 Repository 패턴 미적용**(직접 DB 세션 접근)이며,
**5건은 하드코딩된 TR ID로 VTS/실전 자동 전환 불가** 이슈입니다.

---

## 🔴 크리티컬 이슈 — 우선 수정 필요

### C-1. Repository 패턴 미적용 (64건)

**핵심 원인**: 서비스 레이어가 `StockMetaService.get_session()`을 직접 호출하여
`Router → Service → DB` 구조. Repository 계층 없음.

| 파일 | 직접 세션 접근 건수 |
|------|---|
| `services/market/stock_meta_service.py` | 37건 |
| `services/config/settings_service.py` | 10건 |
| `services/trading/order_service.py` | 8건 |
| `services/trading/portfolio_service.py` | 8건 |
| `services/analysis/financial_service.py` | 1건 |
| `services/market/data_service.py` | 1건 |

#### 📌 현재 코드 (settings_service.py, L60)
```python
session = StockMetaService.get_session()
setting = session.query(Settings).filter_by(key=key).first()
```

#### ✅ 수정 코드
```python
# services/config/settings_service.py
from repositories.settings_repo import SettingsRepo

value = SettingsRepo.get(key)
```

**목표 아키텍처**:
```
Router → Service → Repository → repositories/database.py → SQLite
```

이미 생성된 파일:
- `repositories/database.py` — DB 싱글톤 (engine/session 중앙 관리)
- `repositories/portfolio_repo.py` — Portfolio/PortfolioHolding CRUD
- `repositories/trade_history_repo.py` — TradeHistory CRUD
- `repositories/settings_repo.py` — Settings CRUD

다음 단계: `PortfolioService`, `OrderService`, `SettingsService`에서 `StockMetaService.get_session()` 제거하고 각 Repo 사용.

---

### C-2. 하드코딩된 TR ID — VTS/실전 자동 전환 불가 (5건)

KIS API는 모의투자(VTS)와 실전 환경에서 TR ID가 다릅니다.
`.env`의 `KIS_IS_VTS` 변경 시 자동 전환되어야 하나 하드코딩으로 불가.

DB의 `api_tr_meta` 테이블에 실전/VTS TR ID가 이미 등록되어 있으며,
`StockMetaService.get_api_info(api_name)` 메서드가 자동 전환을 지원합니다.

---

#### [C-2-1] `stock_meta_service.py` — Line 247, 255 (`initialize_default_meta`)

```python
# 📌 현재 코드 — US 모의투자 환경에서 잘못된 TR ID/Path 사용
def initialize_default_meta(cls, ticker: str):
    if is_kr(ticker):
        return cls.upsert_stock_meta(
            ticker,
            api_path="/uapi/domestic-stock/v1/quotations/inquire-price",
            api_tr_id="FHKST01010100",  # ❌ 하드코딩
            api_market_code="J"
        )
    else:
        return cls.upsert_stock_meta(
            ticker,
            api_path="/uapi/overseas-stock/v1/quotations/price-detail",  # ❌ VTS 미지원 경로
            api_tr_id="HHDFS70200200",  # ❌ 실전 전용 — VTS 시 HHDFS00000300 필요
            api_market_code="NAS"
        )
```

```python
# ✅ 수정 코드 — get_api_info() 활용
def initialize_default_meta(cls, ticker: str):
    if is_kr(ticker):
        tr_id, api_path = cls.get_api_info("주식현재가_시세")
        return cls.upsert_stock_meta(
            ticker, market_type="KR",
            api_path=api_path, api_tr_id=tr_id, api_market_code="J"
        )
    else:
        tr_id, api_path = cls.get_api_info("해외주식_상세시세")
        return cls.upsert_stock_meta(
            ticker, market_type="US",
            api_path=api_path, api_tr_id=tr_id, api_market_code="NAS"
        )
```

---

#### [C-2-2] `stock_ranking_service.py` — Line 44

```python
# 📌 현재 코드 — 실전 전용 TR ID 하드코딩
api_tr_id="HHDFS70200200"  # ❌ VTS 환경에서 HHDFS00000300 필요
```

```python
# ✅ 수정 코드
tr_id, api_path = StockMetaService.get_api_info("해외주식_상세시세")
api_tr_id = tr_id
```

---

#### [C-2-3] `kis_fetcher.py` — Line 356

```python
# 📌 현재 코드
tr_id = "HHDFS76240000"  # ❌ 해외주식 기간별 시세 하드코딩
```

```python
# ✅ 수정 코드
tr_id, _ = StockMetaService.get_api_info("해외주식_기간별시세")
```

---

#### [C-2-4] `kis_service.py` — Line 142 (잔고조회)

```python
# 📌 현재 코드 — 모의투자 TR ID만 사용 (실전에서도!)
tr_id = "VTTC8434R"  # ❌ 실전 모드에서 TTTC8434R 필요
```

```python
# ✅ 수정 코드
tr_id, _ = StockMetaService.get_api_info("주식잔고조회")
# DB: VTS="VTTC8434R", Real="TTTC8434R" 자동 선택
```

---

#### [C-2-5] `kis_service.py` — Line 371~373, 402, 431 (주문)

```python
# 📌 현재 코드 — VTS/Real 각각 하드코딩 (분기는 있으나 DB 미활용)
if Config.KIS_IS_VTS:
    tr_id = "VTTC0802U" if order_type == "buy" else "VTTC0801U"
else:
    tr_id = "TTTC0802U" if order_type == "buy" else "TTTC0801U"
```

```python
# ✅ 수정 코드
api_name = "주식주문_매수" if order_type == "buy" else "주식주문_매도"
tr_id, _ = StockMetaService.get_api_info(api_name)
# DB가 단일 진실 공급원(SSOT)이 됨
```

---

### C-3. `routers/auth.py` — `verify` response_model 누락 (1건)

**auth.py — Line 68**

```python
# 📌 현재 코드 — response_model 없어 내부 데이터 유출 가능
@router.get("/auth/verify")
def verify(token: str):
    ...
```

```python
# ✅ 수정 코드
@router.get("/auth/verify", response_model=TokenResponse)
def verify(token: str) -> TokenResponse:
    ...
```

---

## 🟡 경고 이슈

### W-1. `models/schemas.py` — 35개 도메인 혼재 (God Schema File)

**Line 1 — [File]**
```
📌 현재 코드: 단일 schemas.py에 35개 도메인 클래스 혼재
```

권장 분리 구조:
```
models/
  schemas/
    __init__.py        # 하위 호환 re-export
    portfolio.py       # Portfolio*, Holding*
    trading.py         # TradeRecord*, Order*, Sell*
    analysis.py        # Dcf*, Financial*, Valuation*
    market.py          # Macro*, News*, Watch*, Regime*
    settings.py        # Setting*, Tick*
    common.py          # Message*, Status*
```

---

### W-2. `trading_strategy_service.py` — 함수 길이 초과 10건

| 함수명 | 줄 수 | 분리 방향 |
|--------|-------|-----------|
| `run_sector_rebalance` (L1342) | 171줄 | 매도/매수/보고 3개 함수 |
| `calculate_score` (L894) | 169줄 | 시그널별 helper |
| `_execute_trade_v2` (L1243) | 94줄 | 주문/기록/알림 분리 |
| `_execute_collected_signals` (L608) | 78줄 | 매수/매도 루프 분리 |
| `_passes_allocation_limits` (L249) | 84줄 | 제약 조건별 helper |

---

### W-3. `scheduler_service.py` — N+1 패턴 (Line 608)

```python
# 📌 현재 코드 — N+1: 루프 내 반복 서비스 호출
for ticker in tickers:
    tier = MarketDataService.get_tier(ticker)   # N번 호출 ❌
```

```python
# ✅ 수정 코드 — 배치 조회
tiers = MarketDataService.get_all_tiers(tickers)  # 1번
for ticker, tier in tiers.items():
    ...
```

---

### W-4. `kis_service.py` — 타입 힌트 전량 미적용 (21건)

```python
# 📌 현재 코드
def get_balance(cls):
def send_order(cls, ticker, qty, price, order_type):

# ✅ 수정 코드
def get_balance(cls) -> Optional[dict]:
def send_order(cls, ticker: str, qty: int, price: float, order_type: str) -> dict:
```

---

### W-5. `stock_ranking_service.py` — N+1 외부 API 호출 (Line 24)

```python
# 📌 현재 코드 — KIS API를 루프 내 반복 호출 (TPS 제한 위험)
for stock in top_stocks:
    data = KisService.get_overseas_ranking()  # N번 API 호출 ❌
```

수정 방법: 한번에 전체 목록 조회 후 순회.

---

### W-6. `kis_fetcher.py` — `safe_float` 3중 재정의 (Line 108, 172, 222)

```python
# 📌 현재 코드 — 동일 함수가 3회 재정의됨 (마지막 정의만 유효)
def safe_float(val, default=0.0): ...  # Line 108
def safe_float(val, default=0.0): ...  # Line 172 (재정의)
def safe_float(val, default=0.0): ...  # Line 222 (재정의)
```

수정 방법: `utils/convert.py` 등에 1회 정의 후 import.

---

### W-7. `scheduler_service.py` — `start()` 110줄, 타입 힌트 13건 등

`SchedulerService.start()`가 110줄로 스케줄 등록, WS 스레드 시작, DB 초기화를 모두 담당.
`_register_jobs()`, `_start_ws_thread()` 등으로 분리 권장.

---

### W-8. 페이징 파라미터 누락 (routers/)

| 파일 | 핸들러 | Line |
|------|--------|------|
| `analysis.py` | `get_all_dcf` | 61 |
| `market.py` | `get_news` | 39 |
| `market.py` | `get_weekly_economic_calendar` | 65 |
| `market.py` | `get_regime_history` | 72 |
| `market.py` | `get_watching_list` | 90 |
| `portfolio.py` | `get_portfolio` | 32 |

수정 방법: `limit: int = Query(default=50, le=200)` 파라미터 추가.

---

## 🔵 개선 필요

### I-1. `models/stock_meta.py:43` — `Financials.stock_id` FK index 누락

```python
# 📌 현재 코드
stock_id = Column(Integer, ForeignKey('stock_meta.id'), nullable=False)

# ✅ 수정 코드
stock_id = Column(Integer, ForeignKey('stock_meta.id'), nullable=False, index=True)
```

종목당 수천 행이 쌓이는 `financials` 테이블의 핵심 조회 컬럼. **즉시 수정 권장**.

---

### I-2. `models/portfolio.py:26` — `portfolio_id` FK index 누락

```python
# 📌 현재 코드
portfolio_id = Column(Integer, ForeignKey('portfolios.id'))

# ✅ 수정 코드
portfolio_id = Column(Integer, ForeignKey('portfolios.id'), index=True)
```

---

### I-3. `kis_fetcher.py` — 함수 길이 초과 (4건)

| 함수 | 줄 수 |
|------|-------|
| `fetch_overseas_daily_price` | 64줄 |
| `fetch_overseas_detail` | 55줄 |
| `fetch_domestic_price` | 51줄 |
| `fetch_domestic_ranking` | 45줄 |

파싱 로직을 `_parse_*` 헬퍼로 분리 권장.

---

### I-4. `models/__repr__` 타입 힌트 누락 (11건)

`StockMeta`, `Financials`, `ApiTrMeta`, `DcfOverride`, `MarketRegimeHistory`, `Settings`, `TradeHistory`의 `__repr__` 메서드에 `-> str` 미기재.

---

## 📋 종합 LLM 판단

```
[확정 - 즉시 수정]
  C-1.   Repository 패턴 미적용 64건 (6개 파일)
  C-2-1. stock_meta_service.py initialize_default_meta 하드코딩 TR ID
  C-2-2. stock_ranking_service.py:44 HHDFS70200200 하드코딩
  C-2-3. kis_fetcher.py:356 HHDFS76240000 하드코딩
  C-2-4. kis_service.py:142 잔고조회 VTTC8434R 하드코딩 (실전도 VTS ID 사용)
  C-3.   auth.py verify response_model 누락
  W-5.   stock_ranking_service.py N+1 KIS API 루프 호출
  W-6.   kis_fetcher.py safe_float 3중 재정의
  I-1.   Financials.stock_id FK index 누락
  I-2.   PortfolioHolding.portfolio_id FK index 누락

[확정 - 중간 우선순위]
  C-2-5. kis_service.py 주문 TR ID 하드코딩 (분기는 있으나 DB SSOT 위반)
  W-3.   scheduler_service.py N+1 get_tier() 루프
  W-4.   kis_service.py 타입 힌트 전량 미적용 (21건)

[오탐 (False Positive)]
  schemas.py BaseModel 직접 상속 — 단순 응답 스키마에 *Base 계층 불필요
  models/ __repr__ 타입 힌트 — Python 매직 메서드, 관례상 생략 허용

[낮은 우선순위]
  W-1. schemas.py 도메인 분리 (기능 정상, 향후 확장 시 적용)
  W-2. trading_strategy_service.py 함수 길이 (전략 로직 특성상 자연스러움)
  W-7. scheduler_service.py start() 분리
  W-8. 페이징 파라미터 누락 (현 운용 규모에서 영향 미미)
  I-3. kis_fetcher.py 함수 길이
  I-4. __repr__ -> str 힌트
```

---

## 📌 즉시 수정 체크리스트

- [ ] `repositories/` 패키지 → `PortfolioService`, `OrderService`, `SettingsService` 적용
- [ ] `stock_meta_service.py:240~257` → `initialize_default_meta` get_api_info() 사용
- [ ] `stock_ranking_service.py:44` → `get_api_info("해외주식_상세시세")` 사용
- [ ] `kis_fetcher.py:356` → `get_api_info("해외주식_기간별시세")` 사용
- [ ] `kis_service.py:142` → `get_api_info("주식잔고조회")` 사용
- [ ] `kis_fetcher.py` → `safe_float` 3중 정의 → `utils/convert.py` 이전
- [ ] `routers/auth.py:68` → `response_model=TokenResponse` 추가
- [ ] `models/stock_meta.py:43` → `stock_id`에 `index=True` 추가
- [ ] `models/portfolio.py:26` → `portfolio_id`에 `index=True` 추가

---

*생성: Claude Sonnet 4.6 — 2026-02-28*
