# API Reference

**프론트엔드-백엔드 엔드포인트 매핑 + 인증 흐름**

---

## 목차

1. [인증](#1-인증)
2. [라우터별 엔드포인트](#2-라우터별-엔드포인트)
3. [UI 탭별 API 매핑](#3-ui-탭별-api-매핑)
4. [WebSocket](#4-websocket)

---

## 1. 인증

### JWT 인증 흐름

```
POST /api/auth/login
  Body: { username, password }
  Response: { access_token, token_type: "bearer" }

이후 모든 요청:
  Header: Authorization: Bearer <access_token>
```

### 설정

| 설정 | 기본값 |
|------|--------|
| `JWT_SECRET` | `change-me-in-production` |
| `JWT_ALGORITHM` | `HS256` |
| `JWT_EXPIRE_HOURS` | `24` |
| `AUTH_USERNAME` | `sean` |

---

## 2. 라우터별 엔드포인트

### /api/trading

| Method | Path | 설명 | 핵심 서비스 |
|--------|------|------|-----------|
| POST | `/order` | 수동 매수/매도 주문 | `OrderService.sell_single_holding()` |
| GET | `/balance` | 잔고 조회 (포트폴리오 분석 포함) | `PortfolioService.sync_with_kis()` |
| GET | `/waiting-list` | 현재 BUY/SELL 신호 목록 | `TradingStrategyService.get_waiting_list()` |
| GET | `/history` | 매매 내역 (market/date 필터) | `OrderService.get_trade_history()` |
| POST | `/sell` | 수동 매도 실행 | `TradingStrategyService.execute_sell()` |
| GET | `/settings` | 전략 설정값 전체 조회 | `SettingsService.get_all_settings()` |
| PUT | `/settings` | 단일 설정값 수정 | `SettingsService.set_setting()` |
| GET | `/start` | 자동매매 시작 | `TradingStrategyService.set_enabled(True)` |
| GET | `/stop` | 자동매매 중지 | `TradingStrategyService.set_enabled(False)` |
| GET | `/tick-settings` | 틱매매 설정 조회 | `SettingsService.get_tick_settings()` |
| PUT | `/tick-settings` | 틱매매 설정 수정 | `SettingsService.update_tick_settings()` |
| POST | `/backtest/portfolio` | 포트폴리오 백테스트 실행 | `BacktestService.run_portfolio_backtest()` |
| POST | `/sell-all-and-rebuy` | 전량 매도 후 재매수 | `TradingStrategyService.sell_all_and_rebuy()` |

---

### /api/market

| Method | Path | 설명 | 핵심 서비스 |
|--------|------|------|-----------|
| GET | `/monitored` | 모니터링 종목 현재 상태 | `MarketDataService.get_all_states()` |
| GET | `/` | 시장 요약 (KOSPI, KOSDAQ, 환율) | `MarketOverviewService` |
| GET | `/news/{ticker}` | 관련 뉴스 조회 | `NewsService.get_latest_news()` |
| GET | `/signals` | 거래 신호 (과매도/과매수/저평가) | `MarketDataService.build_trading_signals()` |
| GET | `/macro` | 거시 지표 & 시장 레짐 | `MacroService.get_macro_data()` |
| GET | `/calendar/weekly` | 경제지표 발표 일정 (7일) | `EconomicCalendarService.get_weekly_calendar()` |
| GET | `/regime/history` | 시장 레짐 이력 (30일) | `StockMetaService.get_market_regime_history()` |
| GET | `/regime/{date}` | 특정 날짜 레짐 | `StockMetaService.get_regime_for_date()` |
| GET | `/watching` | 감시 중인 종목 목록 | `MarketDataService.get_watch_list()` |

---

### /api/analysis

| Method | Path | 설명 | 핵심 서비스 |
|--------|------|------|-----------|
| GET | `/valuation/{ticker}` | 종합 분석 리포트 | `AnalysisService.get_comprehensive_report()` |
| GET | `/returns/{ticker}` | 수익률 & MDD 분석 | `PortfolioService.analyze_portfolio()` |
| GET | `/metrics/{ticker}` | 재무 지표 (PER, PBR, ROE) | `FinancialService.get_metrics()` |
| GET | `/dcf` | 전 종목 DCF 목록 | `DcfService.get_filtered_list()` |
| GET | `/dcf/{ticker}` | 특정 종목 DCF 현재값 | `DcfService.calculate_dcf()` |
| GET | `/dcf-custom` | 커스텀 파라미터 DCF | `DcfService.calculate_custom_dcf()` |
| PUT | `/dcf-override` | DCF 오버라이드 저장 | `DcfService.save_override()` |
| PUT | `/strategy/weights` | 종목별 점수 가중치 설정 | `TradingStrategyService.set_top_weight_overrides()` |
| GET | `/sector-weights` | 섹터 비중 & 리밸런싱 현황 | `TradingStrategyService.get_sector_rebalance_status()` |
| GET | `/score/{ticker}` | 전략 점수 + 추천 + 근거 | `TradingStrategyService.analyze_ticker()` |

---

### /api/portfolio

| Method | Path | 설명 | 핵심 서비스 |
|--------|------|------|-----------|
| POST | `/upload` | 엑셀 포트폴리오 업로드 | `PortfolioService.upload_portfolio()` |
| GET | `/{user_id}` | 저장된 포트폴리오 조회 | `PortfolioService.load_portfolio_dtos()` |
| GET | `/{user_id}/analysis` | 포트폴리오 수익률 분석 | `PortfolioService.analyze_portfolio()` |
| GET | `/{user_id}/full-report` | 보유종목 전체 상세 분석 | `PortfolioService.build_full_report()` |
| POST | `/{user_id}/add` | 보유종목 수동 추가 | `PortfolioService.add_holding_manual()` |
| PATCH | `/{user_id}/{ticker}/sector` | 섹터 수동 변경 | `PortfolioRepo.update_sector()` |
| DELETE | `/{user_id}/{ticker}` | 보유종목 삭제 | `PortfolioRepo` |
| POST | `/{user_id}/trade` | 통합 매수/매도 처리 | `PortfolioService.apply_trade_action()` |

---

### /api/alerts

| Method | Path | 설명 |
|--------|------|------|
| POST | `/` | 가격 알림 등록 |
| GET | `/` | 등록된 알림 목록 |
| DELETE | `/{alert_id}` | 알림 삭제 |

---

### /api/reports

| Method | Path | 설명 |
|--------|------|------|
| GET | `/daily` | 일일 거래 요약 리포트 |
| GET | `/portfolio` | 포트폴리오 Slack 리포트 전송 |

---

### /api/auth

| Method | Path | 설명 |
|--------|------|------|
| POST | `/login` | 로그인 → JWT 발급 |
| GET | `/me` | 현재 사용자 정보 |

---

### /api/logs

| Method | Path | Query | 설명 |
|--------|------|-------|------|
| GET | `/` | `lines`, `level`, `search` | 서버 로그 조회 (마지막 N줄, 레벨/검색 필터) |

---

## 3. UI 탭별 API 매핑

| UI 탭 | 사용 API | 설명 |
|-------|---------|------|
| **대시보드** | `GET /api/market/monitored` | 실시간 가격 테이블 |
| **대시보드** | `GET /api/market/` | 시장 요약 (KOSPI, 환율) |
| **대시보드** | `GET /api/trading/waiting-list` | BUY/SELL 신호 |
| **포트폴리오** | `GET /api/portfolio/{user_id}` | 보유 종목 목록 |
| **포트폴리오** | `GET /api/portfolio/{user_id}/analysis` | 수익률 분석 |
| **포트폴리오** | `GET /api/portfolio/{user_id}/full-report` | 종목별 상세 리포트 |
| **분석** | `GET /api/analysis/valuation/{ticker}` | 종목 종합 분석 |
| **분석** | `GET /api/analysis/score/{ticker}` | 전략 점수 |
| **분석** | `GET /api/analysis/dcf` | DCF 목록 |
| **섹터** | `GET /api/analysis/sector-weights` | 섹터 비중 현황 |
| **거시** | `GET /api/market/macro` | VIX, F&G, 레짐 |
| **거시** | `GET /api/market/regime/history` | 레짐 이력 차트 |
| **캘린더** | `GET /api/market/calendar/weekly` | 경제지표 일정 |
| **설정** | `GET /api/trading/settings` | 전략 파라미터 |
| **설정** | `PUT /api/trading/settings` | 파라미터 수정 |
| **로그** | `GET /api/logs` | 서버 로그 |
| **백테스트** | `POST /api/trading/backtest/portfolio` | 백테스트 실행 |

---

## 4. WebSocket

### KIS WebSocket (실시간 가격)

```
서버 내부 전용 (클라이언트 직접 접근 불가)
- 연결: KisWsService.connect() → 별도 데몬 스레드
- 구독 코드: H0STCNT0 (국내), HDFSUSP0 (해외)
- 최대 40개 동시 구독 (KR 20 + US 20)
- 수신 데이터 → MarketDataService.on_realtime_data()
```

### 클라이언트 WebSocket (대시보드)

```
ws://{host}/ws/market
  - 서버 → 클라이언트 실시간 가격 브로드캐스트
  - 전략 실행 이벤트 알림
```

---

## 5. 주요 Request/Response 스키마

### POST /api/trading/order
```json
Request:
{
  "ticker": "005930",
  "order_type": "buy",
  "quantity": 10,
  "price": 75000
}
Response:
{
  "success": true,
  "message": "주문 완료",
  "order_id": "..."
}
```

### GET /api/analysis/score/{ticker}
```json
Response:
{
  "ticker": "AAPL",
  "score": 35,
  "recommendation": "BUY",
  "reasons": ["RSI 과매도(28)", "DCF 저평가(15%)"],
  "breakdown": {
    "technical": -20,
    "portfolio": -10,
    "market_context": -15,
    "target_prices": 0,
    "bonuses": -10
  }
}
```

### GET /api/market/macro
```json
Response:
{
  "vix": 18.5,
  "fear_greed": 45,
  "us_10y_yield": 4.2,
  "regime": {
    "status": "NEUTRAL",
    "score": 55,
    "components": {...}
  },
  "economic_indicators": {...}
}
```

### POST /api/trading/backtest/portfolio
```json
Request:
{
  "tickers": ["AAPL", "MSFT", "005930"],
  "years": 3,
  "initial_capital": 10000000,
  "position_pct": 0.3,
  "target_cash_ratio": 0.2
}
Response:
{
  "config": {...},
  "results": {
    "sharpe_ratio": 1.25,
    "mdd": -0.18,
    "win_rate": 0.62,
    "total_return": 0.45
  },
  "equity_curve": [...],
  "trades": [...]
}
```

---

**Last Updated**: 2026-03-12
