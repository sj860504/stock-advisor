# Common Mistakes

**⚠️ CRITICAL - Read at session start (2 min saves 2 hours!)**

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

---

**Update this file when:**
- Bug took >1 hour to debug
- Error could cause production issue
- Mistake repeated across sessions

**Last Updated**: 2026-03-06
