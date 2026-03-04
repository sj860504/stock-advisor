# UI 개선 요구사항 명세서 (For AI / Frontend Developer)

현재 구축되어 있는 Sean's Stock Advisor의 FastAPI 엔드포인트 및 기존 UI를 기반으로, **Google AI Studio 등 외부 환경에서 새로운 프론트엔드 코드(React, Vue 등)를 즉시 생성하고 연동할 수 있도록 필요한 모든 API 명세와 기술적 제약 사항**을 정리한 문서입니다.

---

## 1. 기존 UI 한계점 및 핵심 프론트엔드 아키텍처 개선 방향
1. **코드 모듈화 부재**: 기존 `index.html` 기반 단일 파일 구조를 폐기하고, **React/Vue 계열의 모던 컴포넌트 구조**로 재작성할 것을 권장합니다.
2. **트레이딩 액션 파편화 해결**: 종목 리스트(포트폴리오, 관심종목, 시그널)의 각 **행(Row) 우측에 [매수], [매도], [DCF 시뮬레이터] 버튼**을 인라인으로 배치하고 열리는 모달(Modal)/서랍장(Drawer) 형식을 채택해야 합니다.
3. **통합 대상**: 이 UI는 백엔드 포트폴리오(KIS 증권사 연동), 매크로 지수 수집기(FRED), 가격 시그널(Market Data), 가치평가 시스템(DCF) 모두를 다루는 **종합 대시보드(Trading Admin Panel)** 형태여야 합니다.

---

## 2. 화면별 필수 기능 (UI Components Requirement)

### 2.1 메인 대시보드 (Dashboard)
* **통합 자산 현황 & 섹터 미니카드**: 전체 자산 요약, 실시간 총 평가 손익, 국내/해외 자산 비율 도넛 차트
* **매크로 지표 스냅샷**: VIX, US 10년물 금리, 시장 Regime(Bull/Bear 점수 0~100 게이지)
* **일일 마켓 시황**: `GET /api/summary`를 활용한 해당일 Top 100 종목 시황 요약 (과매수/과매도)

### 2.2 공통 리스트 (Portfolio & Market Monitoring)
* **글로벌 필터**: 테이블 상단에 `[전체] [🇺🇸 미국] [🇰🇷 한국]` 토글 필수 구현.
* **표준화된 Row 액션 (Inline Actions)**: 
  * `[매수(BUY)]` 버튼: 종목명과 현재가(또는 시장가 옵션)가 채워진 주문 모달 오픈.
  * `[매도(SELL)]` 버튼: 포트폴리오 보유 수량을 기본값으로 한 매도 모달 오픈.
  * `[DCF 분석(Simulate)]` 버튼: 성장률, 할인율 슬라이더를 포함한 DCF 시뮬레이터 팝업 열기.

### 2.3 트레이딩 및 전략 제어 패널 (Trading Settings)
* **Bot 마스터 스위치**: 메인 자동 매매 전략 플래그 ON/OFF 토글.
* **비상 탈출 버튼 (Panic Trigger)**: 시장 대공황 대비용 마스터 버튼 (전량 매도 후 관망 지시).
* **가격 알림(Alerts)**: 목표 주가 상하향 돌파 사용자 알림 관리.

---

## 3. 백엔드 API 연동 명세 (For AI Integration)

다음 정보는 개발 AI(Google AI Studio 등)가 API 호출 코드를 작성할 때 사용해야 할 가이드라인입니다.

### 3.1 🔐 인증 및 보안 (Authentication)
모든 주요 API 호출은 헤더에 `Authorization: Bearer <token>`을 포함해야 합니다.
* **로그인 (Authentication) 엔드포인트**
  * **URL**: `POST /api/auth/login`
  * **Content-Type**: `application/x-www-form-urlencoded`
  * **Payload**: `username` (사용자 ID, 기본 테스트 계정: `sean`), `password` (비밀번호)
  * **Response**: `{"access_token": "...", "token_type": "bearer"}`
* **클라이언트 식별자**: 일부 API는 URL 변수에 `user_id`를 사용합니다. 기본값으로 `"sean"` 문자열을 파라미터로 넘겨 구성합니다. (예: `GET /api/portfolio/sean/full-report`)

### 3.2 🛠 리전 구분(KR vs US)을 위한 프론트엔드 유틸리티 
FastAPI 백엔드로 내려오는 데이터 중 티커(Ticker) 형식을 직접 파싱하여 환율/통화($ vs ₩) 및 시장(Market) 필터링에 사용해야 합니다. 
* **판별 로직 규칙**: 티커(`ticker`)가 6자리 완전한 숫자(`regex: /^\d{6}$/`)이면 한국 주식(KR₩), 그 외(예: AAPL, TSLA) 영문이 포함된 티커는 전량 미국 주식(US$)으로 간주합니다.
```javascript
// AI Studio용 Reference Utility Code
const isKrMarket = (d) => (d.market === 'kr') || (!d.market && /^\d{6}$/.test(String(d.ticker || '')));
const getCurrencySymbol = (d) => isKrMarket(d) ? '₩' : '$';
```

### 3.3 📡 핵심 API Endpoint 목록 및 Payload

#### [1] 트레이딩 (주문/매도) - **중요: '리전 마켓' 추가 요구됨**
* **직접 주문 제출**
  * **URL**: `POST /api/trading/order`
  * **Payload Schema (JSON)**:
    ```json
    {
       "ticker": "AAPL",
       "action": "buy",   // "buy" | "sell"
       "quantity": 10,    // 숫자
       "price": 0,        // 지정가, 0 입력 시 시장가 주문
       "market": "us"     // ["추가필요"] 프론트에서 "kr" 또는 "us" 명시 (백엔드 개편 예정)
    }
    ```
* **수동 매도 (전략 우회)**
  * **URL**: `POST /api/trading/sell` (Payload 동일)
* **전량 매도(비상 탈출)**
  * **URL**: `POST /api/trading/sell-all-and-rebuy`

#### [2] 포트폴리오 조회 및 동기화
* **포트폴리오 전체 조회 (보유 종목 테이블용)**
  * **URL**: `GET /api/portfolio/{user_id}/full-report` (예: `sean`)
  * **Response Schema (List)**:
    ```json
    [{
      "ticker": "005930",
      "name": "삼성전자",
      "buy_price": 70000,
      "current_price": 73000,
      "quantity": 100,
      "profit_pct": 4.2,      // 수익률
      "dcf_fair": 90000,      // 시스템 계산 DCF 적정가
      "rsi": 45.2,
      "sector": "tech"
    }]
    ```

#### [3] DCF (단일 종목 모달 시뮬레이터 용도)
* **Custom 파라미터로 실시간 DCF 즉시 계산 시뮬레이션**
  * **URL**: `GET /api/analysis/dcf-custom`
  * **Query Parameters**: `?ticker=AAPL&growth_rate=0.10&discount_rate=0.09&terminal_growth=0.03`
  * **Response**: 시뮬레이션 결과 `fair_value` 및 계산된 현금 흐름 반환. 슬라이더(Input Range) 변경 시 디바운스(Debounce) 형태로 이 API를 다시 호출하여 UI 즉시 업데이트 되도록 제작할 것.
* **시뮬레이션 값을 실제 데이터 베이스 목표가로 Override 저장**
  * **URL**: `PUT /api/analysis/dcf-override`
  * **Payload Schema**:
    ```json
    {
      "ticker": "AAPL",
      "fair_value": 185.50,         // 계산된 최종 목표가 직접 주입
      "growth_rate": 0.12           // 사용자가 드래그한 성장 파라미터
    }
    ```

#### [4] 시장 현황 모니터링 (실시간 신호)
* **Market Signals (과매수/과매도, 지지선 돌파 목록)**
  * **URL**: `GET /api/market/signals`
  * **Response Schema**:
    ```json
    {
      "oversold": [{"ticker": "TSLA", "price": 180, "rsi": 25.1}],
      "overbought": [{"ticker": "NVDA", "price": 850, "rsi": 82.5}],
      "undervalued": [{"ticker": "AAPL", "price": 170, "upside_pct": 15.2}],
      "ema200_support": [{"ticker": "MSFT", "price": 405, "ema200": 403}]
    }
    ```

#### [5] 통합 자산 및 잔고 현황 (Dashboard 용도)
* **총 자산 평가액 및 섹터/종목별 현황 조회**
  * **URL**: `GET /api/trading/balance`
  * **Response Schema (JSON)**:
    ```json
    {
      "total_eval": 15000000,     // 총 평가금액 (KRW 환산 등)
      "cash_kr": 2500000,         // 원화 예수금
      "cash_us": 1500.50,         // 달러 예수금
      "profit_loss": 500000,      // 총 평가 손익
      "holdings": [               // 증권사(KIS) Raw 단위 보유 내역 배열
        {                         // (또는 "output1" 배열로 내려올 수 있음)
          "pdno": "005930",            // 종목코드(티커)
          "prdt_name": "삼성전자",     // 종목명
          "hldg_qty": "100",           // 수량 (문자열일 수 있으므로 파싱 주의)
          "pchs_avg_pric": "70000",    // 평균매수가
          "prpr": "73000",             // 현재가
          "evlu_amt": "7300000",       // 평가금액
          "evlu_pfls_amt": "300000",   // 평가손익금
          "evlu_pfls_rt": "4.28"       // 수익률(%)
        }
      ]
    }
    ```

#### [6] 가치평가(DCF) 랭킹 보드 (스크리닝 용도)
* **전 종목 DCF 적정가 및 괴리율(Upside) 목록**
  * **URL**: `GET /api/analysis/dcf?market_type=kr` (전체 조회 시 파라미터 생략 가능)
  * **Response Schema (JSON)**:
    ```json
    {
      "count": 50,
      "items": [
        {
          "ticker": "AAPL",
          "name": "Apple Inc.",
          "market_type": "US",
          "current_price": 170.50,
          "dcf_value": 195.00,
          "upside_pct": 14.3,          // 저평가 괴리율 (%)
          "is_override": false,        // 수동 오버라이드 여부
          "base_date": "2024-03-01"
        }
      ]
    }
    ```

#### [7] 매크로 경제 지표 및 시장 국면 (Dashboard 상단)
* **주요 거시경제(VIX, 금리, 환율) 및 시장 국면(Regime) 점수**
  * **URL**: `GET /api/market/macro`
  * **Response Schema (JSON)**:
    ```json
    {
      "us_10y_yield": 4.15,
      "vix": 14.2,
      "fear_greed": 75,
      "market_regime": {
        "status": "Bull",             // "Bull", "Bear", "Neutral"
        "regime_score": 85,           // 0 ~ 100 점
        "bear_threshold": 40,
        "components": {
           "technical": 18, "vix": 16, "fear_greed": 15, "economic": 18, "other": 18
        }
      },
      "indices": {
        "S&P500": {"price": 5050.2, "change": 0.5},
        "KOSPI": {"price": 2650.1, "change": -0.2}
      },
      "economic_indicators": {
        "CPI_YOY": {"name": "소비자물가지수(YoY)", "latest": 3.1, "previous": 3.4, "status": "positive"}
      }
    }
    ```

#### [8] 실시간 탑 종목 모니터링 (Market Watch 용도)
* **현재 모니터링 중인 상위 종목 시세 및 RSI 상태**
  * **URL**: `GET /api/market/monitored`
  * **Response Schema (JSON Map Type)**: 티커(Ticker)를 Key 값으로 하는 Map 객체 형태로 반환됩니다. (키에 섞여있는 `"message"` 속성 예외 처리 주의)
    ```json
    {
      "AAPL": {
        "name": "Apple",
        "price": 170.50,
        "change_pct": 1.25,
        "volume": 52000000,
        "rsi": 55.4,
        "ma20": 168.0,
        "last_updated": "2024-03-04T10:30:00"
      },
      "005930": {
        "prdt_name": "삼성전자",
        "price": 73000,
        "change_pct": -0.5,
        "rsi": 42.1
      },
      "message": "데이터 수집 시작..." // 없을 수도 있음. Key가 "message"인지 필터링 필수
    }
    ```
