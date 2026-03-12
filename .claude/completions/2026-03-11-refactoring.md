# 완료: 2026-03 리팩토링

## 완료 작업
1. Repository Pattern 도입 — portfolio_repo, settings_repo, trade_history_repo
2. 하드코딩 TR ID 제거 — C-2-1 (stock_meta_service), C-2-2 (stock_ranking_service)
3. analysis_service 함수 분리 (_fetch_price_data 등 6개 함수 추출)
4. DCF 파라미터 설정화 — config.py DCF_* 상수 추가
5. 경고 이슈 개선 — W-3, W-4, W-6, I-1, I-2
6. 문서 작성 — ARCHITECTURE_MAP.md, FUNCTION_REFERENCE.md

## 미해결 (우선순위 높음)
- C-2-4: kis_service.py:142 잔고조회 TR ID 하드코딩
- C-2-5: kis_service.py:371~373 주문 TR ID 하드코딩
- C-2-3: kis_fetcher.py:356 해외 기간별 시세 TR ID 하드코딩
- news_service.py KIS 뉴스 API 미구현
