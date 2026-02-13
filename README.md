# Stock Advisor API

한국투자증권(KIS) API 및 WebSocket 기반 주식 분석 및 알림 API (FastAPI + FinanceDataReader + KIS)

## 주요 기능

### 📡 실시간 모니터링
- KIS WebSocket(kis_ws)을 통한 실시간 시세 수신
- KIS API를 통한 해외/국내 상위 종목 자동 수집
- 기술적 지표 계산: RSI, EMA(5, 10, 20, 60, 100, 200)
- 주요 지수(S&P 500, KOSPI 등) 실시간 현황 조회

### 📊 가치평가 및 분석
- **DCF (현금흐름할인법)**: KIS API 재무 데이터를 기반으로 적정주가 산출
- **기술적 분석**: RSI 및 이평선 기반 매매 신호 포착
- **수익률 분석**: YTD 수익률 및 MDD(최대 낙폭) 계산
- **재무지표**: PER, PBR, ROE 등 주요 지표 제공

### 💼 포트폴리오 관리
- **엑셀 업로드**: 보유 종목 일괄 등록 (티커, 수량, 매수가)
- **수익률 추적**: 포트폴리오 전체 및 종목별 수익률 현황
- **종목 관리**: 개별 종목 추가/삭제

### 🚨 매매 신호 및 알림
- 과매도 알림 (RSI < 30)
- 과매수 알림 (RSI > 70)
- DCF 저평가 알림 (현재가 < DCF * 0.8)
- EMA200 지지선 터치 알림

## 실행 방법

```bash
# 의존성 설치
pip install -r requirements.txt

# 서버 실행
python main.py

# 또는 uvicorn 직접 실행
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

API 문서: http://localhost:8000/docs

## 기술 스택

- **Back-end**: FastAPI
- **Data**: KIS API (REST/WebSocket)
- **Real-time**: KIS WebSocket
- **Scheduling**: APScheduler
- **Analysis**: Pandas, NumPy
- **Utils**: OpenPyxl (Excel 처리)

## 2026-02-13 작업 내역

### 전략/매매 로직
- 매매 전략 서비스 점검 및 개선(점수 계산, 실행 안정성, 로그/리포트 흐름 정비)
- 시가총액 상위 10개 종목 기본 가중치(+10점) 적용 및 사용자 커스텀 가중치 오버라이드 기능 추가
- 보유 종목 반복 매수 방지 로직 추가(추가매수 조건 충족 시에만 허용)
- 매수/추가매수 모두 시장/섹터 비중 제한 검증 후 집행되도록 강화
- 포트폴리오 목표 비중(국내 30%, 미국 40%, 현금 30%) 기반 매수 제한 반영
- 폭락장 조건에서만 현금 버퍼를 추가로 사용하도록 제어

### 틱매매 기능(1종목)
- 1시간 최저가 기반 진입, +1% 익절, -5% 손절, -3% 추가매수, 장마감 전 현금화 로직 구현
- 직전 매도 체결가 대비 -1% 재진입 로직 추가
- 틱매매 설정 API 추가
  - `GET /api/trading/tick-settings`
  - `PUT /api/trading/tick-settings`
- 삼성전자(`005930`) 기준 틱매매 활성화/검증
- 틱매매 10분 주기 수익 리포트(수익률/수익금) 스케줄러 작업 추가

### 시장시간/실행 윈도우
- 전략 실행기를 장중 윈도우에서만 동작하도록 제한(불필요 실행 방지)
- 미국장은 프리/애프터 포함 실행 가능하도록 확장
- 한국장 시간외 처리 추가 후, 모의투자(VTS)에서는 시간외 주문 불가 제약을 반영해 정규장만 허용
- `dictionary changed size during iteration` 예외 방지를 위해 실시간 상태 순회 시 스냅샷 순회로 수정

### KIS 연동 안정화
- KIS 잔고 조회 500 에러 대응: 재시도(백오프) + 마지막 정상 응답 폴백
- KIS 시세 조회 TPS 제한 대응: 전역 쓰로틀(0.55초) + 자동 재시도
- SPX 조회 404 이슈 수정(해외 시세 조회 경로 통합 + FDR 폴백)

### 리포트/알림
- 앱 기동 직후 KIS 동기화 후 포트폴리오 슬랙 리포트 전송
- 매수/매도 슬랙 메시지를 한 줄 요약 포맷으로 단순화
- 포트폴리오 리포트 항목 확장(총평가금액, 가용현금, 평가손익, 종목별 상세)
- 무체결 시 1시간 주기 리포트, 체결 시 즉시 리포트 분리 운영
- 수익/손실 색상 이모지 표시(수익: red, 손실: blue)

### 분석/데이터/API
- DCF 사용자 오버라이드 저장 기능 추가(FCF, Beta, Growth Rate) + API 노출
  - `PUT /analysis/dcf-override`
- 전략 가중치 오버라이드 API 추가
  - `PUT /analysis/strategy/weights`
- 지표 서비스 EMA 반환 포맷 하위 호환 보완(`ema` dict + flatten key 동시 제공)

### 사후장(실전 전용) 준비 메서드 추가
- 실전투자에서만 사용할 사후장 주문 메서드 추가(기본 비활성)
  - `KisService.send_after_hours_order(...)`
  - `KisService.send_after_hours_buy(...)`
  - `KisService.send_after_hours_sell(...)`
- 환경변수 플래그 추가
  - `KIS_ENABLE_AFTER_HOURS_ORDER=false` (기본)
  - `KIS_AFTER_HOURS_ORD_DVSN=81` (기본)
- 동작 조건
  - `KIS_IS_VTS=true`이면 사후장 주문 차단
  - `KIS_ENABLE_AFTER_HOURS_ORDER=true` + 실전 계좌에서만 허용
