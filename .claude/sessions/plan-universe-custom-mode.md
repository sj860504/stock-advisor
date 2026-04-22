# 유니버스 / 커스텀 모드 전환 구현 계획 (v2)

**작성일**: 2026-04-14
**브랜치**: RC-1
**상태**: 계획 수립 완료 / 미구현

---

## 요구사항

| 모드 | 유니버스 | 커스텀 |
|------|----------|--------|
| 매매 대상 | KIS 시총 상위 KR+US 100종목 | 사용자가 추가한 종목만 |
| WebSocket | 오픈 시장 100종목 전체 | 오픈 시장 watchlist 전체 |
| 데이터 수집 | top100 + 보유종목 | watchlist + 보유종목 |
| 편집 UI | 없음 | 종목별 목표가/메모 편집 가능 |

---

## KIS WebSocket 한계 — ✅ 검증 완료 (2026-04-14)

```
결론: KIS WebSocket 한계 = 세션 총 40개 (시장 구분 없음)
      KR/US 시장이 겹치지 않으므로 개장 시장에 40개 전부 사용 가능
검증: 미국 장 중 40종목 구독 → 전원 수신 확인
적용: WS_HIGH_TIER_COUNT = 40  (scheduler_service.py:23)
```

### 확정된 WebSocket 구성

KR/US 시장은 시간대가 겹치지 않아 한 번에 한 시장만 40슬롯 사용:
```python
watch_kr = not is_us_open and is_kr_strategy_enabled
watch_us = not is_kr_open and is_us_strategy_enabled
```

| 모드 | KR 개장 | US 개장 |
|------|---------|---------|
| 유니버스 | KR 40종목 WebSocket + 나머지 60 폴링 | US 40종목 WebSocket + 나머지 60 폴링 |
| 커스텀 | watchlist KR 전원 (최대 40) | watchlist US 전원 (최대 40) |

### 로테이션 방식은 비효율 — 채택 안 함

100종목을 34개씩 3그룹으로 순환하면:

| 단계 | 소요 시간 |
|------|----------|
| WebSocket 연결 | ~2초 |
| 34종목 구독 (× 50ms) | ~1.7초 |
| 가격 데이터 수신 대기 | ~10~30초 (유동성에 따라) |
| 연결 해제 | ~0.5초 |
| **그룹 1개 합계** | **~14~34초** |
| **3그룹 전체 1사이클** | **~42~102초 (약 1~2분)** |

**문제점**:
- 그룹 2를 보는 동안 그룹 1, 3은 가격 블라인드
- 결국 1~2분 폴링과 동일 → 현재 5분 폴링 대비 약간의 개선에 불과
- 연결/해제 반복으로 KIS 서버 부하 + 연결 오류 위험 증가

**결론: 로테이션 방식 채택 안 함.** 40개 검증 결과에 따라 시나리오 A 또는 B 적용.

---

## 현재 코드 분석

### 서비스 전체 흐름

```
[매 1분] run_trading_strategy()
  └─ TradingStrategyService.run_strategy()
       ├─ _update_target_universe()         # _states 갱신 + prune
       ├─ _run_signals_and_execute()
       │    ├─ SignalService._collect_trading_signals()   # 신호 수집 + 캐시
       │    └─ PositionService._execute_collected_signals()
       │         ├─ _process_single_signal()              # 매수/매도 실행
       │         └─ _check_unmonitored_holdings()         # 미모니터링 보유종목 손절
       └─ AssetManagementService.run()
            └─ _rebalance_market()
                 └─ SignalService.get_latest_signals()    # 캐시 재사용

[매일 08:30] manage_subscriptions_async()
  └─ _build_ticker_universe()    # top100+holdings 빌드
  └─ _classify_tiers()           # HIGH/LOW 분류
  └─ MarketDataService.register_batch()
  └─ _subscribe_kr/us_tickers()  # WebSocket 구독

[매일 04:00] sync_daily_market_data()
  └─ top100 + holdings → RSI/EMA/DCF 계산 → DB 저장

[매 5분] refresh_low_tier_prices()
  └─ LOW tier 종목 REST 폴링
```

### 현재 모드 처리 위치

| 파일 | 위치 | 현재 처리 |
|------|------|----------|
| `trading_strategy_service.py:389` | `_run_signals_and_execute()` | `kr_mode == "top100"` or `"watchlist"` 분기 |
| `trading_strategy_service.py:204` | `_update_target_universe()` | 모드 무관 — wl+top100 항상 포함 |
| `signal_service.py:406` | `_collect_trading_signals()` | `watchlist_kr=None` → top100, `set` → watchlist 필터 |
| `scheduler_service.py:150` | `_build_ticker_universe()` | **watchlist 미포함** — top100+holdings만 |
| `data_service.py:401` | `sync_daily_market_data()` | **watchlist 미포함** — top100+holdings만 |

### 현재 문제점

1. `_build_ticker_universe()` (스케줄러)에 watchlist 없음 → WebSocket 미구독, `_states` 미등록 불안정
2. `sync_daily_market_data()`에 watchlist 없음 → RSI/EMA/DCF 데이터 없음 → 점수 계산 오류
3. `_update_target_universe()` (전략서비스)에는 wl 포함 → 스케줄러와 불일치
4. 종목별 편집 UI 없음

---

## 영향도 분석

### ① `signal_service._collect_trading_signals()` — 변경 필요

**현재**:
```python
watchlist_kr=watchlist_set if kr_mode == "watchlist" else None
watchlist_us=watchlist_set if us_mode == "watchlist" else None
```
**변경**:
```python
watchlist_kr=watchlist_set if kr_mode == "custom" else None
watchlist_us=watchlist_set if us_mode == "custom" else None
```
- 로직 동작 변경 없음, 모드값 rename만

**커스텀 모드 필터 동작 (기존 유지)**:
- 미보유 + watchlist 미포함 → 점수 계산/신호 생성 건너뜀
- 보유종목은 모드 무관하게 항상 처리 (매도 신호용)

---

### ② `asset_management_service._rebalance_market()` — 변경 없음 ✅

- `SignalService.get_latest_signals()` 캐시를 재사용
- 캐시는 `_collect_trading_signals()` 마지막 호출 결과
- 커스텀 모드 시 캐시 = 커스텀 종목 신호만 → 자산관리도 자동으로 커스텀 종목 기준 ✅

---

### ③ `position_service._check_unmonitored_holdings()` — 변경 없음 ✅

- `prepared_signals`에 없는 보유종목 → 손절/익절 체크
- 커스텀 모드에서 watchlist 밖 보유종목도 자동으로 이 경로로 처리됨 ✅

---

### ④ `trading_strategy_service._update_target_universe()` — 변경 필요

**현재**: 모드 무관, wl+top100 항상 `_states`에 등록 (스케줄러와 불일치)

**변경**:
```python
# 모드별 universe 구성 — _build_ticker_universe()와 일치시킴
if kr_mode == "universe":
    all_kr = top100_kr + kr_holdings + wl_kr
else:  # custom
    all_kr = wl_kr + kr_holdings

if us_mode == "universe":
    all_us = top100_us + us_holdings + wl_us
else:  # custom
    all_us = wl_us + us_holdings
```
- `prune_states()` 호출로 `_states`에서 불필요 종목 제거
- 커스텀 모드에서 top100이 `_states`에서 제거됨 → `get_all_cached_prices()` 응답도 커스텀 종목만

---

### ⑤ `scheduler_service._build_ticker_universe()` — 변경 필요 ★ WebSocket 핵심

**현재**: top100+holdings만

**변경**: 모드 인식 + watchlist 포함
```python
kr_mode = normalize(SettingsService.get("kr_strategy_mode"))  # "top100"→"universe"
us_mode = normalize(SettingsService.get("us_strategy_mode"))

wl_kr, wl_us = WatchlistRepo.get_tickers("sean") 분리

if kr_mode == "universe":
    all_kr = top100_kr + kr_holdings + wl_kr  # wl은 항상 등록(UI 표시 + 데이터 보장)
else:  # custom
    all_kr = wl_kr + kr_holdings              # top100 제외

# US 동일
```

**`_classify_tiers()` 변경 없음** — `all_kr`/`all_us` 기준으로 자동 처리:
- 유니버스: 20슬롯에 holdings 우선, top100으로 채움
- 커스텀: wl+holdings가 20 이하면 전원 WebSocket HIGH

---

### ⑥ `sync_daily_market_data()` — 변경 필요

**변경**: 모드 무관, watchlist 항상 포함
```python
wl_raw   = WatchlistRepo.get_tickers("sean")
wl_pairs = [(t, "KR" if is_kr(t) else "US") for t in wl_raw if t not in base_set | holding_set]
all_tickers = base_tickers + holding_pairs + wl_pairs
```
- 커스텀 모드에서 전략 실행 시 RSI/EMA/DCF 데이터 보장
- 유니버스 모드에서도 watchlist 데이터 수집 (UI 표시용)

---

### ⑦ `manage_subscriptions_async()` — 변경 없음 ✅

`_build_ticker_universe()` 변경으로 자동 반영됨. `watch_kr`/`watch_us` 오픈 시장 감지도 기존 유지.

---

### ⑧ `refresh_low_tier_prices` (5분 폴링) — 변경 없음 ✅

LOW tier = `_states`에 있는 종목 중 HIGH 아닌 것. `_states` 자체가 모드별로 달라지므로 자동 반영.

---

## 백엔드 구현 계획

### Step 1. DB 마이그레이션 — `UserWatchlist` 편집 필드 추가

**파일**: `models/watchlist.py`
```python
target_buy_price  = Column(Float, nullable=True)
target_sell_price = Column(Float, nullable=True)
memo              = Column(String(500), nullable=True)
```
→ Alembic migration 실행 필요

---

### Step 2. 모드값 rename + 하위 호환

**공통 헬퍼** (모든 서비스에서 재사용):
```python
_MODE_COMPAT = {"top100": "universe", "watchlist": "custom"}

def normalize_mode(raw: str) -> str:
    return _MODE_COMPAT.get(raw or "universe", raw or "universe")
```

**파일별 변경**:

| 파일 | 변경 |
|------|------|
| `routers/watchlist.py` | `_VALID_MODES = {"universe", "custom"}`, normalize 적용 |
| `trading_strategy_service.py:389` | `or "top100"` → `or "universe"`, `== "watchlist"` → `== "custom"` |
| `trading_strategy_service.py:204` | 동일 |

---

### Step 3. `_build_ticker_universe()` 모드 인식 ★

**파일**: `services/base/scheduler_service.py`

```python
@classmethod
def _build_ticker_universe(cls) -> tuple:
    from repositories.watchlist_repo import WatchlistRepo

    def _norm_ticker(t):
        t = str(t or "").strip().upper()
        return t.zfill(6) if t.isdigit() and len(t) < 6 else t

    kr_mode = normalize_mode(SettingsService.get("kr_strategy_mode"))
    us_mode = normalize_mode(SettingsService.get("us_strategy_mode"))

    portfolio    = PortfolioService.load_portfolio('sean')
    holdings_raw = [_norm_ticker(h.ticker) for h in portfolio]
    kr_holdings  = {t for t in holdings_raw if t and is_kr(t) and len(t) == 6}
    us_holdings  = {t for t in holdings_raw if t and t.isalpha()}

    wl_raw = WatchlistRepo.get_tickers("sean")
    wl_kr  = [_norm_ticker(t) for t in wl_raw if is_kr(_norm_ticker(t))]
    wl_us  = [_norm_ticker(t) for t in wl_raw if not is_kr(_norm_ticker(t)) and _norm_ticker(t).isalpha()]

    if kr_mode == "universe":
        kr_top = [_norm_ticker(t) for t in DataService.get_top_krx_tickers(limit=100)]
        all_kr = list(dict.fromkeys(kr_top + list(kr_holdings) + wl_kr))
    else:
        all_kr = list(dict.fromkeys(wl_kr + list(kr_holdings)))

    if us_mode == "universe":
        us_top = [_norm_ticker(t) for t in DataService.get_top_us_tickers(limit=100)]
        all_us = list(dict.fromkeys(us_top + list(us_holdings) + wl_us))
    else:
        all_us = list(dict.fromkeys(wl_us + list(us_holdings)))

    target_universe = set(all_kr + all_us)
    return all_kr, all_us, kr_holdings, us_holdings, target_universe, holdings_raw
```

---

### Step 4. `_update_target_universe()` 모드 인식

**파일**: `services/strategy/trading_strategy_service.py`

`_build_ticker_universe()`와 동일한 모드별 로직 적용 (중복이지만 스케줄러 의존성 분리를 위해 유지).

---

### Step 5. `sync_daily_market_data()` watchlist 포함

**파일**: `services/market/data_service.py`

```python
# holding_pairs 이후에 추가
from repositories.watchlist_repo import WatchlistRepo
wl_raw   = WatchlistRepo.get_tickers("sean")
wl_pairs = [
    (_norm(t), "KR" if is_kr(_norm(t)) else "US")
    for t in wl_raw
    if _norm(t) and _norm(t) not in holding_set
]
all_tickers = base_tickers + holding_pairs + wl_pairs
```

---

### Step 6. Watchlist 편집 API

**파일**: `repositories/watchlist_repo.py`
```python
@classmethod
def update_item(cls, user_id, ticker, **kwargs):
    # target_buy_price, target_sell_price, memo 업데이트
```

**파일**: `routers/watchlist.py`
```
PATCH /watchlist/{user_id}/{ticker}
Body: WatchlistItemUpdateRequest
```

**파일**: `models/schemas.py`
```python
class WatchlistItemUpdateRequest(BaseModel):
    target_buy_price:  Optional[float] = None
    target_sell_price: Optional[float] = None
    memo:              Optional[str]   = None
```

---

### Step 7. `TickerState`에 watchlist 편집값 반영

**파일**: `services/market/market_data_service.py`

warm-up 또는 `register_batch()` 이후:
```python
wl_item = WatchlistRepo.get_item("sean", ticker)
if wl_item:
    if wl_item.target_buy_price:
        state.target_buy_price  = wl_item.target_buy_price
    if wl_item.target_sell_price:
        state.target_sell_price = wl_item.target_sell_price
```
> `fair_value` 편집은 기존 `DcfOverride` 테이블 위임 (중복 저장 방지)

---

### Step 8. `/market/monitored` 응답에 `is_custom` 추가

**파일**: `services/base/scheduler_service.py` `get_all_cached_prices()`
```python
watchlist_set = set(WatchlistRepo.get_tickers("sean"))
result[ticker]["is_custom"] = ticker in watchlist_set
```

---

## 프론트엔드 구현 계획

### FE-A. Watchlist 탭 — 모드 버튼 rename

**`index.html`**
```html
<!-- 변경 전 -->
<button id="kr-mode-top100"    onclick="setStrategyMode('kr','top100')">Top 100</button>
<button id="kr-mode-watchlist" onclick="setStrategyMode('kr','watchlist')">내 Watchlist</button>

<!-- 변경 후 -->
<button id="kr-mode-universe" onclick="setStrategyMode('kr','universe')">유니버스 모드</button>
<button id="kr-mode-custom"   onclick="setStrategyMode('kr','custom')">커스텀 모드</button>
```

**`app.js` — `_updateModeBtns()`**
```js
const hints = {
    universe: 'Top100 전체 자동매매 | WebSocket: 상위 20종목',
    custom:   '내 종목만 자동매매 | WebSocket: 전 종목 실시간',
};
```

---

### FE-B. Watchlist 테이블 — 편집 UI 추가

**`index.html`** — 컬럼 추가: `목표매수가`, `목표매도가`, `메모`

**`app.js`**
```js
// renderWatchlistTable() 행에 편집 필드 추가
<td><input type="number" placeholder="-"
     value="${item.target_buy_price || ''}"
     onblur="updateWatchlistItem('${ticker}','target_buy_price',this.value)"></td>
<td><input type="number" placeholder="-"
     value="${item.target_sell_price || ''}"
     onblur="updateWatchlistItem('${ticker}','target_sell_price',this.value)"></td>
<td><input type="text" placeholder="-"
     value="${item.memo || ''}"
     onblur="updateWatchlistItem('${ticker}','memo',this.value)"></td>

// 신규 함수
async function updateWatchlistItem(ticker, field, value) {
    await apiFetch(`/watchlist/${USER}/${ticker}`, {
        method: 'PATCH',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ [field]: value || null }),
    });
    showToast(`${ticker} 저장됨`);
}
```

---

### FE-C. Market 탭 — 커스텀 종목 마커

**`app.js` — `renderTop20Filtered()`**
```js
const customBadge = info.is_custom
    ? '<span class="badge b-warn" style="font-size:.7rem">★커스텀</span> '
    : '';
// ticker 셀에 customBadge 추가
```

---

## 구현 순서 (의존성)

```
[1] DB migration
     models/watchlist.py 컬럼 추가
     alembic revision + upgrade

[2] 모드값 rename (독립)
     routers/watchlist.py
     trading_strategy_service.py

[3] _build_ticker_universe() 모드 인식  ← WebSocket 핵심
     scheduler_service.py
     ↑ Step 2 완료 후

[4] _update_target_universe() 모드 인식
     trading_strategy_service.py
     ↑ Step 2 완료 후

[5] sync_daily watchlist 포함 (독립)
     data_service.py

[6] Watchlist 편집 API
     watchlist_repo.py + routers/watchlist.py + schemas.py
     ↑ Step 1 완료 후

[7] TickerState 편집값 반영
     market_data_service.py
     ↑ Step 6 완료 후

[8] is_custom 필드
     scheduler_service.py get_all_cached_prices()

──────────────────────────────────
FE-A: 모드 버튼 rename   ← Step 2 완료 후
FE-B: 편집 UI            ← Step 6 완료 후
FE-C: 커스텀 마커         ← Step 8 완료 후
```

---

## 주의사항 / 리스크

| 항목 | 내용 |
|------|------|
| **KIS WebSocket 한도** | 시장당 20종목 하드 한계. 유니버스 100종목 전체 WebSocket 불가. 커스텀은 20 이하면 전원 가능 |
| **DB 하위 호환** | `settings` 테이블의 `"top100"`, `"watchlist"` → normalize 함수로 자동 매핑 |
| **_build / _update 중복** | `scheduler_service._build_ticker_universe()`와 `trading_strategy_service._update_target_universe()`에 같은 로직 중복. 둘 다 수정 필요 |
| **fair_value 편집** | `DcfOverride` 테이블 위임. Watchlist에 별도 저장 시 이중화 문제 발생 |
| **커스텀 모드 prune** | `_update_target_universe()`에서 `prune_states()` 호출 시 top100이 `_states`에서 제거됨 → Market 탭에 top100 미표시. 의도된 동작이지만 사용자에게 안내 필요 |
| **sync_daily watchlist 추가 시 KIS Rate Limit** | watchlist 종목 수만큼 KIS API 호출 증가. watchlist 상한선 설정 권장 (예: 최대 50종목) |

---

**Last Updated**: 2026-04-14
