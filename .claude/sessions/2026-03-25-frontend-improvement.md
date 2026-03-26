# 프론트엔드 개선 계획서

**작성일**: 2026-03-25
**참고**: [frontend-design plugin](https://github.com/anthropics/claude-code/tree/main/plugins/frontend-design)
**대상 파일**: `static/index.html` (현재 2,868줄 단일 파일)

---

## 현재 상태 진단

### 구조적 문제
- 단일 HTML 파일 2,868줄 — CSS / HTML / JS 혼재, 유지보수 불가
- 인라인 스타일 남발 (`style="display:flex;gap:12px;..."`) — CSS 변수 활용 불일관
- 이모지 의존 UI (`⚙️`, `📋`, `📊`) — 전문적 아이콘 시스템 부재

### 디자인 문제
- 다크 테마는 있으나 개성 없는 제네릭 AI 미학
- 타이포그래피: Inter 단일 폰트, 굵기 변화만으로 계층 표현 — 단조로움
- 컬러: `--accent: #3b82f6` (표준 Tailwind 블루) — 차별점 없음
- 애니메이션 부재 — 상태 변화 피드백 없음
- 차트/시각화 없음 — 수익률·포트폴리오 모두 테이블만

### 기능 공백
- Watchlist 관리 UI 없음 (신규 백엔드 기능 대응 필요)
- 실시간 가격 갱신 시 UX 피드백 없음
- 모바일 대응 없음

---

## 개선 목표

> "bold aesthetic choices, distinctive typography, high-impact animations, context-aware implementation"
> — frontend-design plugin

금융 대시보드 맥락에 맞는 **전문적이고 정보 밀도 높은 인터페이스** — Bloomberg Terminal의 정보 밀도 + 현대적 미니멀리즘

---

## 구현 현황 (2026-03-26 업데이트)

| Phase | 작업 | 상태 |
|-------|------|------|
| 1 | 파일 분리 (CSS/JS 추출) | ✅ 완료 |
| 2 | 디자인 시스템 (컬러, 폰트, 아이콘) | ✅ 완료 (app.css) |
| 3-1 | GNB 개선 (status-dot 추가) | ✅ 완료 |
| 3-3 | 테이블 수익률 바 | ✅ 완료 (app.js) |
| 3-4 | Dashboard KPI 카드 | ✅ 완료 (kpi-row div 추가) |
| 3-5 | 전략 상태 애니메이션 | ✅ 완료 (CSS + JS) |
| 3-6 | 숫자 플래시 업데이트 | ✅ 완료 (CSS flash-bull/bear) |
| 4 | Watchlist 탭 | ✅ 완료 (HTML + JS) |
| 5 | 토스트 알림 | ✅ 완료 (toast-container) |
| 6 | 버그 수정 | ✅ 완료 (2026-03-26) |

### 버그 수정 내역 (2026-03-26)

| 파일 | 버그 | 수정 |
|------|------|------|
| `static/js/app.js:131` | `updateStrategyChip()` 삼항 연산자 `: false_branch` 누락 → SyntaxError로 전체 JS 로드 실패 | 불완전한 중복 줄 제거 |
| `routers/trading.py` | `GET /trading/waiting-list` — `get_waiting_list()`가 `list` 반환인데 `response_model=Dict` → ResponseValidationError 500 | `{enabled, buy_list, sell_list}` dict로 래핑 |

### 파일 크기 변화
- `index.html`: 2,868줄 → 712줄 (마크업 전용)
- `css/app.css`: 신규 551줄 (새 디자인 시스템)
- `js/app.js`: 신규 1,430줄 (전체 로직)

---

## Phase 1 — 파일 구조 분리 ✅

```
static/
├── index.html          # 마크업만 (689줄)
├── css/
│   └── app.css         # 모든 스타일 (551줄)
└── js/
    └── app.js          # 모든 JS 로직 (1,404줄)
```

---

## Phase 2 — 디자인 시스템 ✅

### 컬러 팔레트 — 금융 전문가용

```css
:root {
  --bg:       #080b10;
  --surface:  #0e1420;
  --raised:   #141c2e;
  --accent:   #00d4aa;   /* 틸 — 기존 블루에서 변경 */
  --bull:     #00e676;
  --bear:     #ff5252;
}
```

### 타이포그래피 — 듀얼 폰트
- 수치/티커 → `JetBrains Mono` (`.mono` 클래스)
- UI 레이블 → `Inter` 유지

### 아이콘
- Lucide CDN 추가 (`https://unpkg.com/lucide@latest/dist/umd/lucide.min.js`)
- `initApp()` 에서 `lucide.createIcons()` 호출

---

## 비고

- **차트 추가 (선택)**: `Chart.js` CDN으로 포트폴리오 수익률 라인 차트, 섹터 비중 도넛 차트 추가 가능
- **모바일**: `@media (max-width: 768px)` 대응 CSS 이미 포함 (app.css 540~550줄)
- **번들러 도입 안 함**: 현재 단일 HTML 서빙 구조 유지, 빌드 파이프라인 불필요

---

**Last Updated**: 2026-03-26
