# Quick Start Commands

**Essential commands for this project**

---

## Development

```bash
# 서버 실행
uvicorn main:app --reload --port 8000

# 환경변수 확인 (실서버 여부)
grep -E "DEV_MODE|KIS_IS_VTS|KIS_BASE_URL" .env

# DB 확인
sqlite3 data/stock_advisor.db ".tables"
sqlite3 data/stock_advisor.db "SELECT * FROM market_regime_history ORDER BY date DESC LIMIT 5;"
```

## 로그 확인

```bash
# 실시간 로그
tail -f logs/app.log

# 주문 관련 로그만
grep -E "주문|BUY|SELL|DEV MODE|체결" logs/app.log | tail -50

# US 매매 관련
grep -E "US|overseas|해외|NASD|NYSE" logs/app.log | tail -30
```

## 주요 API 엔드포인트

```bash
# Regime 현황
curl http://localhost:8000/api/macro/regime

# 포트폴리오
curl http://localhost:8000/api/portfolio

# 스케줄러 상태
curl http://localhost:8000/api/scheduler/status
```

## Common Workflows

1. **실서버 배포 전 체크**:
   - `.env`: `DEV_MODE=false`, `KIS_IS_VTS=false` 확인
   - KIS_BASE_URL: `openapi.koreainvestment.com:9443` 확인

2. **US 시장 모니터링** (KST 23:30~06:00):
   - 서버 프로세스 생존 확인 필수
   - overseas cash API 500 오류 주의

3. **Regime 강제 갱신**:
   - `MacroService.invalidate_cache()` 호출

---

**Last Updated**: 2026-03-06
