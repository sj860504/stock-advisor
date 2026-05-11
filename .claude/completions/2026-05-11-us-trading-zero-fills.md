# 2026-05-11 — 미장 매수 0건 사태 진단·수정 총정리

## 사용자 호소
> "어제 미장 미국주식 매수 하나도 안 됨. 한 달 전까지는 잘 됐었음."

## 최종 진단 — 복합 원인 6건

| # | 영역 | 진짜 원인 |
|---|---|---|
| 1 | **logger** | `get_logger(name)`이 30+ 모듈마다 RotatingFileHandler 따로 생성 → 회전 시 fd 30개 deleted, 로그 silent loss |
| 2 | **sync 시간** | `should_fetch("US")` 범위 ET 09:30~17:30 정규장만 → 미장 시작 전(프리마켓) sync 잡 차단 |
| 3 | **sync 누락** | cron 잡은 KST 04:00 단 1개. 미장 시작 직전 fresh sync 잡 자체 부재 |
| 4 | **메모리 미반영** | `sync_daily_market_data`가 DB(financials) 갱신 후 `MarketDataService._states` 메모리 reload 안 함 |
| 5 | **NameError** | `_refresh_low_tier_prices`에 `all_poll_tickers` 변수 정의 누락 → 매 5분 잡 fail → US LOW-tier 가격 폴링 자체 안 됨 |
| 6 | **US universe=3** | `get_top_us_tickers` KIS VTS 응답 부실 시 fallback 분기에서 결과 50개 미만 시 보강 안 함 → universe US=3 (보유만) |

> 1+2+3+4+5+6 합쳐서: **US 99 종목 중 보유 3개만 ready** → AssetMgmt가 보유 종목에만 BUY 시그널 → `_passes_add_buy_entry`(profit > -5% 차단) → 매 분 모두 차단 → 매수 0건

## 수정 커밋 (develop 브랜치, 시간순)

| 커밋 | 파일 | 효과 |
|---|---|---|
| `3161352` | `utils/logger.py` | 루트 로거 1개로 통합 (fd 좀비 차단) |
| `5eef273` | `utils/logger.py` | DEBUG → INFO + websockets/urllib3 노이즈 차단 |
| `061d8a0` | `market_hour_service.py`, `scheduler_service.py` | `should_fetch(US)` 04:00~20:00 ET 확장 + `sync_us_premarket` 잡 (KST 22:00) 추가 |
| `efd920a` | `market_data_service.py`, `data_service.py` | `reload_states_from_db` 메서드 추가 + sync 직후 호출 |
| `7051715` | `scheduler_service.py` | `_refresh_low_tier_prices` NameError fix |
| `36bda4f` | `data_service.py`, `trading_strategy_service.py` | `get_top_us_tickers` < 50개 시 fallback 강제 보강 + universe 이중 안전망 |

## 운영 적용 절차

```bash
cd /root/stock-advisor
git pull origin develop
# .pyc 캐시 모두 삭제 (가장 흔한 함정)
find . -path ./venv -prune -o -name "__pycache__" -type d -print 2>/dev/null | xargs rm -rf
find services/ -name "*.py" -exec touch {} \;
# 옛 프로세스 완전 종료
PID=$(pgrep -f "uvicorn main:app" | head -1)
kill -9 $PID && sleep 3
# 새로 띄움
nohup ./start.sh > /tmp/dev_run.log 2>&1 & disown
```

## 검증 시퀀스

### 즉시 (재시작 후 1~2분)
- `grep "_refresh_low_tier_prices.*raised" logs/app.log | wc -l` — 카운트 더 안 늘어나야
- `grep "Price refresh started" logs/app.log | tail -3` — `99 tickers (HIGH 20 + LOW 79)` 정상

### 미장 시작 (KST 22:00 ~ 22:35)
- `22:00:00 Starting daily market data sync` — sync_us_premarket 트리거
- `22:0X:XX Daily market data sync completed` (~3~4분 소요)
- `22:0X:XX reload_states_from_db: ~99/100 tickers refreshed`
- `22:30:XX Universe updated: ... US=99` ← 핵심 점프
- `22:30:XX Signal collection complete. ~99 stocks ready` ← 3에서 99로 점프
- `📢 Signal [BUY] MSFT/META/NVDA/...` — 보유 외 신규 종목 매수 시그널

만약 7번 도달 후 KIS reject 떠도 그건 다음 단계 디버깅 (`rt_cd`, `msg1` 직접 확인 가능 — logger fix 적용으로 로그 안 사라짐).

## 주요 학습

1. **Python `logging` 모듈은 각 logger마다 핸들러를 따로 가질 수 있음** — RotatingFileHandler를 모듈마다 추가하면 회전 시 fd 충돌. **루트 로거 1개 핸들러 + propagate=True가 정석**.
2. **APScheduler `cron` 잡은 timezone 미지정 시 서버 OS time 사용** — `should_fetch` 같은 시장 활성 검사는 시점 변환 정확히 해야. 다른 잡들(`econ_0830`)은 명시적 `timezone=_ET` 쓰는데 `sync_daily_market`만 누락.
3. **외부 API fetch fallback은 결과 카운트 검증을 강제해야** — KIS VTS가 빈 응답 줄 때 silently 0개 반환되는 패턴. `get_top_us_tickers`에 "결과 < N개면 fallback 강제" 가드 필수.
4. **DB sync 후 in-memory state는 자동으로 갱신 안 됨** — `sync_daily_market_data`가 DB 갱신해도 `_states` 메모리는 옛 값 그대로. 명시적 `reload_states_from_db` 호출 필요.
5. **`.pyc` 캐시가 .py mtime보다 신선하면 옛 코드 로드 가능** — git pull 후 재시작 시 `__pycache__` 일괄 삭제 + `touch` 권장.

## Common Mistakes 후속 추가 항목

- "logger 회전 시 fd 좀비 누적" 항목 신규 등록
- "외부 API fetch fallback 결과 검증 강제" 항목 신규 등록
- "재시작 시 .pyc 캐시 삭제 필수" 항목 신규 등록
