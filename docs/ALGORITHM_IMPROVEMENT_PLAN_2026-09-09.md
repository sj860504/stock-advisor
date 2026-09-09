# 매매 알고리즘 개선 계획 — 감지 · 매수 · 매도 사이클 (2026-09-09)

기준 코드: `develop` 8fafd4d + `claude/algorithm-improvement-lehthi` (Uptrend DCA 결함 수정 802540f).
근거 데이터: 저장소 DB 스냅샷(2026-06-08~12) `ticker_signal_cache` 199건, 보유 23종목(전부 KR).

---

## 0. 현황 진단 — 숫자로 보는 문제

| 관찰 | 수치 (6/12 스냅샷) | 의미 |
|---|---|---|
| KR 유니버스 점수 중앙값 | **29** (99종목 중 52개가 ≤30) | 절반 이상이 동시에 BUY — 임계값이 선별 기능을 상실 |
| DCF_deviation 캡(±25) 포화 | 175건 중 **137건(78%)** | "비율 점수"가 실제로는 ±25 이진 스위치. 한 컴포넌트가 매수/매도 방향을 결정 |
| RSI_deviation 캡(±15) 포화 | 191건 중 113건(59%) | 같은 문제 |
| KOSPI_5d_boost 적용 | 199건 중 173건 (평균 -7.8) | 시장 컴포넌트는 **전 종목에 동일 상수**로 더해짐 → 종목 간 순위엔 무영향, 분포만 통째로 이동 |
| BUY 임계 | config.py 40 / 문서 30 / 자산관리 완화 최대 60 | 세 값이 다르고, 자산관리는 중립 종목(59점)까지 매수 |
| 매수 수량 승수 | `score ≥ 90 → 2×, ≥ 80 → 1.5×` | BUY 는 score ≤ 30~40 에서만 발생 → **승수는 매수 경로에서 절대 발동하지 않는 사문 코드** |
| 분할 매수 3회 | 트랜치가 쿨다운 없이 매 루프 실행 | 1분 간격 3회 = 3분 만에 전량 매수. 분할의 시간 분산 효과 없음 |
| 가격 신선도 | `is_ready` 만 검사, `last_updated` 경과 시간 미검사 | 웹소켓 끊김·폴링 실패 시 옛 가격으로 매매 가능 |
| Crash 감지 | 일봉 종가 기준 1d/5d (yfinance 5분 캐시), 매크로 1시간 캐시 | 장중 -5% 급락은 **당일 종가가 나올 때까지 감지 불가** |

> 결론: 결함 수정(802540f) 이후에도 남은 구조적 문제는 (1) 점수가 포화·시장 상수에 지배되어 종목 선별력이 낮고, (2) 매수 사이징·페이싱 로직이 실질적으로 꺼져 있으며, (3) 감지가 일봉·1시간 주기라 장중 충격에 늦다는 것이다.

---

## 1. 감지(Detection) 사이클

### D1. 점수 컴포넌트 재설계 — 포화 제거, 종목 요소와 시장 요소 분리
- **문제**: `_score_dcf` `int(-undervalue_pct)` 를 ±25 로 클램프 → 25% 이상 저평가면 전부 -25. RSI 도 동일.
  시장 컴포넌트(VIX/FNG/Regime/지수 5d)는 모든 종목에 같은 값 → 순위 정보 0.
- **제안**
  1. 종목 점수 `stock_score` = base 50 + DCF + RSI + change + EMA200 + 포트폴리오(보유분) 만으로 계산.
  2. 시장 컴포넌트는 **임계값 조정치**로 분리: `buy_thr = BUY_THRESHOLD + market_adj`, `sell_thr = SELL_THRESHOLD + market_adj` (market_adj = VIX+FNG+Regime+지수 boost 합, 범위 ±20).
     → 공포장이면 임계가 올라가 더 많이 사고, 과열장이면 내려가 더 쉽게 판다. 동작은 지금과 같지만 "무엇이 시장 효과인지" 로그·UI 에서 분리되어 튜닝 가능.
  3. 포화 완화: 선형 클램프 대신 `cap × tanh(x / scale)` (DCF scale 20%, RSI scale 15). 40% 저평가와 25% 저평가가 구별되게.
  4. DCF 신뢰도 가중: 소스별 계수 (override 1.0 / EPS CAGR 0.9 / FCF 0.8 / analyst target 0.6 / EPS×PER 0.5 / KIS 0.4) 를 `_dcf_input_by_ticker` 에 함께 저장하고 DCF 점수에 곱한다. 애널리스트 3명 미만이면 analyst 폴백 스킵(기존 개선계획 9-2).
- **파일**: `signal_service.py` `_apply_score_components`, `_score_dcf`, `_score_rsi`; `dcf_service.py` 소스 태깅.
- **검증**: 스냅샷 199건 재계산 → BUY 비율 15~25% 목표, 컴포넌트별 at_cap 비율 < 30%.

### D2. 급락 매수 편향(falling knife) 완화
- **문제**: `change_deviation` 1% = 3점, -5% 갭하락이면 -15. 반등 확인 없이 무릎에서 사는 구조. 6/8 매매 기록의 신규 매수 2건(005380, 402340)이 모두 change -6.7/-7.9% 트리거.
- **제안**: 급락 가산은 (a) 당일 저가 대비 회복률 ≥ 30% 또는 (b) 거래량 ≥ 20일 평균 1.5× 일 때만 전액, 그 외 절반. 이를 위해 `TickerState` 에 `avg_volume_20d`(일봉 sync 시 계산) 추가.
- **파일**: `signal_service.py` `_score_technical`, `data_service.py` sync, `models/ticker_state.py`.

### D3. 장중 충격 감지 (Intraday Crash Detector)
- **문제**: `CrashGuardService` 는 `macro.kospi_change_1d/5d` (일봉) + VIX 에 의존. 장중 급락은 종가 확정 후에야 Crash. 매크로 캐시 1시간.
- **제안**
  1. **유니버스 breadth**: `MarketDataService.get_all_states()` 의 실시간 `change_rate` 로 시장별 (하락 종목 비율, 중앙값 등락률) 을 매 루프 계산 → `breadth_1m`. KR 유니버스 중앙값 ≤ -3% AND 하락 비율 ≥ 80% → intraday Crash.
  2. KIS 지수 현재가 API(코스피/코스닥, S&P 선물 대용 SPY) 로 장중 지수 변화율 보강. yfinance 캐시는 폴백.
  3. VIX 는 30분 spike 잡 → Crash 판정에 `vix_change_1d ≥ +20%` 조건 추가(수준 35 미달이어도 급등이면 Crash).
- **파일**: `crash_guard_service.py` (`get_status` 입력에 breadth 추가), `market_data_service.py` (`compute_breadth`), `kis_fetcher.py` (지수 현재가).
- **검증**: 6/1~6/8 급락일 재현 — 장중 몇 분에 Crash 전환되는지 로그.

### D4. 데이터 신선도 게이트
- **문제**: `_collect_trading_signals` 가 `is_ready` 만 확인. `last_updated` 가 30분 전이어도 매매.
- **제안**: HIGH tier 는 `now - last_updated > 3분`, LOW tier 는 `> 10분` 이면 신호 생성 스킵 + 카운터 로그. 매도(손절·트레일링)는 주문 직전 현재가 재조회(US 는 이미 함, KR 도 `fetch_price` 추가).
- **파일**: `signal_service.py` `_apply_hard_gates`, `execution_service_v2.py` `_refresh_us_price` → `_refresh_price`.

### D5. 레짐 판정 보완 (기존 개선계획 8 항목 중 미구현분)
- Bull 임계 동적화(`_get_bull_threshold`: 직전 Bear 1개월 67, 2개월 70) — Bear 탈출 직후 Bull 진입 방지.
- 컴포넌트 가중 재조정(Technical 25 / VIX 25 / F&G 20 / FRED 15 / 복합 15) — 지연 지표 비중 축소.
- **파일**: `macro_service.py`. 이미 구현된 것(30일 blending, extreme_fear Bear 강제, phase ±15)은 유지.

---

## 2. 매수 사이클

### B1. 임계값 통일 + 랭킹 기반 선별
- **문제**: BUY 임계 40(config) / 30(문서) / 자산관리 완화 60. KR 절반이 BUY.
- **제안**: `STRATEGY_BUY_THRESHOLD=30` 으로 통일(config.py 기본값 수정). 매 루프 신규 매수는 **임계 통과 종목 중 점수 하위 N개**(`STRATEGY_MAX_NEW_BUYS_PER_LOOP`, 기본 2) 만 실행. 자산관리 완화 상한 `GAP_RELAX_MAX` 20 → 10 (최대 40).
- **파일**: `config.py`, `position_service.py` `_execute_collected_signals` (정렬 후 신규 매수 카운터), `asset_management_service.py`.

### B2. 매수 사이징 — 사문화된 승수 교체
- **문제**: `_calculate_buy_quantity` 의 `score ≥ 90/80` 승수는 매수에서 발동 불가.
- **제안**: 신뢰도 승수를 BUY 방향으로 뒤집기: `score ≤ 10 → 1.5×`, `≤ 20 → 1.25×`, 그 외 1.0×. 추가로 변동성 사이징: `per_trade × min(1.5, max(0.5, 2% / ATR14%))` — 변동성 큰 종목은 작게, 작은 종목은 크게. ATR 은 일봉 sync 에서 계산해 `TickerState.atr_pct` 에 저장.
- **파일**: `execution_service_v2.py` `_calculate_buy_quantity`, `indicator_service.py` (ATR), `data_service.py`.

### B3. 분할 매수 페이싱
- **문제**: 트랜치가 매 루프 실행 → 3분 내 완료.
- **제안**: 다음 트랜치 조건 = (전 트랜치 대비 -2% 이상 하락) OR (다음 거래일). `SplitOrderState` 에 `last_tranche_price/date` 추가. 만료 5일 유지.
- **파일**: `position_service.py` `_execute_split_tranche`, `models/schemas.py`.

### B4. DCA 정교화 (Uptrend)
- 같은 종목 **하루 최대 1단계**(현재는 다음 루프에 바로 다음 단계 가능 — 갭하락 -16% 시 3분 안에 3/4/5% 전부 투입).
- 단계 임계를 **평균단가가 아닌 최초 진입가** 기준으로 할지 결정: 시뮬레이션(`simulate_uptrend_dca.py`)은 평균단가 기준으로 검증됨. 최초 진입가 기준은 더 공격적 → `dca_done[ticker]["ref_price"]` 에 저장해 옵션(`STRATEGY_UPTREND_DCA_REF=avg|entry`)으로 A/B.
- 예비현금 동적화: 평시 10%, 지수 tier3 이상(5d < -12%) 이면 0% — 폭락 때는 예비를 전부 쓴다.
- **파일**: `position_service.py` `_handle_uptrend_dca_signal`, `execution_service_v2.py` `_uptrend_target_cash_ratio(macro)`.

### B5. 집중 리스크 — 상관 클러스터 한도
- **문제**: 섹터 로직 전면 제거 후 종목당 15% 만 남음. 보유 23종목 중 조선 3(009540/010140/042660), 금융 5, 2차전지 3 → 같은 충격에 동시 DCA.
- **제안**: `stock_meta.industry` 기반 경량 그룹 한도 `STRATEGY_MAX_GROUP_RATIO=0.30`. 그룹 초과 시 신규 매수·DCA 예산을 남은 한도로 축소(차단 아님). 60일 수익률 상관 ≥ 0.8 클러스터를 주 1회 계산해 그룹 보정(2단계).
- **파일**: `execution_service_v2.py` (그룹 노출 계산), `stock_meta_repo.py`.

### B6. 재진입 규칙
- panic_lock 3일 후 재진입은 `RSI < 30` 만 조건. 제안: 손절가 대비 +3% 회복 확인 추가(바닥 확인).
- 부분익절/전량매도 후 같은 종목 재매수: 매도가 대비 -5% 이하 또는 5거래일 경과.

---

## 3. 매도 사이클

### S1. 잔여분 trailing 에 손익분기 하한
- **문제**: +10% 부분익절 후 잔여 50% 는 고점 -10% trailing → 고점이 부분익절가 근처면 **-1% 손실에 잔여분 매도** 가능. "손실 확정 차단" 원칙과 충돌.
- **제안**: trailing 매도가 = `max(remaining_high × 0.9, buy_price × 1.01)`. 즉 잔여분은 최소 +1% 위에서만 trailing 매도, 그 밑이면 보유 유지(DCA 규칙으로 복귀).
- **파일**: `position_service.py` `_handle_uptrend_remaining_trailing`.

### S2. 다단계 스케일아웃
- **문제**: 부분익절 1회(+10%) 뒤엔 trailing 만. +40% 급등 종목도 -10% 트레일링 한 번에 전량.
- **제안**: `STRATEGY_UPTREND_SCALE_OUT="10:0.5,20:0.5,35:0.5"` — +10% 에 50%, +20% 에 잔여 50%, +35% 에 잔여 50%. 수익 구간별 trailing 폭 축소(+20% 이상 -7%, +35% 이상 -5%). `partial_take_done` 을 bool → 단계 인덱스로 확장(JSON 호환).
- **파일**: `position_service.py`, `models/schemas.py` (UserState.partial_take_done 타입).

### S3. Uptrend 모드의 score 매도 제한
- **문제**: score ≥ 70 매도가 Uptrend 에서도 활성 → +2% 종목이 RSI·regime 과열로 팔림. 수수료/세금 감안하면 무의미하거나 손실.
- **제안**: Uptrend 에서 score 매도는 `profit ≥ STRATEGY_UPTREND_SCORE_SELL_MIN_PROFIT (기본 3%)` 일 때만. 손실 구간에서는 score 매도 금지 (안전망 손절만).
- **파일**: `position_service.py` `_route_signal` (score-based 분기 직전 가드).

### S4. 안전망 손절 변동성 반영
- **문제**: -25% 고정. 변동성 낮은 금융주 -25% 와 2차전지 -25% 는 의미가 다름.
- **제안**: `stop = max(-35%, min(-15%, -6 × ATR14%))`. ATR 은 B2 와 공유. 7거래일 연속 룰·Crash 보류 유지.

### S5. 상대 약세 정리 (시간 기반)
- **문제**: "우상향" 가정은 지수엔 맞아도 개별 종목엔 틀릴 수 있음. -20% 에서 6개월 횡보하는 종목이 DCA 로 15% 한도까지 커진 채 묶임.
- **제안**: 보유 90거래일 이상 AND 종목 수익률 − 지수 수익률 ≤ -20%p AND 최근 20일 지수는 상승 → "상대 약세" 플래그 → 50% 정리 후 재평가. `TradeHistoryRepo` 의 최초 매수일로 보유기간 산출.
- **파일**: `position_service.py` (신규 `_handle_relative_weakness`), `trade_history_repo.py`.

### S6. 매도 실행 품질
- KR 매도도 주문 직전 현재가 재조회(D4). 미체결 매도는 다음 루프에서 가격 재산정 후 재주문(현재는 pending 이면 스킵만).
- 장 마감 10분 전에는 분할 매도 잔여 트랜치를 합쳐 1회로 처리(`SELL_SPLIT` 당일 미완 방지).
- 문서 불일치 정리: 자산관리 매도 최소수익 문서 1% / 코드 2% → 코드 기준으로 문서 수정.

---

## 4. 로드맵

| Phase | 기간 | 항목 | 위험 | 검증 |
|---|---|---|---|---|
| **1. 저위험 정합성** | 1주 | B1 임계 통일·랭킹, B2 승수 교체, B3 트랜치 페이싱, D4 신선도 게이트, S1 손익분기 하한, S3 score 매도 수익 하한, S6 문서 정리 | 낮음 (기존 의도 복원) | 단위 테스트 + `backtest_last_week.py` 확장 |
| **2. 감지 재설계** | 2주 | D1 점수 분리·포화 완화·DCF 신뢰도, D2 급락 조건부, D3 breadth/장중 Crash, D5 레짐 보완 | 중간 (분포 변화) | 스냅샷 199건 재계산 리포트, 6/1~6/8 재현, **섀도우 모드 2주**(DEV_MODE 로그만) |
| **3. 사이징·리스크** | 2주 | B2 ATR 사이징, B4 DCA 정교화·예비현금 동적화, B5 그룹 한도, S2 스케일아웃, S4 변동성 손절, S5 상대약세 | 중간 | 2020 코로나 백테스트 재실행(`backtest_2020_corona.py` 에 신규 규칙 반영), 파라미터 그리드 |

### 공통 인프라 (Phase 1 과 병행)
1. **백테스트 엔진 통합**: `scripts/` 에 흩어진 시뮬(2020, 6월, last_week)을 `services/strategy/backtest_service.py` 로 통합해 **실제 `SignalService.calculate_score` / `PositionService._route_signal` 을 호출**하는 리플레이 방식으로 전환. 시뮬 코드와 운영 코드의 규칙 불일치(현재 시뮬은 규칙을 별도 구현)를 없앤다. 일봉 + 유니버스 스냅샷 입력, KIS 주문은 목.
2. **KPI 대시보드**: 루프별 BUY 비율, 컴포넌트 at_cap 비율, Crash/Frozen 시간, DCA 단계별 체결, 부분익절/트레일링 실현 P&L 을 `ticker_signal_cache` 옆 테이블에 일별 집계 → `/api/analysis/strategy-kpi`.
3. **섀도우 모드**: `STRATEGY_SHADOW=1` 이면 주문 대신 `trade_history(status='shadow')` 기록. 신규 규칙은 2주 섀도우 후 전환.

### 설정 키 신규 (예정)
`STRATEGY_MAX_NEW_BUYS_PER_LOOP`, `STRATEGY_BUY_CONFIDENCE_MULT_*`, `STRATEGY_SPLIT_TRANCHE_DROP_PCT`, `STRATEGY_PRICE_STALE_SEC_HIGH/LOW`, `STRATEGY_UPTREND_TRAILING_FLOOR_PCT`, `STRATEGY_UPTREND_SCORE_SELL_MIN_PROFIT`, `STRATEGY_UPTREND_SCALE_OUT`, `STRATEGY_UPTREND_DCA_REF`, `STRATEGY_UPTREND_DCA_MAX_STAGES_PER_DAY`, `STRATEGY_MAX_GROUP_RATIO`, `STRATEGY_CRASH_BREADTH_*`, `STRATEGY_MARKET_ADJ_CAP`, `STRATEGY_SHADOW`.

---

## 5. 결정이 필요한 항목 (사용자)
1. **DCA 기준가**: 평균단가(현행, 시뮬 검증) vs 최초 진입가(공격적). → B4 A/B 로 결정 권장.
2. **예비현금 동적화**: 평시 10% → 폭락 시 0% 자동 전환에 동의하는지.
3. **상대 약세 정리(S5)**: "우상향 가정"의 예외를 둘지. 두지 않으면 S4 변동성 손절만 안전망.
4. **점수 분리(D1)**: UI 점수 의미가 바뀐다(시장 효과가 점수에서 빠져 임계선으로 이동). UI 에 임계선 표시 추가 필요.
