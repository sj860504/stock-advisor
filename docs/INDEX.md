# Documentation Index

**Master navigation with token cost estimates**

---

## Session Start (Essential - ~800 tokens)

Load these files at every session start:
- `CLAUDE.md` (~450 tokens)
- `.claude/COMMON_MISTAKES.md` (~350 tokens)
- `.claude/QUICK_START.md` (~100 tokens)
- `.claude/ARCHITECTURE_MAP.md` (~200 tokens)

## Core Reference Docs (Load As Needed)

| 파일 | 토큰 | 내용 |
|------|------|------|
| `.claude/FUNCTION_REFERENCE.md` | ~2500 tokens | 서비스·레포지토리 함수 상세 레퍼런스 |
| `.claude/BUSINESS_LOGIC.md` | ~800 tokens | 매매 전략·점수 계산·리스크 관리 |
| `.claude/API_REFERENCE.md` | ~600 tokens | 프론트-백 엔드포인트 매핑 |

## Task-Specific Topics

| 토픽 | 로드할 파일 |
|------|-----------|
| 매매 전략 로직 수정 | `BUSINESS_LOGIC.md` + `FUNCTION_REFERENCE.md` §4 |
| 신규 API 엔드포인트 | `API_REFERENCE.md` + `ARCHITECTURE_MAP.md` |
| KIS API 연동 | `FUNCTION_REFERENCE.md` §2 + §7(kis_fetcher) |
| DB 스키마/쿼리 | `FUNCTION_REFERENCE.md` §8~9 |
| 점수 계산 디버깅 | `BUSINESS_LOGIC.md` §2 + `FUNCTION_REFERENCE.md` §4(signal_service) |
| 섹터 리밸런싱 | `BUSINESS_LOGIC.md` §7 + `FUNCTION_REFERENCE.md` §4(sector_rebalancer) |
| 대시보드 UI | `API_REFERENCE.md` §3(UI 탭별 매핑) |

## Archive (0 tokens - load only when explicitly needed)

- `.claude/completions/` — 완료된 태스크 요약
- `.claude/sessions/` — 세션 아카이브
- `docs/archive/` — 구버전 문서
- `docs/learnings/` — 태스크별 학습 노트

---

**Last Updated**: 2026-03-12
