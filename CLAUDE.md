# CLAUDE.md

**Quick-start guide for Claude Code - Complete details in linked docs**

---

## Project Overview

**003_quant** - FastAPI application for 한국/미국 주식 자동매매, Regime 기반 전략, 매크로 분석

**Tech Stack**: FastAPI, APScheduler, SQLite/SQLAlchemy, KIS API, FRED API, yfinance

---

## Session Start Protocol ⚡

**MANDATORY** at start of each session:

```bash
# 1. Load essential docs (~800 tokens - 2 min read)
✓ .claude/COMMON_MISTAKES.md      # ⚠️ CRITICAL - Read FIRST
✓ .claude/QUICK_START.md          # Essential commands
✓ .claude/ARCHITECTURE_MAP.md     # File locations
```

**At task completion:**
- Create completion doc in `.claude/completions/YYYY-MM-DD-task-name.md`
- Use template: `.claude/templates/completion-template.md`
- Move session file to `.claude/sessions/archive/` (if created)
- Update docs as needed (see `.claude/DOCUMENTATION_MAINTENANCE.md`)

**Then load task-specific docs** (~500-1500 tokens):
- See `docs/INDEX.md` for navigation guide

**⚠️ NEVER auto-load:**
- Files in `.claude/completions/` (0 token cost)
- Files in `.claude/sessions/` (0 token cost)
- Files in `docs/archive/` (0 token cost)
- Only load when user explicitly requests

---

## Quick Start Commands

```bash
# Add your common commands here
# npm run dev
# npm test
# npm run build
```

**See**: `.claude/QUICK_START.md` for complete command reference

---

## Documentation Navigation

**📋 Master Index**: `docs/INDEX.md` - Complete navigation with token costs

### Core References
- **Common Mistakes**: `.claude/COMMON_MISTAKES.md` ⚠️ **MANDATORY**
- **Quick Start**: `.claude/QUICK_START.md`
- **Architecture Map**: `.claude/ARCHITECTURE_MAP.md`
- **Maintenance**: `.claude/DOCUMENTATION_MAINTENANCE.md`

---

## Research Order Rule ⚠️ MANDATORY

**질문/분석/수정 요청 시 반드시 아래 순서를 따를 것:**

1. **문서 먼저** — `.claude/BUSINESS_LOGIC.md`, `.claude/FUNCTION_REFERENCE.md`, `.claude/ARCHITECTURE_MAP.md` 에서 관련 내용 확인
2. **소스는 나중에** — 문서에 없거나 불충분할 때만 소스 파일 직접 열람
3. **절대 금지**: 문서 확인 없이 바로 소스 파일(services/, repositories/ 등)을 읽는 행위

---

## Development Rules

- **Trade history 조회는 반드시 DB(`TradeHistoryRepo`)를 사용할 것. KIS API 히스토리 조회 사용 금지.**
- **KIS 토큰은 DB에서 조회하여 사용할 것. 파일 기반 토큰 사용 금지.**
- **가상환경 활성화**: `source /root/stock-advisor/venv/bin/activate`

---

**Last Updated**: 2026-03-10
**Optimized with**: [Claude Token Optimizer](https://github.com/nadimtuhin/claude-token-optimizer)
