# Stock Advisor Architecture

## 1. Overview
**Stock Advisor**는 FastAPI 기반의 실시간 주식 분석 및 포트폴리오 관리 시스템입니다.
미국/한국 주식의 실시간 시세 모니터링, DCF(현금흐름할인법) 가치평가, 그리고 사용자 포트폴리오 수익률 분석을 수행하며 Slack으로 알림을 전송합니다.

## 2. Service Layer Structure

프로젝트는 `services/` 디렉토리 내의 전문화된 서비스 클래스들로 구성되어 있습니다.

```mermaid
graph TD
    Main[main.py (FastAPI)] --> Scheduler[SchedulerService]
    Main --> Portfolio[PortfolioService]
    Main --> Alert[AlertService]
    
    Scheduler -- 1. 주기적 실행 --> Data[DataService]
    Scheduler -- 2. 가치평가 --> Financial[FinancialService]
    Scheduler -- 3. 알림 체크 --> Alert
    Scheduler -- 4. 자산 분석 --> Portfolio
    
    Portfolio --> Data[DataService]
    Portfolio --> Ticker[TickerService]
    
    Financial --> Data
```

### 🧩 Core Services

#### 1. `SchedulerService` (The Heartbeat)
백그라운드에서 주기적인 작업을 총괄합니다.
- **`start()`**: 스케줄러 초기화 및 작업 등록.
- **`update_prices()`** (1분 간격): Top 20 및 관심 종목의 실시간 시세, RSI, EMA 업데이트.
- **`check_portfolio_hourly()`** (1시간 간격): **Webull 스타일** 등락률(Pre-market 반영)로 포트폴리오 전수 조사 및 상승 종목 리포트 생성.
- **`update_dcf_valuations()`** (30분 간격): DCF 적정주가 재계산.

#### 2. `PortfolioService` (Asset Management)
사용자의 자산을 관리합니다.
- **`parse_excel()`**: 엑셀 파일을 파싱하여 보유 종목(Ticker, 수량, 매수가) 로드.
- **`load_portfolio()` / `save_portfolio()`**: JSON 파일로 자산 데이터 영구 저장.
- **`analyze_portfolio()`**: 현재가 기반 수익률 및 섹터별 비중 분석.

#### 3. `AlertService` (Notification)
Slack 알림을 관리합니다. 서버 부하 분산을 위해 **대기열(Queue)** 방식을 사용합니다.
- **`check_and_alert()`**: RSI 과매도/과매수, DCF 저평가, 지지선(EMA) 돌파 여부 판단.
- **`send_slack_alert()`**: 알림 메시지를 메모리 내 대기열(`_pending_alerts`)에 추가.
- **`get_pending_alerts()`**: 대기 중인 알림을 반환하고 비움 (API polling용).

#### 4. `FinancialService` (Valuation)
기업의 펀더멘털을 분석합니다.
- **`get_dcf_data()`**: FCF(자유현금흐름), Beta, 성장률 데이터 수집.
- **`validate_dcf()`**: 산출된 DCF 적정가와 현재가를 비교하여 신뢰도(High/Medium/Low) 평가.

#### 5. `DataService` & `TickerService` (Infra)
- **`DataService`**: `yfinance`, `FinanceDataReader`를 통해 OHLCV 및 실시간 시세 조회.
- **`TickerService`**: 한국어 종목명(삼성전자) ↔ 티커(005930) 변환.

## 3. Data Flow (Notification Loop)

1.  **Detection**: `SchedulerService`가 시세 변동을 감지하고 `AlertService`에 알림 요청.
2.  **Queueing**: `AlertService`는 메시지를 즉시 보내지 않고 **Queue**에 적재.
3.  **Polling**: 외부 에이전트(OpenClaw Cron) 또는 클라이언트가 `GET /alerts/pending` 호출.
4.  **Delivery**: 수신된 메시지를 실제 Slack 채널로 전송.

## 4. Key Features
- **Webull-Style Pre-market Logic**: 프리마켓 등락률 계산 시 `(현재가 - 정규장 종가) / 정규장 종가` 공식을 사용하여 앱과 동일한 경험 제공.
- **Hybrid Data Source**: 미국 주식은 `yfinance`, 한국 주식은 `FinanceDataReader` 및 `Naver Finance` 크롤링 혼용.
