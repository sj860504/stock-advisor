# Common Mistakes

**⚠️ CRITICAL - Read at session start (2 min saves 2 hours!)**

---

## ⚠️ Research Order — 반드시 준수

**질문/분석/수정 시 순서:**
1. `.claude/BUSINESS_LOGIC.md` → `.claude/FUNCTION_REFERENCE.md` → `.claude/ARCHITECTURE_MAP.md` 순으로 확인
2. 문서에 없거나 불충분할 때만 소스 파일 열람
3. **절대 금지**: 문서 미확인 상태에서 `services/`, `repositories/` 등 소스 직접 열람

---

## Top 5 Critical Mistakes

### 1. DEV_MODE=true로 실제 주문 차단

**Symptom**: 주문 로그에 "[DEV MODE] 실제 주문 차단" 출력, 실제 체결 0건
**Check**: `.env`에서 `DEV_MODE=true`, `KIS_IS_VTS=true`, `KIS_BASE_URL=openapivts...` 확인
**Fix**: 실서버 운영 시 `DEV_MODE=false`, `KIS_IS_VTS=false`, `KIS_BASE_URL=openapi.koreainvestment.com:9443`

### 2. send_overseas_order() 거래소 코드 미지정 (NYSE 종목 오류)

**Symptom**: NYSE 종목(DIS, JNJ, MDT, IEF, ETN, LOW, HD, GILD, LLY, MRK 등) 주문 실패
**Check**: `trading_strategy_service.py`에서 `send_overseas_order(ticker, qty, price, "buy")` — market 파라미터 생략 시 기본값 "NASD"
**Fix**: ticker별 거래소 코드 자동 매핑 로직 추가 필요 (NASD/NYSE/AMEX)

### 3. Overseas available cash API HTTP 500 → USD 현금 0 처리

**Symptom**: US 매수 전부 차단, "⚠️ Overseas available cash API HTTP 500" 로그 반복
**Check**: VTS(모의투자) 환경에서 해외 현금 조회 API 불안정
**Fix**: API 실패 시 이전 캐시 값 사용, 또는 실서버 환경 사용

### 4. FRED API realtime_start/end 오해

**Symptom**: 날짜 범위 필터가 안 되거나 매일 중복 데이터 반환
**Check**: `realtime_start/end`는 빈티지(발표시점) 필터이며, 날짜 범위 필터가 아님
**Fix**: `include_release_dates_with_no_data=true` 사용 금지; 월별 중복제거는 year-month 기준 첫 날짜만 사용

### 5. US 매수 시 KRW 현금으로 수량 계산

**Symptom**: USD 현금 충분해도 수량=0 반환
**Check**: `_calculate_buy_quantity()`에서 `cash_balance(KRW)` 사용 여부 확인
**Fix**: US 매수는 USD 현금 기준으로 수량 계산

### 6. AssetManagement 쿨다운 미영속 → 동일 종목 반복 매도 (2026-03-18 수정)

**Symptom**: 자산관리 서비스가 동일 종목을 매 1분 루프마다 반복 매도 (T 20+회 매도 사례)
**Check**: `trading_strategy_service.py`에서 `_save_state(state)`가 `AssetManagementService.run()` 이전에만 호출
**Root Cause**: 자산관리가 설정한 `sell_cooldown`이 DB에 저장되지 않아 다음 루프에서 소실
**Fix**: `AssetManagementService.run()` 이후 `_save_state(state)` 2차 호출 추가

### 7. logger 모듈별 핸들러 → 회전 시 fd 좀비 (2026-05-11 수정)

**Symptom**: 로그 파일이 회전된 .deleted fd로 흘러 디스크에 안 남음, 며칠치 로그 영구 휘발
**Check**: `lsof -p <uvicorn_pid> | grep "logs/app"` 결과 동일 파일 fd 30개 이상 (대부분 deleted)
**Root Cause**: `utils/logger.py`에서 `get_logger(name)`이 모듈마다 별도 `RotatingFileHandler` 인스턴스 생성. 한 핸들러가 회전(rename)하면 다른 핸들러는 옛 fd로 deleted 파일에 계속 쓰기.
**Fix**: 루트 로거 1개에 핸들러 등록, 모듈 로거는 `propagate=True`로 위임 (`_root_initialized` 플래그로 1회 초기화 보장)

### 8. sync_daily_market 후 메모리 _states 미반영 (2026-05-11 수정)

**Symptom**: DB(financials) RSI/EMA는 갱신되는데 strategy 사이클이 옛 메모리 값 보고 is_ready=False 유지
**Check**: `Signal collection complete. N stocks ready`에서 N이 보유 종목 수에 머무름. financials 테이블 updated_at은 최신
**Root Cause**: `DataService.sync_daily_market_data`가 DB 갱신만 하고 `MarketDataService._states`에 reload 안 함. lazy warm-up은 KIS 야간 API에 의존 → 야간 응답 부실하면 영원히 not ready.
**Fix**: `MarketDataService.reload_states_from_db(tickers)` 메서드 추가 + `sync_daily_market_data` 끝에서 호출

### 9. APScheduler cron 잡 timezone 누락 (2026-05-11 발견)

**Symptom**: `sync_daily_market_data` 잡 시간 의도와 실제 동작 불일치
**Check**: `scheduler_service.py`에서 `add_job(..., 'cron', hour=N)` — timezone 인자 누락 시 서버 OS time 기준
**Root Cause**: BackgroundScheduler 기본은 OS time. 다른 잡(`econ_0830`, `vix_spike_check`)은 명시적 `timezone=_ET` 쓰는데 `sync_daily_market`만 누락 → KST 04:00에 실행
**Fix**: 시간 명시적 변환 + `should_fetch` 범위 확장 (US는 ET 04:00~20:00 프리/애프터 포함) + 미장 시작 전 `sync_us_premarket` 잡 (KST 22:00) 추가

### 10. get_top_us_tickers fallback 미보강 → universe US=3 (2026-05-11 수정)

**Symptom**: `Universe updated: ... US=3 [top100]` — 100개 top100 의도인데 보유 종목 3개만
**Check**: KIS VTS US ranking API가 빈/부분 응답 줄 때 `_apply_us_ticker_supplements` 분기에서 보강 안 됨
**Root Cause**: `_fetch_us_tickers_from_kis` 결과가 0개 아닌 작은 수(1~5개)면 `if not tickers:` 분기 안 타고 `if len(tickers) < limit:` 분기로 가는데 supplements 작동 안 함
**Fix**: `get_top_us_tickers` 최종 결과 < 50개 시 `_build_us_fallback_data`로 강제 보강 + `_update_target_universe`에 이중 안전망

### 11. `__pycache__` 캐시로 옛 코드 로드 (2026-05-11 학습)

**Symptom**: git pull 후 재시작했는데도 옛 동작 그대로 (NameError 계속 발생)
**Check**: `ls -la services/*/__pycache__/*.pyc` mtime이 `.py` 파일보다 신선
**Root Cause**: Python이 `.pyc` 캐시 timestamp 비교에서 오판 (git pull은 .py mtime 자동 갱신 X)
**Fix**: `find . -path ./venv -prune -o -name "__pycache__" -type d -print | xargs rm -rf` + `find services/ -name "*.py" -exec touch {} \;` 후 재시작

### 12. _refresh_low_tier_prices NameError (2026-05-11 수정)

**Symptom**: 매 5분 `apscheduler.executors.default - ERROR - Job ... raised an exception` + `NameError: name 'all_poll_tickers' is not defined`
**Check**: `scheduler_service.py:424` — `all_poll_tickers` 변수 정의 누락
**Root Cause**: HIGH + LOW 합본 변수가 정의 안 됨. 매 5분 잡이 전부 fail → US LOW-tier 가격 폴링 자체 작동 안 함 → US 종목 ready=False
**Fix**: `all_poll_tickers = list(set(high_tickers) | set(low_tickers))` 추가

### 13. Uptrend DCA 추매가 레거시 추매 엔트리 게이트에 막힘 (2026-09-09 수정)

**Symptom**: -3% DCA 1단계가 실행되지 않고 매 분 `Add-buy condition not met. Order skipped.` 반복. US 종목 DCA 는 단계 비율 무시하고 USD 현금 전액 매수
**Check**: `_execute_trade_v2(is_holding=True)` 경로는 `_passes_add_buy_entry`(profit > `STRATEGY_ADD_POSITION_BELOW` -5% 차단)를 통과함. DCA 예산은 `market_total + cash_balance(KRW)` 를 USD 가격으로 나눔
**Fix**: `trigger_reason='dca_stage_*'` 는 `_check_buy_cash_and_entry_conditions` 에서 엔트리·목표현금 게이트 우회. DCA 금액은 전부 KRW 기준(`price_krw`, `usd_cash×fx`). 새 매수 경로를 추가할 때 `_execute_trade_v2` 의 is_holding 게이트를 반드시 확인할 것

---

**Update this file when:**
- Bug took >1 hour to debug
- Error could cause production issue
- Mistake repeated across sessions

**Last Updated**: 2026-09-09
