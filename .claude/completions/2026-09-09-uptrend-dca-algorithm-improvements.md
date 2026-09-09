# 2026-09-09 — Uptrend DCA 알고리즘 결함 수정 및 개선

## 요청
> "알고리즘 개선해줘. 브랜치 최신으로 변경해야 한다."

브랜치 `claude/algorithm-improvement-lehthi` 를 `origin/develop`(8fafd4d, 6/12) 기준으로 재설정 후 작업.
(기존 브랜치는 3월 `master` 기준 — develop 대비 135커밋 뒤).

## 진단 — Uptrend DCA 알고리즘(기본 ON)의 실제 동작 결함

| # | 영역 | 문제 | 영향 |
|---|---|---|---|
| 1 | **DCA 1단계 사문화** | `_handle_uptrend_dca_signal` → `_execute_trade_v2(is_holding=True)` → `_passes_add_buy_entry` 가 `profit > -5%` 면 차단 | -3% 단계가 실제로는 -5% 에서야 실행. 매 분 시도·실패 반복 |
| 2 | **US 종목 DCA 통화 혼용** | `total_assets = us_total_krw + KRW현금`, `budget(KRW) / price(USD)` | 수량 수천 배 과대 → 미수차단 가드가 'USD 현금 전액' 으로 축소 → 단계 비율(3/4/5%)·max position 15% 무시 |
| 3 | **안전망 손절 부재** | `STRATEGY_UPTREND_STOP_LOSS_PCT(-25)/_DAYS(7)` 설정만 있고 미사용. 7b8c39d 이후 Uptrend 모드는 forced_sell 경로가 전혀 없음 | -60% 종목도 무한 보유 |
| 4 | **Frozen 이 매수를 안 막음** | `is_frozen` 은 DCA 만 차단. score 신규매수·자산관리 budget 매수는 VIX>50 에서도 실행 | 패닉 단계 추가 진입 |
| 5 | **US 종목에 KOSPI tier 적용** | score boost / per_trade mult 가 항상 `kospi_change_5d` | 미장 폭락 시 US 종목 가산 없음, 코스피 폭락 시 US 종목 과대 매수 |
| 6 | **미감시 보유 종목 규칙 불일치** | `_process_unmonitored_holding` 은 레거시 -7% 손절/익절 그대로 | 유니버스 밖 종목만 -7% 에서 손절 |
| 7 | **현금 부족 = 단계 소진** | `budget < price` 면 `dca_done` mark | 다른 종목 매도로 현금 생겨도 재시도 없음 |
| 8 | **dry powder 없음** | 목표현금 0% → 자산관리가 매 루프 score≤60 까지 현금 소진 | 폭락 시 DCA 2.5× 배율을 쓸 현금이 없음 |
| 9 | CrashGuard 캐시 | 60초 시간 캐시 → 매크로 갱신 후 옛 판정 잔존 | 소폭 |

## 수정 (파일별)

| 파일 | 변경 |
|---|---|
| `execution_service_v2.py` | `_check_buy_cash_and_entry_conditions(trigger_reason)` — `dca_stage_*` 는 추매 엔트리·목표현금 게이트 우회. `_execute_buy_order` Frozen 중앙 게이트. `_uptrend_target_cash_ratio()` = max(MIN_CASH, RESERVE) |
| `position_service.py` | DCA 를 KRW 기준으로 통일(`usd_cash` 전달, `price_krw`), 현금부족 시 단계 미소진, 지수 mult 종목 시장 기준. `_should_execute_stop_loss` Uptrend 는 `UPTREND_STOP_LOSS_DAYS`. `_process_unmonitored_holding_uptrend` 신설. `execute_buy_budget(macro_data)` |
| `signal_service.py` | `_score_portfolio`: profit ≤ -25% → `uptrend_stop_loss_hit` forced_sell. `_score_market_context(ticker)` → SPX/KOSPI 분기 |
| `crash_guard_service.py` | `index_5d_tier/index_5d_change/index_label(ticker)`, `per_trade_mult/score_boost(macro, ticker)`, 입력값 키 캐시 |
| `asset_management_service.py` | 목표현금 = 예비현금 규칙 공유, Uptrend 모드 현금확보 매도 스킵, `macro_data` 전달 |
| `settings_service.py` | `STRATEGY_UPTREND_DCA_RESERVE_RATIO` (기본 0.10) 신규 |
| `routers/market.py` | crash-status 에 `spx_tier`, `spx_1d/5d`, 실제 enabled 값 |
| `tests/test_uptrend_dca.py` | 25 케이스 (DB 없이 SettingsService 패치) |

## ⚠️ 행동 변화 (운영 확인 필요)

1. **예비현금 10%**: score 신규매수/budget 매수는 현금 10% 이상일 때만. DCA 는 0% 까지 사용. 예전 풀투자로 되돌리려면 `STRATEGY_UPTREND_DCA_RESERVE_RATIO=0`.
2. **-25% × 7거래일 안전망 손절 활성** (Crash 중 보류). 현재 -25% 이하 보유 종목이 있다면 7거래일 후 매도된다.
3. DCA 1단계(-3%)가 이제 실제로 실행된다 → 추매 빈도 증가.
4. US 종목 DCA 수량이 단계 비율대로 줄어든다 (이전엔 USD 현금 전액).

## 검증

- `pytest tests/test_uptrend_dca.py` 25 passed.
- 사전 존재 실패: `test_indicator_service::…short_series` (develop 에서도 실패), `test_market_separation` (KIS 네트워크 필요) — 본 변경과 무관.

## 운영 적용

```bash
cd /root/stock-advisor && git pull && ./start.sh   # settings 신규 키는 init_defaults 가 자동 삽입
```

## 후속 — 최근 일주일 백테스트 스크립트 (`scripts/backtest_last_week.py`)

원격 작업 환경의 이그레스 정책이 시세 서버(query1/2.finance.yahoo.com, stooq.com, fchart/api.finance.naver.com, api.stlouisfed.org, alphavantage.co)를 전부 403 으로 차단해 실데이터 백테스트는 여기서 실행 불가.
운영 서버에서 실행할 스크립트를 추가하고 `--synthetic` 합성 데이터로 동작만 검증함.

```bash
python scripts/backtest_last_week.py            # 최근 7일, Legacy vs Uptrend(before) vs Uptrend(after)
python scripts/backtest_last_week.py --days 14 --cash 3000000
```
- 일봉(고/저/종가) 기준, 보유 종목 한정 (신규 종목 score 매수 미포함). 하루 1단계 DCA 로 단순화.
- `--csv-dir` 로 미리 받은 CSV 사용 가능 (네트워크 없는 환경).
