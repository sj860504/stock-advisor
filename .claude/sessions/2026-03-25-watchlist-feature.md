# 구현 계획서: 전략 모드 선택 (Top100 vs Watchlist)

**작성일**: 2026-03-25
**최종 수정**: 2026-03-26
**목표**: 시장별(KR/US)로 전략 투자 대상을 **Top100** 또는 **사용자 Watchlist** 중 선택 가능하게 한다.

---

## 설계 원칙

| 항목 | 설명 |
|------|------|
| 설정 단위 | **시장별 독립** — KR과 US 각각 모드 선택 |
| 기본값 | `top100` (기존 동작 유지) |
| Watchlist 모드 시 | Top200은 **가격 데이터만 수집**, 점수 계산 생략 |
| SELL/손절/익절 | 모드 무관하게 **항상 실행** (보유 종목 보호) |
| 하위 호환 | `watchlist=None` 기본값 → 백테스트/시뮬레이션 영향 없음 |

---

## 동작 비교

### Top100 모드 (기존 동작)
```
Universe: Top100(KR/US) + 보유종목
점수 계산: Universe 전체
BUY 신호:  점수 상위 종목 → 자동매매
SELL 신호: 보유종목 전체
```

### Watchlist 모드 (신규)
```
Universe:  Top200(KR/US, 가격만) + Watchlist + 보유종목
점수 계산: Watchlist + 보유종목만 (Top200 제외)
BUY 신호:  Watchlist 종목 중 조건 충족 시만
SELL 신호: 보유종목 전체 (변경 없음)
```

---

## 구현 단계

### Step 1. DB 모델 — UserWatchlist

**파일**: `models/watchlist.py` (신규)

```python
from sqlalchemy import Column, String, DateTime
from datetime import datetime
from models.stock_meta import Base

class UserWatchlist(Base):
    __tablename__ = 'user_watchlist'
    user_id  = Column(String(50), primary_key=True)
    ticker   = Column(String(20), primary_key=True)
    added_at = Column(DateTime, default=datetime.now)
```

- 복합 PK `(user_id, ticker)` — 멀티유저, 중복 방지
- StockMeta와 분리: 전역 종목 데이터 vs 유저별 개념

---

### Step 2. DB 초기화에 모델 등록

**파일**: `repositories/database.py` — `init_db()` (line 49~53)

```python
from models.watchlist import UserWatchlist  # noqa: F401  ← 추가
```

---

### Step 3. Repository — WatchlistRepo

**파일**: `repositories/watchlist_repo.py` (신규)

```python
class WatchlistRepo:

    @classmethod
    def get_tickers(cls, user_id: str) -> list[str]:
        """유저의 Watchlist 티커 목록 반환."""

    @classmethod
    def add_ticker(cls, user_id: str, ticker: str) -> bool:
        """티커 추가. 이미 존재하면 False 반환."""

    @classmethod
    def remove_ticker(cls, user_id: str, ticker: str) -> bool:
        """티커 제거. 존재하지 않으면 False 반환."""
```

- `session_scope` (write) / `session_ro` (read) 패턴 준수
- 캐시 불필요: 목록 소규모, 1분 루프 1회 조회

---

### Step 4. 전략 모드 설정 — Settings 테이블 활용

기존 `Settings` key-value 테이블에 2개 키 추가.
별도 테이블/모델 불필요.

| 키 | 기본값 | 가능한 값 |
|----|--------|----------|
| `kr_strategy_mode` | `"top100"` | `"top100"` \| `"watchlist"` |
| `us_strategy_mode` | `"top100"` | `"top100"` \| `"watchlist"` |

**읽기 (SettingsRepo 기존 API 사용)**:
```python
kr_mode = SettingsRepo.get(user_id, "kr_strategy_mode", default="top100")
us_mode = SettingsRepo.get(user_id, "us_strategy_mode", default="top100")
```

---

### Step 5. Universe 업데이트 분기

**파일**: `services/strategy/trading_strategy_service.py`
**함수**: `_update_target_universe()` (line ~135)

```python
from repositories.watchlist_repo import WatchlistRepo

kr_mode = SettingsRepo.get(user_id, "kr_strategy_mode", default="top100")
us_mode = SettingsRepo.get(user_id, "us_strategy_mode", default="top100")

# Top 티커 로드 (가격 수집용)
kr_top = [_norm_ticker(t) for t in DataService.get_top_krx_tickers(limit=200)] if run_kr else []
us_top = [_norm_ticker(t) for t in DataService.get_top_us_tickers(limit=200)]  if run_us else []

# Watchlist 티커 로드
watchlist_raw = WatchlistRepo.get_tickers(user_id)
watchlist     = [_norm_ticker(t) for t in watchlist_raw]
wl_kr = [t for t in watchlist if t and len(t) == 6 and t.isdigit()]
wl_us = [t for t in watchlist if t and t.isalpha()]

# Universe 결정
# - top100 모드: Top100 + 보유 (기존과 동일)
# - watchlist 모드: Watchlist + 보유 + Top200(가격만)
if kr_mode == "watchlist":
    all_kr = list(set(kr_holdings + wl_kr))       # 점수 계산 대상
    price_only_kr = list(set(kr_top) - set(all_kr)) # 가격만 수집
else:
    all_kr = list(set(kr_top[:100] + kr_holdings))
    price_only_kr = []

if us_mode == "watchlist":
    all_us = list(set(us_holdings + wl_us))
    price_only_us = list(set(us_top) - set(all_us))
else:
    all_us = list(set(us_top[:100] + us_holdings))
    price_only_us = []

# MarketDataService 등록
# all_kr/all_us → 점수 계산 포함 등록 (기존 방식)
# price_only_* → 가격 갱신만, 신호 계산 제외 (data_only 플래그)
```

> **구현 포인트**: `MarketDataService.register()` 또는 `update_state()` 호출 시
> `price_only` 티커는 OHLCV/지표 업데이트는 하되 신호 스코어 계산(`_compute_score`)는 skip.
> `MarketDataState` 에 `score_enabled: bool = True` 필드 추가, `price_only` 등록 시 `False` 설정.

---

### Step 6. 신호 필터링 — watchlist 파라미터 추가

**파일**: `services/strategy/signal_service.py`
**함수**: `_collect_trading_signals()`

```python
# 파라미터 추가 (기본값 None → 하위 호환 유지):
def _collect_trading_signals(
    cls, holdings, macro_data, user_state,
    kr_total, us_total_krw, cash_balance,
    target_cash_kr, target_cash_us,
    usd_cash=0.0, exchange_rate=1350.0,
    watchlist_kr: set[str] | None = None,  # ← 추가
    watchlist_us: set[str] | None = None,  # ← 추가
) -> list[SignalSchema]:
    ...
    for ticker, state in market_data_service.get_all_states().items():

        # score_enabled=False 인 price_only 종목은 신호 계산 전 스킵
        if not state.score_enabled:
            continue

        holding = holdings_map.get(ticker)

        # Watchlist 필터: 보유 없고 watchlist도 아니면 BUY 신호 생략
        if holding is None:
            mkt_watchlist = watchlist_kr if is_kr(ticker) else watchlist_us
            if mkt_watchlist is not None and ticker not in mkt_watchlist:
                continue
        ...
```

---

### Step 7. 신호 수집 호출부 — watchlist 주입

**파일**: `services/strategy/trading_strategy_service.py`
**함수**: `_run_signals_and_execute()`

```python
kr_mode = SettingsRepo.get(user_id, "kr_strategy_mode", default="top100")
us_mode = SettingsRepo.get(user_id, "us_strategy_mode", default="top100")

watchlist_raw = WatchlistRepo.get_tickers(user_id)
watchlist_set = set(_norm_ticker(t) for t in watchlist_raw)

prepared_signals = SignalService._collect_trading_signals(
    ...,
    watchlist_kr = watchlist_set if kr_mode == "watchlist" else None,
    watchlist_us = watchlist_set if us_mode == "watchlist" else None,
)
```

---

### Step 8. Pydantic 스키마 추가

**파일**: `models/schemas.py`

```python
class WatchlistItem(BaseModel):
    ticker: str
    added_at: datetime

class WatchlistResponse(BaseModel):
    user_id: str
    tickers: list[WatchlistItem]

class WatchlistUpdateResponse(BaseModel):
    status: str   # "added" | "removed" | "already_exists" | "not_found"
    ticker: str

class StrategyModeResponse(BaseModel):
    kr_strategy_mode: str   # "top100" | "watchlist"
    us_strategy_mode: str
```

---

### Step 9. API 라우터

**파일**: `routers/watchlist.py` (신규)

```
GET    /api/watchlist/{user_id}              → Watchlist 조회
POST   /api/watchlist/{user_id}/{ticker}     → 종목 추가
DELETE /api/watchlist/{user_id}/{ticker}     → 종목 제거

GET    /api/watchlist/{user_id}/mode         → 전략 모드 조회
PUT    /api/watchlist/{user_id}/mode         → 전략 모드 변경
       body: { "market": "kr"|"us", "mode": "top100"|"watchlist" }
```

- 티커 정규화: `.strip().upper()`, KR 숫자 6자리 zero-fill
- 모드 변경은 기존 `SettingsRepo.set()` 호출로 저장

---

### Step 10. main.py 등록

```python
from routers import watchlist as watchlist_router
app.include_router(watchlist_router.router, prefix="/api")
```

---

### Step 11. 프론트엔드 — Watchlist 탭 UI

**기존 탭 구조 유지**, Watchlist 탭에 전략 모드 토글 추가:

```html
<!-- 시장별 전략 모드 선택 카드 -->
<div class="card">
  <div class="card-title">⚙️ 전략 모드</div>
  <div style="display:flex;gap:24px;padding:8px 0">

    <!-- 한국 -->
    <div>
      <div style="font-size:.82rem;color:var(--sub);margin-bottom:8px">🇰🇷 한국</div>
      <div style="display:flex;gap:8px">
        <button id="kr-mode-top100"   class="btn btn-primary btn-sm"  onclick="setStrategyMode('kr','top100')">Top 100</button>
        <button id="kr-mode-watchlist" class="btn btn-outline btn-sm" onclick="setStrategyMode('kr','watchlist')">내 Watchlist</button>
      </div>
      <div id="kr-mode-hint" style="font-size:.76rem;color:var(--sub);margin-top:6px"></div>
    </div>

    <!-- 미국 -->
    <div>
      <div style="font-size:.82rem;color:var(--sub);margin-bottom:8px">🇺🇸 미국</div>
      <div style="display:flex;gap:8px">
        <button id="us-mode-top100"    class="btn btn-primary btn-sm"  onclick="setStrategyMode('us','top100')">Top 100</button>
        <button id="us-mode-watchlist" class="btn btn-outline btn-sm"  onclick="setStrategyMode('us','watchlist')">내 Watchlist</button>
      </div>
      <div id="us-mode-hint" style="font-size:.76rem;color:var(--sub);margin-top:6px"></div>
    </div>

  </div>
</div>
```

**JS 함수:**
```javascript
async function fetchStrategyMode() {
    const data = await apiFetch(`/watchlist/${USER}/mode`);
    updateModeBtns('kr', data.kr_strategy_mode);
    updateModeBtns('us', data.us_strategy_mode);
}

async function setStrategyMode(market, mode) {
    await apiFetch(`/watchlist/${USER}/mode`, {
        method: 'PUT',
        body: JSON.stringify({ market, mode })
    });
    updateModeBtns(market, mode);
    const label = mode === 'watchlist' ? '내 Watchlist 종목만 자동매매' : 'Top100 전체 자동매매';
    showToast(`${market.toUpperCase()} 전략: ${label}`);
}

function updateModeBtns(market, mode) {
    document.getElementById(`${market}-mode-top100`).className   = mode === 'top100'    ? 'btn btn-primary btn-sm'  : 'btn btn-outline btn-sm';
    document.getElementById(`${market}-mode-watchlist`).className = mode === 'watchlist' ? 'btn btn-primary btn-sm' : 'btn btn-outline btn-sm';
    const hints = {
        top100:    'Top100 전체가 자동매매 대상입니다',
        watchlist: '내 Watchlist 종목만 자동매매됩니다 (Top200은 점수 계산 생략)',
    };
    document.getElementById(`${market}-mode-hint`).textContent = hints[mode] || '';
}
```

---

## 변경 파일 요약

| 파일 | 유형 | 변경 규모 |
|------|------|----------|
| `models/watchlist.py` | 신규 | ~15줄 |
| `repositories/database.py` | 수정 | +1줄 |
| `repositories/watchlist_repo.py` | 신규 | ~50줄 |
| `models/schemas.py` | 수정 | +15줄 |
| `services/strategy/trading_strategy_service.py` | 수정 | +20줄 |
| `services/strategy/signal_service.py` | 수정 | +8줄 |
| `routers/watchlist.py` | 신규 | ~80줄 |
| `main.py` | 수정 | +2줄 |
| `static/index.html` | 수정 | +모드 토글 UI |
| `static/js/app.js` | 수정 | +3 함수 |

---

## 검증 시나리오

| # | 시나리오 | 기대 결과 |
|---|----------|----------|
| 1 | KR=top100, US=top100 | 기존 동작과 동일 |
| 2 | KR=watchlist (빈 목록) | KR BUY 신호 0건, SELL은 정상 |
| 3 | KR=watchlist, AAPL 추가 | AAPL BUY 신호만, 나머지 KR Top200 스킵 |
| 4 | US=watchlist, KR=top100 | KR은 Top100 전략, US는 Watchlist 전략 혼용 |
| 5 | KR=watchlist, 보유 종목이 watchlist 미포함 | SELL/손절/익절 정상 작동 |
| 6 | 백테스트 호출 | `watchlist_kr=None` 기본값으로 기존 동작 유지 |
| 7 | Top200 종목 score_enabled=False | 신호 계산 루프에서 skip, 가격만 수집 |

---

**Last Updated**: 2026-03-26
