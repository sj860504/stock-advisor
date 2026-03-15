# 코드 리뷰 보고서 2026-03-06 (전체 프로젝트)

> 도구: `code-reviewer` 스킬 + `lint_fastapi.py` 정적 분석 + LLM 수동 판단
> 대상: 전체 프로덕션 Python 파일 (~55개, scripts/ tests/ 제외)
> Phase 2 리팩토링 완료 항목 포함 (Priority 1~2)

---

## 전체 요약 (파일별)

### 🔴 크리티컬 확정 건 (Priority 1 — 이미 수정 완료)

| # | 파일 | 이슈 | 상태 |
|---|------|------|------|
| 1 | `trading_strategy_service.py` | 중복 메서드 `_calc_holding_profit` ≈ `_compute_holding_profit_pct` — 버그 위험 | ✅ 수정 완료 |

### 🟡 확정 Warning (Priority 2 — 이미 수정 완료)

| # | 파일 | 이슈 | 상태 |
|---|------|------|------|
| 2 | `trading_strategy_service.py` | logger 모듈 레벨 선언 있으나 12개 메서드에서 재선언 | ✅ 수정 완료 |
| 3 | `macro_service.py` | `print()` → `logger.info()` | ✅ 수정 완료 |
| 4 | `macro_service.py` | `invalidate_cache` 반환 타입 누락 | ✅ 수정 완료 |
| 5 | `macro_service.py` | 중첩함수 `_fetch` 타입 힌트 누락 | ✅ 수정 완료 |
| 6 | `portfolio_service.py` | `rebalance_portfolio`, `_rebalance_logic` 반환 타입 누락 | ✅ 수정 완료 |
| 7 | `macro_service.py` + `portfolio_service.py` | 환율 1400 vs 1350 불일치 → config 상수 통합 | ✅ 수정 완료 |

### 🟡 확정 Warning (Priority 2 — 미수정)

| # | 파일 | 이슈 | 위치 |
|---|------|------|------|
| 8 | `market_data_service.py` | 반환 타입 힌트 10건 누락 (`-> None`) | L38,144,193,198,255,261,292,308,329,340 |
| 9 | `kis_ws_service.py` | 반환 타입 힌트 7건 누락 | L34,67,128,168,193,208 |
| 10 | `settings_service.py` | 반환 타입 힌트 3건 (`-> None`, `-> Optional[Any]`) | L58,75,106 |
| 11 | `alert_service.py` | 반환 타입 힌트 3건 누락 | L23,67,238 |
| 12 | `execution_service.py` | 반환 타입 힌트 3건 누락 | L16,44,69 |
| 13 | `kis_fetcher.py` | 반환 타입 힌트 2건 + 함수 길이 43줄 | L76,100,363 |
| 14 | `stock_ranking_service.py` | 반환 타입 힌트 2건 누락 | L13,54 |
| 15 | `master_data_service.py` | 반환 타입 힌트 2건 누락 | L56,83 |
| 16 | `backtest_service.py` | 반환 타입 힌트 2건 누락 | L20,59 |
| 17 | `dcf_service.py` | 반환 타입 힌트 1건 누락 | L97 |
| 18 | `simulation_service.py` | 반환 타입 힌트 1건 누락 | L29 |
| 19 | `order_service.py` | 반환 타입 힌트 1건 누락 | L76 |
| 20 | `trading_strategy_service.py` | `_handle_score_trade` 63줄 + 파라미터 19개 | L749 |
| 21 | `trading_strategy_service.py` | `_execute_trade_v2` 파라미터 13개 → TradeContext 도입 | L1640 |
| 22 | `kis_service.py` | 토큰 로드 로직 중복 (`_load_cached_token`/`_load_cached_real_token`) | - |

### ✅ 클린 파일 (이슈 없음)

```
config.py                              services/analysis/analysis_service.py
utils/logger.py                        services/analysis/analyzer/dcf_analyzer.py
utils/market.py                        services/analysis/analyzer/financial_analyzer.py
services/base/scheduler_service.py     services/analysis/financial_service.py
services/base/file_service.py          services/analysis/indicator_service.py
services/kis/kis_service.py            services/market/market_overview_service.py
repositories/stock_meta_repo.py        services/market/news_service.py
routers/alerts.py                      services/market/data_service.py
routers/reports.py                     services/market/ticker_service.py
models/portfolio.py
```

---

## 파일별 상세 리뷰

---

### `main.py`

```
📈 검출 요약  🔴 1건 | 🟡 4건 | 🔵 2건
```

**[LLM 판단]**
```
1. [오탐]   L79 — response_model 누락:
            serve_dashboard는 response_class=FileResponse 사용 → HTML 파일 서빙.
            JSON API가 아니므로 response_model 불필요. 오탐.

2. [적용 제외] L16 — lifespan 명칭:
            FastAPI lifespan context manager 관례명. 변경 불필요.

3. [확정 Warning] L54 — auth_middleware 반환 타입:
            → `async def auth_middleware(request: Request, call_next) -> Response:`

4. [적용 제외] L79 — serve_dashboard 반환 타입:
            FileResponse 반환. 타입 추가 시: `-> FileResponse:` 가능하나 낮은 우선순위.
```

---

### `services/strategy/trading_strategy_service.py` (1,883줄)

```
📈 검출 요약  🔴 0건 | 🟡 4건 | 🔵 10건 + LLM 추가 5건
```

**[LLM 판단]**
```
1. [오탐]     L143 — StrategyStateRepo 직접 접근:
              _load_state/_save_state 전용 헬퍼 내부 접근, 정상 패턴.

2. [낮은 우선순위] L119 — N+1 쿼리:
              _migrate_json_to_db() 앱 최초 1회 실행. 운영 핫패스 아님.

3. [확정 Warning] L749 — _handle_score_trade 63줄 + 파라미터 19개:
              → _handle_buy_split / _handle_sell_signal 분리 권장.

4. [확정 Warning] L847 — _check_unmonitored_holdings 46줄:
              → _process_unmonitored_holding(단건) + 루프로 분리.

5. [확정 Critical★ → 수정 완료] 중복 메서드:
              _calc_holding_profit (L1314) 삭제, _compute_holding_profit_pct 단일화.

6. [확정 Warning → 수정 완료] logger 재선언 12건 제거.

7. [확정 Warning] _execute_trade_v2 13개 파라미터 (L1640):
              → TradeContext dataclass 도입 권장.

8. [확정 Info] @classmethod 전면 사용:
              단위 테스트 불가. 장기적으로 인스턴스 DI 검토.

9. [확정 Info] _analyze_stock_v3 (L1386) Dead code 의심:
              실행 흐름 미연결 여부 확인 후 삭제 검토.
```

---

### `services/market/macro_service.py` (817줄)

```
📈 검출 요약  🔴 0건 | 🟡 7건 | 🔵 3건
```

**[LLM 판단]**
```
1. [낮은 우선순위] L61/365/494/729 — 함수 길이:
              각각 단일 책임 오케스트레이터/점수계산. 분리 시 context 파편화.

2. [확정 Warning → 수정 완료] L110 — invalidate_cache 반환 타입 누락.
3. [확정 Warning → 수정 완료] L635 — _fetch 타입 힌트 누락.
4. [확정 Warning → 수정 완료] L68 — print() → logger.info().
5. [확정 Info → 수정 완료] get_exchange_rate() → Config.EXCHANGE_RATE_KRW_USD 참조.
```

---

### `services/market/market_data_service.py`

```
📈 검출 요약  🔴 0건 | 🟡 11건 | 🔵 7건
```

**[LLM 판단]**
```
1. [오탐]   L325 — get_all_states 페이징:
            내부 서비스 메서드 (Router에서 직접 노출 아님). 페이징 불필요.

2. [확정 Warning] L38,144,193,198,255,261,292,308,329,340 — 반환 타입 10건:
            → 모두 `-> None` 추가 (side-effect only 메서드들).
            단, L38 _get_semaphore → `-> asyncio.Semaphore`
```

---

### `services/kis/kis_ws_service.py`

```
📈 검출 요약  🔴 0건 | 🟡 9건 | 🔵 1건
```

**[LLM 판단]**
```
1. [적용 제외] L27 — __init__ 명칭/타입:
              Python 관례 메서드. → None 타입은 추가 가능.

2. [적용 제외] L67 — connect 명칭:
              WebSocket 클라이언트 관례명. 변경 시 외부 라이브러리와 불일치.

3. [적용 제외] L128 — subscribe 명칭:
              WebSocket pub/sub 관례명.

4. [확정 Warning] L34,67,128,168,193,208 — 반환 타입 6건:
              → get_approval_key: `-> Optional[str]`
              → connect, subscribe, handle_message: `-> None`
              → parse_*: `-> Optional[dict]`
```

---

### `services/config/settings_service.py`

```
📈 검출 요약  🔴 3건 | 🟡 5건 | 🔵 2건
```

**[LLM 판단]**
```
1. [오탐] L70/72/109 — Redis .set() TTL 누락:
          SettingsRepo는 SQLite 기반 설정 저장소. Redis 없음.
          `.set(key, value)` 는 DB INSERT/UPDATE 메서드. 오탐.

2. [오탐] L68 — for 루프 내 N+1:
          설정값은 수십 건 이하. 운영 성능 영향 없음.
          (배치 조회는 오버엔지니어링)

3. [오탐] L116 — get_all_settings 페이징:
          설정 항목 고정 (~20건). 페이징 불필요.

4. [확정 Warning] L58,75,106 — 반환 타입 3건:
          → init_defaults: `-> None`
          → get_setting: `-> Optional[Any]`
          → set_setting: `-> Optional[Settings]`
```

---

### `services/trading/portfolio_service.py` (472줄)

```
📈 검출 요약  🔴 0건 | 🟡 4건 | 🔵 3건
```

**[LLM 판단]**
```
1. [낮은 우선순위] L247/296 — 함수 길이:
              단일 책임 분석/계산 흐름. 분리 불필요.

2. [확정 Warning → 수정 완료] L465/470 — 반환 타입 추가.
3. [확정 Info → 수정 완료] DEFAULT_EXCHANGE_RATE 1350→1400 정렬.
```

---

### `services/trading/execution_service.py`

```
📈 검출 요약  🔴 0건 | 🟡 3건 | 🔵 0건
```

**[LLM 판단]**
```
1. [확정 Warning] L16,44,69 — 반환 타입 3건:
          → _get_token: `-> Optional[str]`
          → buy_market_order: `-> bool`
          → get_balance: `-> dict`
```

---

### `services/trading/order_service.py`

```
📈 검출 요약  🔴 0건 | 🟡 2건 | 🔵 0건
```

**[LLM 판단]**
```
1. [낮은 우선순위] L87 — 타 도메인 Repo 직접 접근:
          OrderService → TradeHistoryRepo.record().
          규모상 별도 TradeService 레이어 추가는 오버엔지니어링.

2. [확정 Warning] L76 — record_trade 반환 타입: → `-> Optional[dict]`
```

---

### `services/notification/alert_service.py`

```
📈 검출 요약  🔴 0건 | 🟡 4건 | 🔵 1건
```

**[LLM 판단]**
```
1. [낮은 우선순위] L85 — for 루프 내 DataService 호출:
          조건부 fallback (`if state else DataService.get_current_price(...)`).
          실제 N+1은 state 미존재 시에만 발생. 허용 범위.

2. [확정 Warning] L23,67,238 — 반환 타입 3건: → `-> None`
```

---

### `services/notification/report_service.py`

```
📈 검출 요약  🔴 0건 | 🟡 2건 | 🔵 0건
```

**[LLM 판단]**
```
1. [낮은 우선순위] L87/178 — 함수 길이 44/63줄:
          format_hourly_gainers, format_portfolio_report 는 문자열 포맷팅 전담 함수.
          포맷 단계 분리 시 오히려 가독성 저하.
```

---

### `services/kis/fetch/kis_fetcher.py`

```
📈 검출 요약  🔴 0건 | 🟡 3건 | 🔵 3건
```

**[LLM 판단]**
```
1. [확정 Warning] L76,100 — 반환 타입 2건:
          → _throttle_request: `-> None`
          → _get_with_retry: `-> requests.Response`

2. [낮은 우선순위] L363 — fetch_overseas_daily_price 43줄:
          날짜 범위 생성 + 페이지 반복 + 데이터 조립 단일 흐름. 분리 시 context 파편화.
```

---

### `services/market/master_data_service.py`

```
📈 검출 요약  🔴 0건 | 🟡 2건 | 🔵 3건
```

**[LLM 판단]**
```
1. [확정 Warning] L56,83 — 반환 타입:
          → get_kospi_master: `-> pd.DataFrame`
          → get_kosdaq_master: `-> pd.DataFrame`
```

---

### `services/market/market_hour_service.py`

```
📈 검출 요약  🔴 0건 | 🟡 1건 | 🔵 0건
```

**[LLM 판단]**
```
1. [낮은 우선순위] L145 — is_strategy_window_open 42줄:
          장/연장 시간대 복수 조건 단일 함수. 분리 시 context 파편화.
```

---

### `services/strategy/backtest_service.py`

```
📈 검출 요약  🔴 0건 | 🟡 2건 | 🔵 0건
```

**[LLM 판단]**
```
1. [확정 Warning] L20,59 — 반환 타입:
          → run_rsi_backtest: `-> dict`
          → _simulate: `-> dict`
```

---

### `services/strategy/scanner_service.py`

```
📈 검출 요약  🔴 0건 | 🟡 1건 | 🔵 0건
```

**[LLM 판단]**
```
1. [오탐] L49 — for 루프 내 KisService.get_overseas_ranking():
          거래소별 순위 조회 API (NASD, NYSE, AMEX 각 1건).
          KIS API는 거래소 배치 조회 미지원. 순차 호출이 유일한 방법.
```

---

### `services/strategy/simulation_service.py`

```
📈 검출 요약  🔴 0건 | 🟡 1건 | 🔵 0건
```

**[LLM 판단]**
```
1. [확정 Warning] L29 — 반환 타입: → `-> dict`
```

---

### `services/analysis/stock_ranking_service.py`

```
📈 검출 요약  🔴 0건 | 🟡 3건 | 🔵 0건
```

**[LLM 판단]**
```
1. [오탐] L24 — for 루프 내 KisService.get_overseas_ranking():
          scanner_service 동일. API 제약상 순차 호출 불가피.

2. [확정 Warning] L13,54 — 반환 타입 2건: → `-> None`
```

---

### `services/analysis/dcf_service.py`

```
📈 검출 요약  🔴 0건 | 🟡 1건 | 🔵 0건
```

**[LLM 판단]**
```
1. [확정 Warning] L97 — save_override 반환 타입: → `-> None`
```

---

### `services/kis/kis_service.py` (580줄)

```
✅ lint 이슈 없음
```

**[LLM 판단] 추가 발견:**
```
1. [확정 Info] _load_cached_token / _load_cached_real_token 코드 중복:
              두 함수가 토큰 파일 경로만 다르고 로직 동일.
              → _load_cached_token(cls, path: str, ...) 공통 메서드 추출 가능.
```

---

### `services/base/scheduler_service.py` (638줄)

```
✅ 모든 컨벤션 준수
```

**[LLM 판단]**: 구조 우수. 잡 등록 분리, 티어 분류, WS 구독 분리 등 헬퍼 패턴 잘 적용됨.

---

### `services/market/economic_calendar_service.py`

```
📈 검출 요약  🔴 0건 | 🟡 0건 | 🔵 3건 (도큐스트링 Info)
```

**[LLM 판단]**: 구조 우수. 🔵 3건은 내부 헬퍼 docstring — Info 수준으로 미수정 허용.

---

### `services/market/stock_meta_service.py`

```
📈 검출 요약  🔴 0건 | 🟡 0건 | 🔵 1건 (docstring)
```

**[LLM 판단]**: Info 수준, 수정 불필요.

---

### 라우터 파일들

| 파일 | lint 결과 | LLM 판단 |
|------|-----------|---------|
| `routers/auth.py` | 🟡5 🔵2 | 모두 오탐: login/verify → REST 표준명, Base 상속 강제는 오버엔지니어링 |
| `routers/market.py` | 🟡4 | 낮은 우선순위: days 파라미터로 범위 제한됨, 내부 API |
| `routers/analysis.py` | 🟡2 | 낮은 우선순위: DcfListResponse 이미 구조화된 응답, update_dcf_override 22줄은 허용범위 |
| `routers/portfolio.py` | 🟡3 | 낮은 우선순위: 내부 API, soft delete는 도메인 요구사항 아님 |
| `routers/trading.py` | 🟡2 | 낮은 우선순위: 내부 API |
| `routers/logs.py` | 🟡1 | 낮은 우선순위: lines 파라미터로 이미 범위 제한 |
| `routers/alerts.py` | ✅ | 클린 |
| `routers/reports.py` | ✅ | 클린 |

---

### 리포지토리 파일들

| 파일 | lint 결과 | LLM 판단 |
|------|-----------|---------|
| `repositories/stock_meta_repo.py` | ✅ | 클린, session_scope 패턴 우수 |
| `repositories/settings_repo.py` | 🟡3 | 오탐: `get`/`set`은 설정 저장소 표준 메서드명, `get_all`은 소량 데이터 |
| `repositories/trade_history_repo.py` | 🟡3 | 오탐: `record`/`query`는 트레이드 히스토리 Repo 관례명. `_apply_filters` 반환 타입만 확정 |
| `repositories/strategy_state_repo.py` | 🟡2 | 오탐: `load`/`save`는 상태 영속성 Repo 관례명 |
| `repositories/portfolio_repo.py` | 🟡1 | 오탐: `save`는 포트폴리오 저장 관례명 |
| `repositories/database.py` | 🔵3 | Info: docstring 누락 3건 — 수정 불필요 |

---

### 모델 파일들

| 파일 | lint 결과 | LLM 판단 |
|------|-----------|---------|
| `models/schemas.py` | 🟡20→5확정 🔵3 | 오탐: 도메인 분리 강제/Base 상속 강제. 확정: to_dict docstring 3건 |
| `models/stock_meta.py` | 🟡5 🔵5 | 적용 제외: `__repr__` 전부 — Python 관례 메서드 |
| `models/portfolio.py` | ✅ | 클린 |
| `models/trade_history.py` | 🟡1 🔵1 | Info |
| `models/strategy_state.py` | 🔵1 | Info |
| `models/settings.py` | 🟡1 🔵1 | Info |
| `models/kis_schemas.py` | 🟡1 | Info |

---

## 전체 확정 이슈 카운트 (오탐/적용 제외 제거 후)

| 등급 | 수정 완료 | 미수정 | 합계 |
|------|----------|--------|------|
| 🔴 크리티컬 | 1 | 0 | 1 |
| 🟡 경고 | 6 | 40+ | 46 |
| 🔵 개선 (Info) | - | ~10 | ~10 |

> 🟡 40건 대부분은 `-> None` / `-> dict` / `-> Optional[str]` 반환 타입 힌트 추가

---

## 리팩토링 우선순위 로드맵

### ✅ Priority 1 — 완료 (버그 위험)

```
① trading_strategy_service.py: _calc_holding_profit 삭제, 단일 메서드로 통일
```

### ✅ Priority 2 — 완료 (코드 품질)

```
② logger 재선언 12건 제거 (trading_strategy_service.py)
③ macro_service.py: print→logger, 반환 타입, _fetch 타입 힌트
④ portfolio_service.py: 반환 타입 추가
⑤ config.py: EXCHANGE_RATE_KRW_USD 상수 추가
```

### Priority 3 — 반환 타입 힌트 일괄 추가 (2~3시간)

```
⑥ market_data_service.py: -> None 10건
⑦ kis_ws_service.py: 6건
⑧ settings_service.py: 3건
⑨ alert_service.py: 3건
⑩ execution_service.py: 3건
⑪ kis_fetcher.py: 2건
⑫ stock_ranking_service.py: 2건
⑬ master_data_service.py: 2건
⑭ backtest_service.py: 2건
⑮ dcf_service.py, simulation_service.py, order_service.py: 각 1건
```

### Priority 4 — 설계 개선 (반나절~1일)

```
⑯ TradeContext dataclass 도입:
   _execute_trade_v2 (13개 파라미터), _process_single_signal (20개 파라미터)

⑰ _handle_score_trade 분리:
   _handle_buy_split → 분할매수
   _handle_sell_signal → 점수 매도

⑱ kis_service.py 토큰 로드 공통화:
   _load_cached_token(path) 단일 메서드

⑲ trade_history_repo.py _apply_filters 반환 타입 추가
```

### Priority 5 — 장기 아키텍처 (별도 스프린트)

```
⑳ trading_strategy_service.py 모듈 분해:
   signal_service.py     ← calculate_score, _score_* 함수들
   execution_service.py  ← _execute_trade_v2, 매수/매도 주문
   position_service.py   ← 분할매수, 쿨다운, 틱매매

㉑ @classmethod → 인스턴스 기반 DI (테스트 가능성 확보)
```

---

*리뷰 도구: code-reviewer 스킬 + lint_fastapi.py + LLM 수동 분석*
*생성일: 2026-03-06 (전체 프로젝트 버전)*
