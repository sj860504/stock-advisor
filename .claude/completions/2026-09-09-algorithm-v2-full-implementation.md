# 2026-09-09 — 감지·매수·매도 사이클 개선 계획 전면 구현

## 요청
> "전체 매도 사이클이랑 매수 사이클, 감지 알고리즘 개선 계획 해봐" → "다 수정해"

계획: `docs/ALGORITHM_IMPROVEMENT_PLAN_2026-09-09.md`. 규칙 요약: `.claude/BUSINESS_LOGIC.md` §5-2.

## 추가 발견 버그 (D0)
매매 루프(`trading_strategy_service._load_macro_and_assets`)와 `/api/analysis/score` 가 `MacroDataSnapshot(**get_macro_data())` 로
스냅샷을 만들어 KOSPI/SPX 변화율이 항상 None → **실제 매매 루프에서 Crash 가드(VIX 제외)·지수 5d boost·per_trade mult 가 전혀 동작하지 않았다.**
5분 signal_cache 잡만 `get_macro_data_snapshot()` 을 써서 UI 에는 boost 가 보였다. → 전부 `get_macro_data_snapshot()` 으로 통일 (COMMON_MISTAKES #14).

## 구현 요약

| 사이클 | 항목 |
|---|---|
| 감지 | D0 스냅샷 통일 · D1 stock/market 점수 분리 + tanh 포화 완화 + DCF 소스 신뢰도 · D2 급락 확인(반등/거래량) · D3 breadth/VIX 급등 장중 Crash · D4 신선도 게이트 · D5 레짐 가중 25/25/20/15/15 + Bull 임계 동적 |
| 매수 | B1 임계 30 통일 + 루프당 신규 2건 + 완화 상한 10 · B2 신뢰도×ATR 사이징(사문 승수 교체) · B3 트랜치 페이싱 · B4 DCA 1일 1단계·기준가 옵션·예비현금 동적 · B5 업종 그룹 30% · B6 재진입(손절가 회복, 매도 후 5일/-5%) |
| 매도 | S1 잔여 trailing 손익분기 하한 · S2 3단계 스케일아웃 + 수익구간별 trailing · S3 score 매도 수익 ≥3% · S4 ATR 손절 · S5 상대약세 50% 정리 · S6 KR 매도 가격 재조회 + 마감 10분 전 병합 |
| 인프라 | 섀도우 모드 · `/api/analysis/strategy-kpi` · `scripts/backtest_replay.py`(운영 코드 재생) · financials `atr_pct/avg_volume_20d`(alembic d4f1a2b3c5e6) · UI 점수 툴팁 |

## 파일

`services/strategy/uptrend_rules.py`(신규), `signal_service.py`, `position_service.py`, `execution_service_v2.py`, `crash_guard_service.py`,
`asset_management_service.py`, `services/market/macro_service.py`, `market_data_service.py`, `data_service.py`, `market_hour_service.py`,
`services/analysis/indicator_service.py`, `financial_service.py`, `services/trading/portfolio_service.py`, `services/base/scheduler_service.py`,
`services/config/settings_service.py`(신규 키 45개), `config.py`, `models/{schemas,ticker_state,stock_meta}.py`,
`repositories/{stock_meta_repo,trade_history_repo}.py`, `routers/{analysis,market}.py`, `static/js/app.js`,
`migrations/versions/d4f1a2b3c5e6_add_atr_volume_to_financials.py`, `scripts/backtest_replay.py`, `tests/test_algorithm_v2.py`.

## ⚠️ 운영 행동 변화 (배포 전 확인)

1. **BUY 임계 40 → 30** (DB `STRATEGY_BUY_THRESHOLD` 가 40 으로 저장돼 있으면 그 값이 우선 — Settings 탭에서 30 으로 맞출 것).
2. 신규 매수는 루프당 2건, 분할 트랜치는 하루 1회(또는 -2% 추가 하락), DCA 는 종목당 하루 1단계.
3. 점수 분포가 바뀐다: tanh + DCF 신뢰도로 BUY 비율이 줄어든다. 첫 주는 `/api/analysis/strategy-kpi` 로 BUY 비율(목표 15~25%)·캡 포화율(<30%) 확인.
4. 폭락(지수 5d < -12%) 시 예비현금 10% 가 0% 로 풀린다.
5. 매도 후 5거래일(또는 -5%) 재매수 차단, 손절 후 재진입은 손절가 +3% 회복 필요.
6. 상대 약세 정리 ON: 90일 이상 보유 + 지수 대비 -20%p + 지수 회복 중이면 50% 매도. 원치 않으면 `STRATEGY_RELATIVE_WEAKNESS_ENABLED=0`.
7. `financials` 마이그레이션 필요 (`./start.sh` 가 alembic upgrade head 실행). ATR/거래량은 다음 일봉 sync 후부터 채워진다 — 그 전엔 ATR 사이징·ATR 손절은 자동으로 비활성(고정 -25%).
8. **권장**: 첫 2주는 `STRATEGY_SHADOW=1` 로 섀도우 운영 후 전환.

## 검증
- `pytest tests/test_algorithm_v2.py tests/test_uptrend_dca.py` → 62 passed.
- `python scripts/backtest_replay.py --synthetic --days 7 --cash 5000000 --mode both` 정상 종료 (합성 데이터).
- 실데이터 백테스트는 원격 환경 이그레스 차단으로 미실행 → 운영 서버에서 `backtest_replay.py --days 30 --dcf-from-db --assume-held-days 120` 권장.
