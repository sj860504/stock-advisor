# 완료: 전체 프로젝트 코드 리뷰 + Priority 1~4 리팩토링

**날짜**: 2026-03-06 (세션 2건에 걸쳐 완료)

## 수행 내용

### Phase 1: 코드 리뷰 보고서 작성 (전체 프로젝트)
- **~55개 프로덕션 파일** 전체 `lint_fastapi.py` 정적 분석 + LLM 판단
- 보고서 저장: `docs/code_review_20260306.md`
- scheduler_service, kis_service, stock_meta_repo → 이슈 없음 (✅)
- trading_strategy_service, macro_service → 주요 이슈 발견

### Phase 2: Priority 1~2 리팩토링

#### Priority 1 (버그 위험)
- `trading_strategy_service.py`: 중복 메서드 `_calc_holding_profit` 삭제
  - `calculate_score()`에서 `_compute_holding_profit_pct()` 단일 메서드로 통일

#### Priority 2 (코드 품질)
- `trading_strategy_service.py`: logger 재선언 12건 제거 (모듈 레벨 재사용)
- `macro_service.py`: `print()` → `logger.info()`, `invalidate_cache() -> None`, `_fetch(key: str) -> tuple`
- `portfolio_service.py`: `rebalance_portfolio/`_rebalance_logic` → `dict` 반환 타입 추가
- `config.py`: `EXCHANGE_RATE_KRW_USD = 1400` 상수 추가 (환율 단일화)
- `macro_service.py`: `get_exchange_rate()` → `Config.EXCHANGE_RATE_KRW_USD` 참조
- `portfolio_service.py`: `DEFAULT_EXCHANGE_RATE` 1350 → 1400 정렬

### Phase 3: Priority 3 - 반환 타입 힌트 (12개 파일)
- `market_data_service.py`: 10개 반환 타입 추가
- `kis_ws_service.py`: 7개 반환 타입 추가
- `settings_service.py`: `Optional` 임포트 + 3개 반환 타입
- `alert_service.py`: 3개 반환 타입
- `execution_service.py`: 3개 반환 타입
- `stock_ranking_service.py`, `backtest_service.py`, `master_data_service.py`
- `dcf_service.py`, `simulation_service.py`, `order_service.py`, `kis_fetcher.py`
- **모두 `ast.parse` 신택스 통과 확인**

### Phase 4: Priority 4 - 설계 개선 (3개 항목)
- `trade_history_repo.py`: `_apply_filters` → `-> Query` 타입 추가, `from sqlalchemy.orm import Query` 임포트
- `kis_service.py`: `_load_cached_token` / `_load_cached_real_token` 공통 로직을 `_load_token_from_file(path, is_real)` 메서드로 추출
- `trading_strategy_service.py`: `_handle_score_trade`(63줄, 19파라미터)를 3개 메서드로 분리:
  - `_handle_buy_split` (L737): 신규/분할 매수 로직
  - `_handle_sell_signal` (L793): 점수 기반 매도 로직
  - `_handle_score_trade` (L810): 2개 핸들러로 위임하는 dispatcher

## 미완료 → Priority 5 (별도 스프린트)
- `TradeContext` dataclass 도입 (`_execute_trade_v2` 17개 파라미터 → 구조체화)
- `trading_strategy_service.py` 모듈 분해: signal_service / execution_service_v2 / position_service

## 신택스 검증
- 전체 수정 파일 `ast.parse` 통과 확인
