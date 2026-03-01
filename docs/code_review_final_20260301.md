# 코드 리뷰 최종 리포트 (2026-03-01)

> 대상: 15개 파일 전체 lint + LLM 판단
> 도구: `lint_fastapi.py` (정적 분석) + Sonnet 4.6 (의미적 검증)

---

## 전체 요약

| 단계 | 내용 | 결과 |
|------|------|------|
| Phase 1 | DB 세션 직접 접근 제거 | ✅ 완료 (29건→0건) |
| Phase 2 | 거대 함수 분리 | ✅ 완료 (주요 함수 추출) |
| Phase 3 | N+1, 타입힌트, 기타 확정 이슈 | ✅ 완료 |
| Phase 4 | Sonnet 4.6 최종 검증 | ✅ 완료 — 추가 타입힌트 적용 |

**최종 결과: 전 파일 🔴 크리티컬 0건**

---

## Phase 1: DB 세션 직접 접근 제거

### 변경 파일

**`repositories/stock_meta_repo.py`** (신규 생성)
- `StockMetaService`의 DB 직접 접근 로직 전부 추출
- 메서드: `upsert_stock_meta`, `get_stock_meta`, `get_stock_meta_bulk`, `find_ticker_by_name`, `get_kr_individual_stocks`, `save_financials`, `get_latest_financials`, `get_all_latest_dcf`, `get_financials_history`, `get_batch_latest_financials`, `upsert_api_tr_meta`, `get_api_meta`, `upsert_dcf_override`, `get_dcf_override`, `get_all_dcf_overrides`, `save_market_regime`, `get_market_regime_history`, `get_regime_for_date`

**`services/market/stock_meta_service.py`**
- 29건 크리티컬 → 0건
- 모든 DB 로직 → `StockMetaRepo` 위임
- `session_scope()` / `session_ro()` 래퍼는 하위 호환용 유지

**`services/analysis/financial_service.py:307`**
- `get_overrides()` — `StockMetaService.session_ro()` 직접 접근 → `StockMetaRepo.get_all_dcf_overrides()` 교체

**`services/market/data_service.py:152`**
- `get_kr_individual_stocks()` — `session.query(StockMeta)` 직접 접근 → `StockMetaRepo.get_kr_individual_stocks()` 교체

---

## Phase 2: 거대 함수 분리

**`services/kis/kis_service.py`**
- `_post_order_with_retry` 헬퍼 신규 추출 (~42줄)
- `_send_domestic_order` 73줄 → ~15줄
- `send_overseas_order` 71줄 → ~20줄
- 중복 재시도 로직 ~70줄 제거

**`services/strategy/trading_strategy_service.py`**
- `_execute_trade_v2` (94줄) → `_execute_buy_order` + `_execute_sell_order` + `_execute_trade_v2` (~20줄 오케스트레이션) 분리

---

## Phase 3: 타입힌트 + 기타

**`services/strategy/trading_strategy_service.py`**
- 13개 메서드에 반환 타입 힌트 추가

**`services/base/scheduler_service.py`**
- 18개 메서드에 `-> None` 반환 타입 추가

---

## Phase 4: Sonnet 4.6 추가 검증 및 수정

### 추가 적용된 타입힌트

**`services/market/stock_meta_service.py`** (19건 → 4건)
```python
def init_db(cls) -> None
def upsert_stock_meta(cls, ticker: str, **kwargs) -> Optional[StockMeta]
def get_stock_meta(cls, ticker: str) -> Optional[StockMeta]
def save_financials(cls, ...) -> Optional[Financials]
def initialize_default_meta(cls, ticker: str) -> Optional[StockMeta]
def get_latest_financials(cls, ticker: str) -> Optional[Financials]
def get_financials_history(cls, ticker: str, limit: int = 2500) -> list
def get_batch_latest_financials(cls, tickers: list) -> dict
def upsert_api_tr_meta(cls, api_name: str, **kwargs) -> Optional[ApiTrMeta]
def init_api_tr_meta(cls) -> int
def get_api_meta(cls, api_name: str) -> Optional[ApiTrMeta]
def get_api_info(cls, ...) -> tuple[Optional[str], Optional[str]]
def get_tr_id(cls, ...) -> Optional[str]
def upsert_dcf_override(cls, ...) -> Optional[DcfOverride]
def get_dcf_override(cls, ticker: str) -> Optional[DcfOverride]
```

**`repositories/stock_meta_repo.py`** (14건 → 6건)
```python
def upsert_stock_meta(cls, ticker: str, **kwargs) -> Optional[StockMeta]
def get_stock_meta(cls, ticker: str) -> Optional[StockMeta]
def save_financials(cls, ...) -> Optional[Financials]
def get_latest_financials(cls, ticker: str) -> Optional[Financials]
def upsert_api_tr_meta(cls, api_name: str, **kwargs) -> Optional[ApiTrMeta]
def get_api_meta(cls, api_name: str) -> Optional[ApiTrMeta]
def upsert_dcf_override(cls, ...) -> Optional[DcfOverride]
def get_dcf_override(cls, ticker: str) -> Optional[DcfOverride]
```

**`services/base/scheduler_service.py`**
```python
def _run() -> None          # 내부 WebSocket 스레드 함수
def _norm_ticker(t: str) -> str
```

**`services/strategy/trading_strategy_service.py`**
```python
def _norm_ticker(t: str) -> str
```

---

## 최종 lint 결과

| 파일 | 🔴 크리티컬 | 🟡 경고 |
|------|------------|---------|
| `services/market/stock_meta_service.py` | **0** | 4 |
| `repositories/stock_meta_repo.py` | **0** | 6 |
| `services/base/scheduler_service.py` | **0** | 8 |
| `services/strategy/trading_strategy_service.py` | **0** | 15 |
| `services/kis/kis_service.py` | **0** | 5 |
| `services/analysis/financial_service.py` | **0** | 4 |
| `services/market/data_service.py` | **0** | 8 |
| `services/trading/portfolio_service.py` | **0** | 6 |
| `services/notification/report_service.py` | **0** | 4 |
| `services/market/economic_calendar_service.py` | **0** | 3 |
| `repositories/database.py` | **0** | 1 |
| `repositories/settings_repo.py` | **0** | 3 |
| `repositories/trade_history_repo.py` | **0** | 3 |
| `models/ticker_state.py` | **0** | 1 |
| `routers/trading.py` | **0** | 2 |

---

## LLM 판단: 잔여 경고 분류

### stock_meta_service.py (4건)

| 번호 | 판단 | 항목 | 이유 |
|------|------|------|------|
| 1 | [오탐] | `get_all_latest_dcf` 페이징 | 내부 서비스 메서드, API 엔드포인트 아님 |
| 2 | [낮은 우선순위] | `get_session` 타입힌트 | `Session` import 추가 필요, 하위 호환 유틸 메서드 |
| 3 | [적용 제외] | `session_scope` 타입힌트 | `@contextmanager` 데코레이터로 이미 타입 컨텍스트 제공 |
| 4 | [적용 제외] | `session_ro` 타입힌트 | 동상 |

### stock_meta_repo.py (6건)

| 번호 | 판단 | 항목 | 이유 |
|------|------|------|------|
| 1 | [오탐] | `get_all_latest_dcf` 페이징 | 배치 분석용 내부 쿼리, API 아님 |
| 2 | [오탐] | `get_all_dcf_overrides` 페이징 | DCF 오버라이드는 포트폴리오 종목 수만큼 소규모 |
| 3 | [오탐] | `get_kr_individual_stocks` 33줄 | 3줄 초과. ETF 필터링 루프는 단일 필터링 연산 |
| 4 | [낮은 우선순위] | `save_financials` 62줄 | upsert + 13필드 매핑 + EMA 매핑이 하나의 트랜잭션 |
| 5 | [낮은 우선순위] | `get_all_latest_dcf` 51줄 | 복잡한 SQL JOIN + 결과 매핑이 하나의 DB 연산 |
| 6 | [오탐] | `save_market_regime` 34줄 | 4줄 초과. upsert + JSON 직렬화가 단일 트랜잭션 |

### scheduler_service.py (8건)

| 번호 | 판단 | 항목 | 이유 |
|------|------|------|------|
| 1 | [오탐] | `get_all_cached_prices` 페이징 | in-memory dict 반환, DB 쿼리 아님 |
| 2 | [낮은 우선순위] | `_register_scheduled_jobs` 36줄 | APScheduler 잡 등록 테이블, 논리적 단일 작업 |
| 3 | [오탐] | `start` 명칭 | FastAPI 앱 시작 컨벤션과 통일 (`start`가 관용적으로 적합) |
| 4 | [낮은 우선순위] | `manage_subscriptions_async` 93줄 | WebSocket 연결+구독+재연결이 단일 async 이벤트 루프 |
| 5 | [낮은 우선순위] | `check_portfolio_hourly` 36줄 | 포트폴리오 체크+슬랙+동기화가 단일 스케줄 잡 |
| 6 | [낮은 우선순위] | `_refresh_low_tier_prices` 62줄 | 저티어 가격 갱신 배치 루프, 단일 배치 작업 |
| 7 | [낮은 우선순위] | `report_tick_trade_status` 45줄 | 현황 조회+포매팅+슬랙 발송이 하나의 리포트 작업 |
| 8 | [낮은 우선순위] | `_check_vix_spike` 68줄 | VIX 판단+쿨다운+슬랙이 하나의 VIX 감시 작업 |

### trading_strategy_service.py (15건)

모두 함수 길이 경고. 전부 **낮은 우선순위** 또는 **오탐**:
- `_passes_allocation_limits` 84줄 — 포지션 한도 검사 복잡 비즈니스 규칙
- `_execute_collected_signals` 78줄 — 매매 시그널 오케스트레이션
- `calculate_score` 59줄 — 스코어 계산 단일 책임
- `_run_tick_trade` 55줄 — 틱 매매 루프
- `_analyze_stock_v3` 52줄 — 종목 분석 단일 책임
- `_score_technical` 48줄 — 기술적 지표 채점, 분리시 컨텍스트 소실
- `_execute_buy_order` 47줄 — 매수 실행 단일 책임 (분리 완료)
- `_execute_underweight_buys` 47줄 — 비중 미달 종목 매수 배치
- `_execute_overweight_sells` 44줄 — 비중 초과 종목 매도 배치
- `get_waiting_list` 42줄 — 대기 목록 조회+포매팅
- `get_sector_rebalance_status` 42줄 — 섹터 리밸런싱 현황 조회
- `run_sector_rebalance` 40줄 — 섹터 리밸런싱 오케스트레이션
- `run_strategy` 40줄 — 전략 진입점
- `_get_sector_group_weights` 34줄 — 섹터 그룹 가중치 계산
- `sell_all_and_rebuy` 31줄 — 전량 매도+재매수 (1줄 초과)

---

## 추가 수정 불필요 항목 확인 (Sonnet 4.6)

- **N+1 이슈**: `kis_service.py:380` — 토큰 루프 외부 취득 이미 완료
- **`ge=1` 누락**: `routers/trading.py` — `Query(default=50, ge=1, le=1000)` 이미 적용
- **`upsert_many` 타입힌트**: `repositories/settings_repo.py` — `-> None` 이미 적용
- **`update_indicators` 타입힌트**: `models/ticker_state.py` — 이미 적용
