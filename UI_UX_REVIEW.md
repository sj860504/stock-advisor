# Stock Advisor Pro — UI/UX 리뷰 보고서

**브랜치**: `new_ui`  
**분석 일자**: 2026-04-18  
**분석 방법**: design-critique 스킬 + accessibility-review 스킬 + 실제 화면 스크린샷 직접 분석 (localhost:8000)

---

## 목차

1. [디자인 크리틱 (Design Critique)](#1-디자인-크리틱)
2. [접근성 감사 (WCAG 2.1 AA)](#2-접근성-감사)
3. [실제 화면 시각적 분석](#3-실제-화면-시각적-분석)
4. [구현 우선순위 로드맵](#4-구현-우선순위-로드맵)
5. [CSS 개선 샘플 코드](#5-css-개선-샘플-코드)
6. [HTML 개선 샘플 코드](#6-html-개선-샘플-코드)

---

## 1. 디자인 크리틱

### 현재 프로젝트 현황

| 항목 | 내용 |
|------|------|
| 프론트엔드 구조 | `index.html` (809줄) + `app.css` (551줄) + `app.js` (1752줄) — 단일 파일 SPA |
| 탭 구조 | Dashboard / History / Market / Trading / Macro / Settings / Logs (7개) |
| CSS 시스템 | CSS 변수 기반 다크 테마 (`--accent`, `--bull`, `--bear` 등 잘 정의됨) |
| 아이콘 | lucide@latest 이미 import됨 — 현재 이모지로만 사용 중 |

### 첫인상

GNB의 **전략 ON/OFF 칩**이 첫 시선을 잡으며 핵심 상태를 즉시 전달한다. 색상 시스템과 다크 테마 구성은 금융 대시보드에 적합하나, 정보 밀도가 매우 높아 Dashboard 탭 진입 시 인지 부하가 크다.

### 사용성 (Usability)

| 발견 사항 | 심각도 | 권장 조치 |
|-----------|--------|-----------|
| Market 탭 Top100 테이블 `</table>` 닫힘 태그 누락 | 🔴 Critical | 즉시 수정 — 브라우저별 렌더링 오류 가능성 |
| Trading 탭 전량 매도 버튼이 일반 매도와 동일 레벨 배치 | 🔴 Critical | 물리적 위치 분리 + 이중 confirm 모달 적용 |
| Dashboard에 KPI·잔고·보유종목·섹터 전부 집약 | 🟡 Moderate | 잔고·섹터는 collapsed 섹션으로 처리 |
| 7개 탭 동일 비중 나열 — 핵심/보조 구분 없음 | 🟡 Moderate | Dashboard·Trading은 Primary, Logs·Settings는 Secondary 그룹으로 시각 구분 |
| 로그아웃 버튼이 전략 상태 칩과 혼재 | 🟢 Minor | 우측 끝 또는 드롭다운 메뉴로 이동 |

### 일관성 (Consistency)

| 요소 | 문제 | 권장 조치 |
|------|------|-----------|
| 인라인 스타일 남용 | HTML 전체에 `style=""` 속성 80+ 곳 반복 | `.flex-row`, `.gap-8` 등 유틸리티 클래스로 추출 |
| 폰트 크기 난립 | `.71rem` ~ `.8rem` 7~8가지 혼용 | xs/sm/base/lg/xl 5단계 스케일로 표준화 |
| `btn-xs` 클래스 미정의 | HTML 참조 있으나 CSS 정의 없음 → 브라우저 폴백 | CSS에 `btn-xs` 정의 추가 |
| 이모지 vs 아이콘 혼용 | 카드 제목 이모지 사용 기준 불일치 | lucide 아이콘으로 통일 (이미 import됨) |
| 모달 폭 하드코딩 | 모든 모달이 `width: 420px` 고정 | sm/md/lg 모달 변형 지원 |

### 잘 되어 있는 점

- CSS 변수 시스템(`--accent`, `--bull`, `--bear` 등)이 잘 설계되어 테마 확장 기반 탄탄
- `flash-bull` / `flash-bear` 애니메이션으로 실시간 가격 변동 직관적 표시
- `status-dot` 펄스 애니메이션으로 전략 활성 상태 시각화
- 정렬 가능한 테이블 헤더 + 검색 필터 일관 적용
- 커스텀 스크롤바로 전체적 미감 일치

---

## 2. 접근성 감사

**기준**: WCAG 2.1 AA  
**총 발견**: 18건 — Critical 6 / Major 8 / Minor 4

### 색상 대비 결과

| 요소 | 전경 | 배경 | 비율 | 기준 | 통과 |
|------|------|------|------|------|------|
| 본문 텍스트 `--text` | #e8edf5 | #0e1420 | **14.4:1** | 4.5:1 | ✅ |
| 보조 텍스트 `--sub` | #7b8fa6 | #0e1420 | **5.1:1** | 4.5:1 | ✅ |
| 보조 텍스트 `--sub` (nav) | #7b8fa6 | #0a0e18 | **5.7:1** | 4.5:1 | ✅ |
| 액센트 `--accent` | #00d4aa | #0e1420 | **9.1:1** | 4.5:1 | ✅ |
| 버튼 텍스트 (primary) | #06111c | #00d4aa | **9.4:1** | 4.5:1 | ✅ |
| Bull `--bull` | #00e676 | #0e1420 | **11.2:1** | 4.5:1 | ✅ |
| Bear `--bear` | #ff5252 | #0e1420 | **6.8:1** | 4.5:1 | ✅ |

> **결론**: 색상 대비는 전항목 통과. 핵심 문제는 대비율이 아닌 **폰트 크기**와 **색상만으로 정보 전달**하는 방식.

### Critical 이슈 (6건)

| # | 이슈 | WCAG | 권장 조치 |
|---|------|------|-----------|
| 1 | 폰트 크기 0.71~0.78rem (11~12.5px) — `.mc-label`, `.badge` 등 다수 | 1.4.4 Resize Text | 최소 `0.875rem(14px)`으로 상향 |
| 2 | `nav-item`이 `<div>` — 키보드 Tab 접근 불가, role/tabindex 없음 | 2.1.1 Keyboard | `<button>` 태그로 교체, `role="tab"` + `tabindex="0"` |
| 3 | 모달 포커스 트랩 미구현 — 모달 오픈 시 배경 요소 Tab 이동 가능 | 2.1.2 No Keyboard Trap | open 시 `focus()` 이동 + Tab 이벤트 내부 루핑 구현 |
| 4 | CSS에 `:focus-visible` 스타일 미정의 — 시각적 포커스 인디케이터 없음 | 2.4.7 Focus Visible | `:focus-visible { outline: 2px solid var(--accent); }` 전역 적용 |
| 5 | `#login-error` DOM 업데이트 시 스크린리더 미알림 | 3.3.1 Error ID | `role="alert"` + `aria-live="polite"` 추가 |
| 6 | 모달 6개에 `role="dialog"`, `aria-modal`, `aria-labelledby` 모두 없음 | 4.1.2 Name, Role, Value | 각 `.modal-box`에 ARIA 속성 추가 |

### Major 이슈 (8건)

| # | 이슈 | WCAG | 권장 조치 |
|---|------|------|-----------|
| 7 | 색상만으로 매수/매도 정보 전달 (`.up`, `.down`, `b-up`, `b-down`) | 1.4.1 Use of Color | 색상 + 텍스트 또는 아이콘(▲/▼) 함께 사용 |
| 8 | `strategy-dot` 펄스 점 — 텍스트 대안 없음 | 1.1.1 Non-text Content | `aria-hidden="true"` + 인접 텍스트 대안 추가 |
| 9 | `btn-sm` 패딩 `4px 10px` — 터치 타깃 ~28px (기준 44px 미달) | 2.5.5 Target Size | `padding: 8px 12px` 이상으로 확대 |
| 10 | `btn-xs` HTML 참조 있으나 CSS 정의 없음 | 2.1.1 Keyboard | CSS에 `btn-xs` 정의 추가 |
| 11 | `#toast-container`에 `aria-live` 없어 알림 스크린리더 미전달 | 3.3.1 Error ID | `aria-live="polite"` + `aria-atomic="true"` 추가 |
| 12 | `.tbl-search <input>`에 `<label>` 연결 없이 `placeholder`만 사용 | 3.3.2 Labels | `aria-label="종목 검색"` 추가 |
| 13 | DCF `<input type="range">`에 `for`/`id` 연결 없음 | 3.3.2 Labels | `id` 부여 후 `<label for="">` 연결 |
| 14 | `<table>`에 `caption` 및 `aria-label` 없음 | 1.3.1 Info and Structure | `aria-label="보유 종목 목록"` 등 추가 |

### Minor 이슈 (4건)

| # | 이슈 | WCAG | 권장 조치 |
|---|------|------|-----------|
| 15 | 정렬 화살표 `▲` `▼` 유니코드 — 스크린리더가 문자명으로 읽음 | 1.3.1 Info and Structure | `aria-sort="ascending/descending"` 속성으로 대체 |
| 16 | `flash-bull`/`flash-bear` 애니메이션 — `prefers-reduced-motion` 미처리 | 2.3.1 Three Flashes | `prefers-reduced-motion` 미디어 쿼리 추가 |
| 17 | `<table>` 태그 내부 nav-item이 `<div>` — 시맨틱 구조 불완전 | 4.1.2 Name, Role, Value | `<button>` 또는 `<a>` 태그로 교체 |
| 18 | `<meta name="description">` 없음 | 4.1.1 Parsing | SEO 및 접근성 지원을 위해 메타 description 추가 |

---

## 3. 실제 화면 시각적 분석

> localhost:8000 직접 접속 후 스크린샷 기반 추가 발견 사항 (코드 분석으로 미발견)

### 🔴 즉시 수정

#### RSI "0.0" 뱃지가 초록색 표시
- **위치**: 보유 종목 테이블 RSI 컬럼 (HLB 028300, 카카오페이 377300 등)
- **문제**: RSI 데이터 미존재 시 `0.0`을 초록색 배지로 표시 → 좋은 신호로 오독 가능
- **수정**: RSI가 `0` 또는 `null`이면 회색 배지 `"-"` 으로 표시

#### 매도 / 삭제 버튼 동일 색·동일 크기로 나란히
- **위치**: 보유 종목 테이블 우측 액션 버튼
- **문제**: 두 버튼 모두 동일한 `btn-danger` 빨간색, 붙어있어 잘못 클릭 위험 매우 높음. "삭제"가 "매도"보다 훨씬 위험한 동작임에도 시각적 구분 없음
- **수정**: "삭제"는 아이콘만 표시(🗑 또는 lucide Trash2)하거나 테이블 행 hover 시에만 노출, 또는 confirm 없이 삭제 불가 처리

### 🟡 개선 권장

#### 섹터 컬럼 전부 "미분류/ET ↓"
- **문제**: 실질적으로 데이터가 없는 컬럼이 테이블 공간을 차지하며 노이즈로 작용
- **수정**: 미분류 항목을 강조 표시하거나, 입력 유도 tooltip 추가. 또는 기본값 컬럼 숨김 처리 옵션 제공

#### 쿨다운 컬럼 전부 "-"
- **문제**: 활성 쿨다운이 없는 경우 컬럼 전체가 "-"만 표시 → 불필요한 열 공간 낭비
- **수정**: 쿨다운 없을 때는 컬럼 숨김 또는 "없음" 표기 제거, 쿨다운 있는 행만 강조

#### "DCF" 버튼 불명확
- **문제**: 각 행에 "DCF" 텍스트 버튼이 있으나 hover tooltip 없어 기능 불명확
- **수정**: `title` 속성 또는 툴팁으로 "DCF 적정가 수동 설정" 설명 추가

#### 총 수익 "₩0" 초록색
- **문제**: KPI 카드에서 총 수익이 ₩0일 때 초록색으로 표시 — ₩0은 긍정도 부정도 아님
- **수정**: 값이 0이면 `--sub` 회색으로 처리

#### 매크로 바 레이블 가독성
- **문제**: REGIME, SPX VS MA200 등 레이블이 실제 화면에서 매우 작게 보임 (0.71rem)
- **수정**: 최소 0.75rem으로 상향, 또는 레이블을 값 아래가 아닌 tooltip으로 이동해 값을 더 크게 표시

---

## 4. 구현 우선순위 로드맵

### Phase 1 — 버그 수정 및 Critical 접근성 (즉시, ~1시간)

| 순서 | 작업 | 파일 |
|------|------|------|
| 1 | `</table>` 닫힘 태그 누락 수정 | `index.html` |
| 2 | `:focus-visible` 전역 스타일 추가 | `app.css` |
| 3 | `btn-xs` CSS 정의 추가 | `app.css` |
| 4 | `#login-error`에 `role="alert"` + `aria-live` 추가 | `index.html` |
| 5 | `#toast-container`에 `aria-live` 추가 | `index.html` |
| 6 | RSI `0.0` → 회색 "-" 처리 | `app.js` |

### Phase 2 — UI 일관성 및 UX 안전성 (단기, 1~2일)

| 순서 | 작업 | 파일 |
|------|------|------|
| 7 | 타이포그래피 스케일 5단계 표준화 | `app.css` |
| 8 | `nav-item` → `<button>` 태그 교체 | `index.html` + `app.js` |
| 9 | `btn-sm` 터치 타깃 확대 (`padding: 8px 12px`) | `app.css` |
| 10 | 삭제 버튼 시각 분리 (아이콘화 또는 hover 노출) | `index.html` + `app.css` |
| 11 | 총 수익 ₩0 → 회색 처리 | `app.js` |
| 12 | `prefers-reduced-motion` 쿼리 추가 | `app.css` |
| 13 | lucide 아이콘으로 카드 제목 이모지 대체 | `index.html` |

### Phase 3 — 정보 구조 및 접근성 완성 (중기, 3~5일)

| 순서 | 작업 | 파일 |
|------|------|------|
| 14 | 모달 6개 ARIA 속성 추가 + 포커스 트랩 | `index.html` + `app.js` |
| 15 | 색상+아이콘 병행 표시 (매수▲/매도▼) | `app.js` |
| 16 | Dashboard 잔고·섹터 collapsed 섹션 처리 | `index.html` + `app.js` |
| 17 | 반복 인라인 스타일 → 유틸리티 클래스 추출 | `app.css` + `index.html` |
| 18 | "DCF" 버튼 툴팁 추가 | `index.html` |
| 19 | 섹터 컬럼 미분류 항목 처리 개선 | `app.js` |

---

## 5. CSS 개선 샘플 코드

### 타이포그래피 스케일 표준화

```css
/* Typography Scale — 현재 .71rem ~ .8rem 난립을 5단계로 통일 */
:root {
  --fs-xs:   0.75rem;    /* 12px — 보조 레이블 최소 */
  --fs-sm:   0.8125rem;  /* 13px — 소형 배지, 버튼 */
  --fs-base: 0.875rem;   /* 14px — 일반 텍스트 */
  --fs-md:   1rem;       /* 16px — 기본 */
  --fs-lg:   1.05rem;    /* 16.8px — 카드 제목 */
}
```

### `:focus-visible` 전역 스타일 (Critical #4)

```css
/* 키보드 포커스 인디케이터 */
*:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
  border-radius: 4px;
}
/* 마우스 클릭 시 outline 제거 */
*:focus:not(:focus-visible) {
  outline: none;
}
```

### `prefers-reduced-motion`

```css
@media (prefers-reduced-motion: reduce) {
  .flash-bull, .flash-bear { animation: none; }
  .status-dot.on           { animation: none; }
  .page-section            { animation: none; }
  *                        { transition-duration: 0.01ms !important; }
}
```

### `btn-xs` 클래스 추가

```css
/* btn-xs — HTML 참조 있으나 CSS 미정의 */
.btn-xs {
  padding: 6px 10px;
  font-size: var(--fs-sm, 0.8125rem);
  min-height: 32px;
}
```

---

## 6. HTML 개선 샘플 코드

### 모달 ARIA 속성 (Critical #6)

```html
<!-- Before -->
<div id="tradeModal" class="modal-backdrop">
  <div class="modal-box">
    <div class="modal-title">매매 기록 추가</div>

<!-- After -->
<div id="tradeModal" class="modal-backdrop"
     role="dialog" aria-modal="true" aria-labelledby="trade-modal-title">
  <div class="modal-box">
    <div class="modal-title" id="trade-modal-title">매매 기록 추가</div>
```

### `nav-item` → `<button>` 교체 (Critical #2)

```html
<!-- Before -->
<div class="nav-item active" data-tab="dashboard"
     onclick="switchTab(this,'dashboard')">Dashboard</div>

<!-- After -->
<button class="nav-item active" type="button" data-tab="dashboard"
        onclick="switchTab(this,'dashboard')"
        role="tab" aria-selected="true">Dashboard</button>
```

### `aria-live` 오류 영역 (Critical #5)

```html
<!-- Before -->
<div class="login-error" id="login-error"></div>

<!-- After -->
<div class="login-error" id="login-error"
     role="alert" aria-live="polite" aria-atomic="true"></div>
```

### RSI 0.0 → 회색 처리 (app.js)

```javascript
// RSI 렌더링 시
function renderRsi(value) {
  if (!value || value === 0) {
    return `<span class="badge b-gray">-</span>`;
  }
  const cls = value >= 70 ? 'b-down' : value <= 30 ? 'b-up' : 'b-gray';
  return `<span class="badge ${cls}">${value.toFixed(1)}</span>`;
}
```

---

## 이슈 요약

| 구분 | 건수 |
|------|------|
| 🔴 Critical (즉시 수정) | **8건** |
| 🟡 Major / Moderate | **12건** |
| 🟢 Minor | **5건** |
| **총합** | **25건** |

---

*본 보고서는 design-critique + accessibility-review 스킬(정적 코드 분석) + localhost:8000 실제 화면 스크린샷 분석을 종합한 결과입니다.*  
*실제 브라우저 테스트 (VoiceOver, NVDA, Chrome Lighthouse) 후 추가 이슈가 발견될 수 있습니다.*
