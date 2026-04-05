# 설계서: S&P 500 Forward P/E Regime 점수 반영

**작성일**: 2026-04-05
**상태**: 구현 완료 (2026-04-05, commit 5837f8d)

---

## 1. 목적 및 배경

현재 regime_score는 5개 컴포넌트(technical/vix/fng/econ/other) + phase_modifier로 구성되며,
**밸류에이션 관점(시장 가격 수준)** 이 전혀 반영되지 않는다.

S&P 500 Forward P/E 비율은:
- 5년 평균 대비 낮을 때 → 시장 저평가 → 상승 여력 → **Bull 신호**
- 5년 평균 대비 높을 때 → 고평가 → 하방 리스크 → **Bear 신호**

---

## 2. 배치 결정: [5] other_20 에 흡수

### 5개 컴포넌트 적합성 검토

| 컴포넌트 | 성격 | Forward P/E 적합 여부 |
|---------|------|----------------------|
| technical_20 | 가격 모멘텀/EMA | ✗ — 가격 행동 지표 |
| vix_20 | 변동성 | ✗ — 공포 지표 |
| fng_20 | 투자심리 | ✗ — 심리 지표 |
| econ_20 | FRED 거시 지표 | △ — 소스 다름(yfinance) |
| **other_20** | **금리/금리차/DXY/BTC/금/오일** | **✓ — 시장 전반 금융 조건** |

**결론**: Forward P/E는 "시장 가격 수준"을 나타내는 금융 조건 지표로, other_20의 성격과 일치.

### 100점 유지 방식

Forward P/E raw score (±6) 를 other_raw 에 합산하고, max_val을 28 → **34** 로 확장.

```
other_raw = yield_score + curve_score + dxy_score + btc_score + gold_score + oil_score
            (기존 max ±28)

other_raw_new = other_raw + forward_pe_raw
                (새 max ±34)

other_20 = _to_20(other_raw_new, 34)   ← 0~20, 100점 합산 불변
```

**기여 규모**: other_20 내 forward_pe 단독 기여 최대 ±3.5pt → regime_score 기준 ±3.5점 영향

> **트레이드오프**: max_val 확장으로 기존 sub-score(특히 yield ±8) 가중치가 약간 희석됨.
> yield는 ±8/28 → ±8/34로 약 18% 희석. 허용 가능한 수준으로 판단.

---

## 3. 데이터 소스

### 3-1. Forward P/E 현재값
**1순위**: `yfinance` SPY ticker info

```python
import yfinance as yf
spy = yf.Ticker("SPY")
forward_pe = spy.info.get("forwardPE")  # float | None
```

- 무료, 추가 API 키 불필요
- 갱신 주기: yfinance 캐시 기준 (통상 1~2일 딜레이, 주간 단위로 충분)
- 실패 시 None → forward_pe_raw = 0 처리 (점수 기여 없음)

### 3-2. 5년 평균 Forward P/E

**방식**: MarketRegimeHistory 테이블에 `forward_pe` 컬럼 추가 후, 과거 기록 평균 사용

```sql
SELECT AVG(forward_pe) FROM market_regime_history
WHERE date >= date('now', '-5 years') AND forward_pe IS NOT NULL
```

- 최소 30개 데이터 미만 → **bootstrap 기본값 18.5** (S&P 500 10Y 역사 평균 근사)
- 데이터 쌓이면 자동으로 실측 평균으로 전환

---

## 4. 점수 계산 로직

### 4-1. `_calc_forward_pe_raw(forward_pe, avg_5y_pe)` 신규

```python
@staticmethod
def _calc_forward_pe_raw(
    forward_pe: float | None,
    avg_5y_pe: float | None,
) -> int:
    """
    S&P 500 Forward P/E vs 5Y average → raw score (-6 ~ +6)
    → other_raw에 합산되어 _to_20(other_raw, 34)으로 정규화됨
    """
    if forward_pe is None or avg_5y_pe is None or avg_5y_pe <= 0:
        return 0

    deviation = (forward_pe - avg_5y_pe) / avg_5y_pe

    if   deviation <= -0.20: return +6   # 20%↓ 이상 저평가
    elif deviation <= -0.10: return +4   # 10~20% 저평가
    elif deviation <= -0.05: return +2   # 5~10% 저평가
    elif deviation <  +0.05: return  0   # ±5% 중립
    elif deviation <  +0.10: return -2   # 5~10% 고평가
    elif deviation <  +0.20: return -4   # 10~20% 고평가
    else:                    return -6   # 20%↑ 이상 고평가
```

### 4-2. 점수 티어표

| Forward P/E vs 5Y avg | deviation | raw score | other_20 기여 (단독) |
|----------------------|-----------|-----------|---------------------|
| 20%↓ 이상 저평가      | ≤ -0.20   | **+6**    | +3.5pt              |
| 10~20% 저평가         | ≤ -0.10   | **+4**    | +2.4pt              |
| 5~10% 저평가          | ≤ -0.05   | **+2**    | +1.2pt              |
| ±5% 중립              | < +0.05   | **0**     | 0pt                 |
| 5~10% 고평가          | < +0.10   | **-2**    | -1.2pt              |
| 10~20% 고평가         | < +0.20   | **-4**    | -2.4pt              |
| 20%↑ 이상 고평가      | ≥ +0.20   | **-6**    | -3.5pt              |

> 현재 시장(Forward P/E ≈ 21x, 5Y avg 18.5x) → deviation ≈ +0.135 → **-4점** (고평가)

---

## 5. Regime Score 공식 — 100점 완전 유지

### 변경 없는 부분 (공식 자체)
```
regime_score = technical_20 + vix_20 + fng_20 + econ_20 + other_20 + phase_modifier
               (0~100 클리핑)
```

### 변경되는 부분 (other_20 내부 계산)

**변경 전**:
```python
other_raw = yield + curve + dxy + btc + gold + oil      # max ±28
other_20  = _to_20(other_raw, 28)
```

**변경 후**:
```python
other_raw = yield + curve + dxy + btc + gold + oil + forward_pe_raw   # max ±34
other_20  = _to_20(other_raw, 34)
```

> 5개 컴포넌트 합산 구조, 0~100 범위, phase_modifier 방식 — 모두 **무변경**

---

## 6. 아키텍처 변경사항

### 6-1. DB 마이그레이션 (`scripts/migrate_forward_pe.py` 신규)

```python
from repositories.database import session_scope
from sqlalchemy import text

with session_scope() as session:
    session.execute(text(
        "ALTER TABLE market_regime_history ADD COLUMN forward_pe REAL"
    ))
print("Migration complete: forward_pe column added")
```

### 6-2. `models/stock_meta.py` — MarketRegimeHistory ORM

```python
forward_pe: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
```

### 6-3. `services/market/macro_service.py`

#### (a) `_get_forward_pe()` 신규

```python
@staticmethod
def _get_forward_pe() -> float | None:
    """SPY forward P/E 조회 (yfinance). 실패 시 None 반환."""
    try:
        val = yf.Ticker("SPY").info.get("forwardPE")
        return float(val) if val and val > 0 else None
    except Exception:
        return None
```

#### (b) `_get_avg_5y_forward_pe()` 신규

```python
@classmethod
def _get_avg_5y_forward_pe(cls) -> float:
    """DB에서 최근 5년 forward_pe 평균. 데이터 30개 미만 시 18.5 반환."""
    avg = StockMetaRepo.get_avg_forward_pe_5y()
    return avg if avg is not None else 18.5
```

#### (c) `_calc_forward_pe_raw()` — 4-1 참조

#### (d) `_calc_composite_20()` 수정 (other_20 계산 함수)

파라미터에 `forward_pe: float | None = None`, `avg_5y_pe: float | None = None` 추가.

```python
@classmethod
def _calc_composite_20(
    cls, us_10y_yield, yield_spread, btc_ret, dxy_ret, gold_ret, oil_ret,
    forward_pe: float | None = None,       # 신규
    avg_5y_pe: float | None = None,        # 신규
) -> tuple[int, dict]:
    ...
    forward_pe_raw = cls._calc_forward_pe_raw(forward_pe, avg_5y_pe)  # 신규
    other_raw = yield_score + curve_score + dxy_score + btc_score + gold_score + oil_score + forward_pe_raw
    other_20  = MacroService._to_20(other_raw, 34)   # max_val 28 → 34
    return other_20, {
        "yield_score": yield_score, "curve_score": curve_score,
        "dxy_score": dxy_score, "btc_score": btc_score,
        "gold_score": gold_score, "oil_score": oil_score,
        "forward_pe_raw": forward_pe_raw,   # 신규 — UI/로깅용
    }
```

#### (e) `_get_market_regime()` 수정

```python
# 데이터 수집 단계에 추가
forward_pe = cls._get_forward_pe()
avg_5y_pe  = cls._get_avg_5y_forward_pe()
# _calculate_all_regime_components 호출 시 전달
```

#### (f) `_calculate_all_regime_components()` 수정

`forward_pe`, `avg_5y_pe` 파라미터 추가 → `_calc_composite_20()` 호출 시 전달.

#### (g) `_build_regime_schema()` — OtherScores 모델에 필드 추가

API 응답으로 forward P/E 데이터 노출 (UI 렌더링용):

```python
# OtherScores 스키마 (models/schemas.py) 에 추가
forward_pe: float | None = None        # 현재 Forward P/E
avg_5y_pe: float | None = None         # 5년 평균
forward_pe_deviation: float | None = None   # 편차 (%)
forward_pe_raw: int = 0                # 점수 기여 (-6~+6)
```

### 6-4. `repositories/stock_meta_repo.py` — `get_avg_forward_pe_5y()` 신규

```python
@staticmethod
def get_avg_forward_pe_5y() -> float | None:
    MIN_COUNT = 30
    cutoff = (datetime.now() - timedelta(days=5*365)).strftime("%Y-%m-%d")
    with session_ro() as session:
        rows = session.execute(
            text("""
                SELECT AVG(forward_pe), COUNT(*)
                FROM market_regime_history
                WHERE date >= :cutoff AND forward_pe IS NOT NULL
            """),
            {"cutoff": cutoff},
        ).fetchone()
        if rows and rows[1] >= MIN_COUNT:
            return float(rows[0])
        return None
```

### 6-5. `models/schemas.py` — RegimeComponents 수정

```python
class RegimeComponents(BaseModel):
    ...
    forward_pe: float | None = None      # 신규
    avg_5y_pe: float | None = None       # 신규
```

---

## 7. UI 변경사항 (`static/js/app.js`)

### 7-1. Macro Bar 칩 추가 (`renderMacroBar`)

Forward P/E 칩을 Fear&Greed 다음에 추가:

```javascript
// r.components.other_detail 에서 꺼냄
const od = (data.market_regime?.components?.other_detail) || {};
const fwdPe = od.forward_pe;
const avgPe = od.avg_5y_pe;

// chips 배열에 추가
{
  label: 'Fwd P/E',
  val: fwdPe != null ? fwdPe.toFixed(1) + 'x' : '-',
  sub: avgPe != null ? `avg ${avgPe.toFixed(1)}x` : null,
  cls: od.forward_pe_raw > 0 ? 'up' : od.forward_pe_raw < 0 ? 'down' : ''
},
```

표시 예시:
```
[ Fwd P/E  ]
[ 21.3x    ]   ← red (고평가)
[ avg 18.5x]
```

### 7-2. Regime Components 패널 레이블 변경 (`renderRegimeScore`)

```javascript
// 변경 전
{ name:'기타 (금리·BTC·DXY·Gold)', key:'other', detail: [...].join(' | ') }

// 변경 후
{ name:'기타 (금리·BTC·DXY·Gold·P/E)', key:'other', detail: [
    od.us_10y_yield != null ? `10Y ${od.us_10y_yield}%` : '',
    od.yield_spread_10y2y != null ? `스프레드 ${fmtPct(od.yield_spread_10y2y)}` : '',
    od.btc_1m_ret != null ? `BTC ${fmtPct(od.btc_1m_ret)}` : '',
    od.dxy_1m_ret != null ? `DXY ${fmtPct(od.dxy_1m_ret)}` : '',
    od.gold_1m_ret != null ? `Gold ${fmtPct(od.gold_1m_ret)}` : '',
    od.forward_pe != null ? `P/E ${od.forward_pe.toFixed(1)}x` : '',   // 신규
].filter(Boolean).join(' | ') }
```

### 7-3. Macro Tab other_detail 섹션 확장 (`renderMacroDetail`)

기존 `macro-other-detail` 섹션 마지막에 추가:

```javascript
// 기존 항목들(10Y 금리, 스프레드, VIX 1M, BTC, DXY, Gold) 이후에 추가
${od.forward_pe != null ? `
  <div class="score-row">
    <span style="color:var(--sub)">S&P500 Forward P/E</span>
    <b class="mono">${od.forward_pe.toFixed(1)}x</b>
  </div>
  <div class="score-row">
    <span style="color:var(--sub)">5Y 평균 P/E</span>
    <b class="mono">${(od.avg_5y_pe??18.5).toFixed(1)}x</b>
  </div>
  <div class="score-row">
    <span style="color:var(--sub)">밸류에이션 편차</span>
    <b class="mono ${od.forward_pe_raw>0?'up':od.forward_pe_raw<0?'down':''}">
      ${od.forward_pe_deviation!=null ? fmtPct(od.forward_pe_deviation) : '-'}
      &nbsp;(${od.forward_pe_raw>0?'+':''}${od.forward_pe_raw??0}점)
    </b>
  </div>` : ''}
```

표시 예시 (Macro 탭 other 섹션):
```
S&P500 Forward P/E   21.3x
5Y 평균 P/E          18.5x
밸류에이션 편차       +13.5%  (-4점)   ← red
```

---

## 8. 변경 파일 요약

| 파일 | 변경 유형 |
|------|----------|
| `services/market/macro_service.py` | `_calc_forward_pe_raw()` 추가, `_calc_composite_20()` 수정 |
| `repositories/stock_meta_repo.py` | `get_avg_forward_pe_5y()` 추가 |
| `models/stock_meta.py` | `forward_pe` 컬럼 추가 |
| `models/schemas.py` | OtherScores, RegimeComponents 필드 추가 |
| `static/js/app.js` | Macro Bar 칩, Components 레이블, Macro Detail 섹션 수정 |
| `scripts/migrate_forward_pe.py` | 신규 마이그레이션 스크립트 |

`calculate_historical_regime()` (백테스트) — forward_pe/avg_5y_pe 미전달 시 기본값 0 처리 → **하위호환 유지**.

---

## 9. 주의사항

1. **yfinance forwardPE 신뢰도**: 애널리스트 컨센서스 추정치. 분기 실적 발표 직후 급변 가능.
2. **부트스트랩 기간**: 데이터 30개(≈1개월) 미만 동안 18.5 fallback 사용.
3. **기존 점수 희석**: max_val 28→34 로 기존 yield/curve 등 가중치 약 18% 희석. 허용 범위 판단.
4. **점수 영향**: Forward P/E 단독 최대 ±3.5pt. Bull(65)/Bear(40) 임계값 근처에서 결정적 역할 가능.

---

**담당**: MacroService + UI
**예상 작업량**: 중 (함수 4개 추가/수정 + DB 마이그레이션 + UI 3곳 수정)
