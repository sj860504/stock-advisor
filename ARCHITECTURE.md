# Stock Advisor Architecture

## 1. Overview
**Stock Advisor**는 FastAPI 기반의 실시간 주식 분석 및 포트폴리오 관리 시스템입니다.
미국/국내 주식의 실시간 시세 모니터링, DCF(현금흐름 할인) 기반 가치평가,
포트폴리오 수익 분석을 수행하고 Slack으로 알림을 전송합니다.

## 2. Service Layer Structure
프로젝트는 `services/` 디렉터리 아래의 기능별 서비스로 구성됩니다.

```mermaid
graph TD
    Main[main.py (FastAPI)] --> Scheduler[SchedulerService]
    Main --> Portfolio[PortfolioService]
    Main --> Alert[AlertService]

    Scheduler -- 1. 주기 실행 --> Data[DataService]
    Scheduler -- 2. 가치 평가 --> Financial[FinancialService]
    Scheduler -- 3. 알림 체크 --> Alert
    Scheduler -- 4. 자산 분석 --> Portfolio

    Portfolio --> Data[DataService]
    Portfolio --> Ticker[TickerService]

    Financial --> Data
```

### Core Services

#### 1. `SchedulerService` (Heartbeat)
백그라운드에서 주기적으로 작업을 수행합니다.
- **`start()`**: 스케줄러 초기화 및 작업 등록
- **`update_prices()`** (1분 간격): Top 20 종목의 실시간 시세, RSI, EMA 업데이트
- **`check_portfolio_hourly()`** (1시간 간격): 포트폴리오 점검 및 리포트 생성
- **`update_dcf_valuations()`** (30분 간격): DCF 적정 가치 갱신

#### 2. `PortfolioService` (Asset Management)
사용자 자산 정보를 관리합니다.
- **`parse_excel()`**: 엑셀 파일로부터 보유 종목(Ticker, 수량, 매수가) 로드
- **`load_portfolio()` / `save_portfolio()`**: JSON 파일 기반 자산 정보 저장/로드
- **`analyze_portfolio()`**: 현재가 기반 수익률 및 섹터 비중 분석

#### 3. `AlertService` (Notification)
Slack 알림을 관리하며 큐 기반으로 동작합니다.
- **`check_and_alert()`**: RSI/DCF/EMA 조건을 체크하여 알림 생성
- **`send_slack_alert()`**: 알림 메시지를 큐에 적재
- **`get_pending_alerts()`**: 대기 중 알림 조회 (API polling)

#### 4. `FinancialService` (Valuation)
기업 가치 평가 관련 데이터를 수집/계산합니다.
- **`get_dcf_data()`**: FCF, Beta, 성장률 데이터 구성
- **`validate_dcf()`**: DCF 적정 가치 대비 저/고평가 구간 판단

#### 5. `DataService` & `TickerService` (Infra)
- **`DataService`**: OHLCV 시세 및 지표 계산
- **`TickerService`**: 종목명/티커 변환 및 보정

## 3. Data Flow (Notification Loop)
1. **Detection**: `SchedulerService`가 시세 변화를 감지
2. **Queueing**: `AlertService`가 알림 메시지를 큐에 적재
3. **Polling**: 클라이언트가 `GET /alerts/pending` 호출
4. **Delivery**: Slack 채널로 알림 전송

## 4. Key Features
- **Pre-market Logic**: 프리마켓 변동을 반영한 가격 산정
- **Hybrid Data Source**: KIS + 보조 소스(FinanceDataReader 등) 혼합 사용
