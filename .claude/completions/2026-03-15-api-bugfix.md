# 2026-03-15 API 버그 수정

## 수정된 오류

### 1. RegimeComponents.ema_map Pydantic 타입 오류
- **파일**: `models/schemas.py`
- **원인**: `ema_map: Dict[str, Any]`로 선언되어 있으나 실제 값은 `{5: ..., 20: ..., 60: ..., 120: ..., 200: ...}` 정수 키
- **수정**: `Dict[str, Any]` → `Dict[int, Any]`

### 2. MacroDataSnapshot.exchange_rate 누락
- **파일**: `models/schemas.py`, `services/market/macro_service.py`
- **원인**: `scheduler_service.get_all_cached_prices()`에서 `macro_data.exchange_rate` 참조하는데 `MacroDataSnapshot` 모델에 해당 필드 없음
- **수정**:
  - `MacroDataSnapshot`에 `exchange_rate: float = 1350.0` 필드 추가
  - `MacroService.get_macro_data_snapshot()`에서 `cls.get_exchange_rate()` 호출 후 주입

### 3. /api/summary 500 — 문자열 반환
- **파일**: `routers/reports.py`
- **원인**: `AlertService.generate_daily_summary()` 반환값이 `str`인데 라우터 `response_model=Dict[str, Any]`로 선언
- **수정**: `generate_daily_summary()` 호출 제거 → `get_all_cached_prices()` dict 직접 반환

### 4. /api/market/calendar/weekly 500 — CalendarEvent 객체 반환
- **파일**: `routers/market.py`
- **원인**: `EconomicCalendarService.get_weekly_calendar()`가 `list[CalendarEvent]` 반환 (Pydantic 객체)
- **수정**: `[e.model_dump(mode="json") for e in ...]` 변환 후 반환

## 확인된 정상 엔드포인트
- `GET /api/summary` → 200, ticker별 점수/가격/RSI/EMA dict
- `GET /api/market/calendar/weekly` → 200, 경제지표 발표 일정 list
- `GET /api/market/macro` → 200, 거시경제 지표
