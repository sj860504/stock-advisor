# Completion: Documentation Update (develop-1 post-merge)

**Date**: 2026-03-12
**Task**: 함수 목록 + 비즈니스 로직 + 프론트-백 관계 문서화

## 완료 항목

| 파일 | 작업 | 상태 |
|------|------|------|
| `.claude/FUNCTION_REFERENCE.md` | 누락 서비스 6개 + 레포 2개 + ORM 1개 추가 | ✅ |
| `.claude/ARCHITECTURE_MAP.md` | 전체 교체 (구버전 003_quant → 현행 구조) | ✅ |
| `.claude/BUSINESS_LOGIC.md` | 신규 생성 (비즈니스 로직 전체) | ✅ |
| `.claude/API_REFERENCE.md` | 신규 생성 (프론트-백 엔드포인트 매핑) | ✅ |
| `docs/INDEX.md` | 3개 신규 문서 링크 추가 | ✅ |

## 추가된 내용

### FUNCTION_REFERENCE.md
- Section 4: `signal_service.py`, `position_service.py`, `execution_service_v2.py`, `sector_rebalancer_service.py`, `backtest_service.py`
- Section 7: `kis_fetcher.py`
- Section 8: `stock_meta_repo.py`, `strategy_state_repo.py`
- Section 9: `StrategyState` ORM 모델
- `trading_strategy_service.py` obsolete 내용 정리 (오케스트레이터 역할 명시)

### BUSINESS_LOGIC.md (신규)
- 전략 실행 플로우 (run_strategy 전체 시퀀스)
- 점수 계산 로직 [A]~[H] 컴포넌트
- 분할 매수/매도 (SplitOrderState, SplitSellOrderState)
- 손절/익절/추매 쿨다운 로직
- 현금/섹터 비중 관리
- 시장 레짐 판정 (5개 컴포넌트)
- DCF 2단계 모델
- 틱 트레이딩 플로우
- 스케줄러 작업표 14개
- 주요 설정값 전체 (BUY_THRESHOLD, SELL_THRESHOLD 포함)

### API_REFERENCE.md (신규)
- /api/trading (13개), /api/market (9개), /api/analysis (10개), /api/portfolio (8개)
- JWT 인증 흐름
- UI 탭별 API 매핑표
- Request/Response 스키마 예시

## 검증 결과

1. ✅ FUNCTION_REFERENCE.md에서 signal_service.py 검색 → 내용 있음
2. ✅ ARCHITECTURE_MAP.md에서 repositories/ 구조 확인
3. ✅ BUSINESS_LOGIC.md에서 BUY_THRESHOLD, SELL_THRESHOLD 포함
4. ✅ API_REFERENCE.md에서 4개 주요 라우터 엔드포인트 24회 참조
5. ✅ docs/INDEX.md에 3개 새 문서 링크 확인 (10줄)
