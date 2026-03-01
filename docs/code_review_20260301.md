# 전체 코드 리뷰 보고서 (2026-03-01)

> lint_fastapi.py 자동 분석 + LLM 의미적 판단 통합 결과
> 대상: routers/, models/, repositories/, services/ 전체

---

## 전체 요약

| 파일 그룹 | 🔴 크리티컬 | 🟡 경고 | 🔵 개선 | LLM 확정 이슈 |
|-----------|-----------|--------|--------|-------------|
| routers/ (7파일) | 0 | 21 | 2 | **3건** (타입힌트 일부, 함수길이 경미) |
| models/ (7파일) | 0 | 27 | 9 | **오탐 대부분** (`__repr__`, Base→Response 패턴) |
| repositories/ (4파일) | 0 | 16 | 4 | **오탐 대부분** (get/set/record 명명 관례) |
| services/analysis/ (8파일) | 1 | 14 | 4 | **3건 확정** (거대함수, N+1) |
| services/base/ (2파일) | 0 | 10 | 1 | **3건 확정** (거대함수) |
| services/config/ (1파일) | 3 | 6 | 2 | **오탐** (Redis 오탐), **1건 확정** (N+1) |
| services/kis/ (3파일) | 0 | 25 | 4 | **4건 확정** (거대함수) |
| services/market/ (10파일) | 37 | 55 | 15 | **6건 확정** (DB직접접근, 거대함수, N+1) |
| services/notification/ (2파일) | 0 | 8 | 2 | **2건 확정** (거대함수, N+1) |
| services/strategy/ (4파일) | 0 | 33 | 13 | **5건 확정** (거대함수) |
| services/trading/ (3파일) | 0 | 11 | 5 | **2건 확정** (거대함수, N+1) |

---

## [LLM 판단] 전체 이슈 판정

### 오탐(False Positive) — 수정 불필요

| 이슈 | 근거 |
|------|------|
| `__repr__` 타입힌트/Docstring 요구 | Python 관례 메서드. `-> str` 추가는 선택사항, docstring 불필요 |
| `BaseModel` 직접 상속 (Base→Response 구조 미적용) | 단순 Response 전용 클래스에 상속 구조 강제는 오버엔지니어링. 필드 재사용이 없으면 불필요 |
| `schemas.py` 35개 도메인 혼재 | 단일 파일 schemas.py는 이 프로젝트의 의도적 패턴. 분리 시 circular import 위험 높음 |
| `settings_service.py` Redis `.set()` TTL 없음 | 프로젝트는 SQLite 사용. `SettingsRepo.set()`은 Redis가 아님 — lint 오탐 |
| `settings_repo.py` `get`/`set` 명명 규칙 | Repository DAO 패턴의 관례명. `get_setting_by_key`로 변경 시 호출부 대규모 수정 필요 |
| `portfolio_repo.py` `save` 명명 | Repository 패턴 관례명 |
| `trade_history_repo.py` `record`/`query` 명명 | 도메인 특화 관례명 (trade를 record한다, query한다) |
| `routers/` 페이징 파라미터 미적용 경고 | `get_weekly_calendar(days=7)`, `get_regime_history(days=30)` 등은 time-based limit이 이미 적용됨. `get_watching_list()`, `get_settings()` 등은 데이터 건수가 물리적으로 제한됨 |
| `routers/auth.py` `login`/`verify` 명명 규칙 | REST API 표준 동사. 변경 불필요 |
| `portfolio.py` 하드 삭제 경고 | 포트폴리오 보유 종목 삭제는 사용자 명시적 행위. Soft Delete 불필요 |

---

### 확정(Confirmed) 이슈 — 우선순위별 목록

#### 🔴 HIGH — 즉시 수정 권장

**1. `stock_meta_service.py` — DB 세션 직접 접근 (35곳)**
- 서비스 레이어가 `session.query()`, `session.add()`, `session.commit()` 등을 직접 호출
- Repository 패턴 위반. `StockMetaRepo` 레이어로 위임해야 함
- 영향: 트랜잭션 관리 분산, 테스트 어려움

**2. `data_service.py:154` — DB 세션 직접 접근**
- 서비스에서 `session.query(Indicators)` 직접 접근
- `IndicatorsRepo` 레이어로 위임 필요

**3. `financial_service.py:307` — DB 세션 직접 접근**
- `session.query(DcfOverride)` 직접 접근
- `DcfOverrideRepo` 레이어로 위임 필요

#### 🟡 MEDIUM — 거대 함수 분리

| 파일 | 함수 | 줄 수 | 배율 |
|------|------|------|------|
| `services/market/macro_service.py` | `_get_market_regime` | 300줄 | **10x** |
| `services/market/macro_service.py` | `calculate_historical_regime` | 265줄 | **8.8x** |
| `services/strategy/trading_strategy_service.py` | `run_sector_rebalance` | 171줄 | 5.7x |
| `services/strategy/trading_strategy_service.py` | `calculate_score` | 169줄 | 5.6x |
| `services/analysis/financial_service.py` | `get_dcf_data` | 119줄 | 4.0x |
| `services/base/scheduler_service.py` | `start` | 110줄 | 3.7x |
| `services/base/scheduler_service.py` | `manage_subscriptions_async` | 93줄 | 3.1x |
| `services/strategy/trading_strategy_service.py` | `_execute_trade_v2` | 94줄 | 3.1x |
| `services/trading/portfolio_service.py` | `sync_with_kis` | 100줄 | 3.3x |
| `services/strategy/trading_strategy_service.py` | `_passes_allocation_limits` | 84줄 | 2.8x |
| `services/notification/report_service.py` | `format_portfolio_report` | 81줄 | 2.7x |
| `services/market/data_service.py` | `get_price_history` | 84줄 | 2.8x |
| `services/market/economic_calendar_service.py` | `get_weekly_calendar` | 78줄 | 2.6x |
| `services/trading/portfolio_service.py` | `analyze_portfolio` | 79줄 | 2.6x |
| `services/base/scheduler_service.py` | `_check_vix_spike` | 68줄 | 2.3x |
| `services/kis/kis_service.py` | `_send_domestic_order` | 73줄 | 2.4x |
| `services/kis/kis_service.py` | `send_overseas_order` | 71줄 | 2.4x |

#### 🟡 MEDIUM — N+1 쿼리/API 호출

| 파일 | 위치 | 내용 |
|------|------|------|
| `services/analysis/stock_ranking_service.py:24` | for 루프 | `KisService.get_overseas_ranking()` 반복 호출 |
| `services/analysis/stock_ranking_service.py:37` | for 루프 | `StockMetaService.get_api_info()` 반복 호출 |
| `services/notification/alert_service.py:70` | for 루프 | `DataService.get_current_price()` 반복 호출 |
| `services/market/data_service.py:390` | for 루프 | `KisService.get_access_token()` 반복 호출 |
| `services/config/settings_service.py:68` | for 루프 | `SettingsRepo.get()` 반복 호출 |

#### 🔵 LOW — 타입 힌트 추가 (선택)

| 파일 | 해당 메서드 |
|------|-----------|
| `analysis_service.py` | `_calculate_trade_score` 반환 타입 `-> tuple[Optional[float], list]` |
| `dcf_service.py` | `save_override` 반환 타입 |
| `ticker_state.py` | `__post_init__`, `update_from_socket`, `recalculate_indicators`, `update_indicators` → `-> None` |
| `database.py` | `init_db`, `get_engine`, `get_session`, `session_scope`, `session_ro` |
| `kis_ws_service.py` | 비동기 메서드 반환 타입 |
| `trading_strategy_service.py` | 다수 메서드 |

---

## 파일별 상세 결과

### routers/

| 파일 | 크리티컬 | 경고 | 판정 |
|------|---------|------|------|
| `alerts.py` | 0 | 0 | ✅ PASS |
| `reports.py` | 0 | 0 | ✅ PASS |
| `analysis.py` | 0 | 5 | 오탐 4건(페이징/경미한길이), 확정 1건(타입힌트) |
| `auth.py` | 0 | 6 | 오탐 5건(명명/Base구조), 확정 1건(login 타입힌트) |
| `market.py` | 0 | 4 | 오탐 4건(페이징: days/limit가 이미 있음) |
| `portfolio.py` | 0 | 3 | 오탐 3건(페이징/소프트삭제) |
| `trading.py` | 0 | 3 | 확정 1건(get_trade_history limit 상한값 없음) |

### models/

| 파일 | 크리티컬 | 경고 | 판정 |
|------|---------|------|------|
| `portfolio.py` | 0 | 0 | ✅ PASS |
| `kis_schemas.py` | 0 | 1 | 오탐(Base→Response 불필요) |
| `schemas.py` | 0 | 20 | 오탐(단일파일 의도적, Base→Response 오버엔지니어링) |
| `settings.py` | 0 | 1 | 오탐(`__repr__` 관례) |
| `stock_meta.py` | 0 | 5 | 오탐(`__repr__` 관례) |
| `ticker_state.py` | 0 | 5 | 확정 2건(타입힌트 누락, update_from_socket 32줄 경미) |
| `trade_history.py` | 0 | 1 | 오탐(`__repr__` 관례) |

### repositories/

| 파일 | 크리티컬 | 경고 | 판정 |
|------|---------|------|------|
| `database.py` | 0 | 7 | 확정 2건(타입힌트 추가 권장), 오탐 5건 |
| `portfolio_repo.py` | 0 | 1 | 오탐(save 명명 관례) |
| `settings_repo.py` | 0 | 4 | 오탐 3건(명명/페이징), 확정 1건(upsert_many 타입힌트) |
| `trade_history_repo.py` | 0 | 3 | 오탐 2건(명명 관례), 확정 1건(타입힌트) |

### services/

**가장 중요한 확정 이슈는 위 [LLM 판단] 섹션 참고.**

---

## 수정 우선순위 로드맵

```
Phase 1 (이번 주): DB 세션 직접 접근 제거
  - stock_meta_service.py → StockMetaRepo 패턴으로 이전
  - data_service.py, financial_service.py → 각 Repo 위임

Phase 2 (다음 주): 최대 거대 함수 분리
  - macro_service._get_market_regime (300줄) → _calc_tech_score, _calc_vix_score 등 분리
  - trading_strategy_service.calculate_score (169줄) → 단계별 헬퍼 분리

Phase 3 (여유 시): N+1 개선 + 타입힌트
  - data_service.py:390 토큰 루프 외부로 이동
  - alert_service.py N+1 배치 조회로 교체
  - 타입힌트 누락 보완
```

---

*생성: Claude Sonnet 4.6 | lint_fastapi.py + LLM 판단 통합*
