# KIS API 패턴 및 주의사항

## TR ID 규칙
KIS는 VTS/실전 TR ID가 다름. 반드시 get_api_info() 사용.

```python
tr_id, api_path = StockMetaService.get_api_info("api_name")
```

주요 API 이름 (DB api_tr_meta):

| api_name | VTS TR ID | Real TR ID |
|----------|-----------|------------|
| 주식현재가_시세 | FHKST01010100 | FHKST01010100 |
| 주식잔고조회 | VTTC8434R | TTTC8434R |
| 주식주문_매수 | VTTC0802U | TTTC0802U |
| 주식주문_매도 | VTTC0801U | TTTC0801U |
| 해외주식_상세시세 | HHDFS00000300 | HHDFS70200200 |
| 해외주식_기간별시세 | HHDFS76240000 | HHDFS76240000 |

## TPS 제한
- 최소 요청 간격: 0.55초 (_throttle_request())
- Rate limit 감지: HTTP 429 또는 HTTP 500 + EGW00201
- 재시도: 최대 3회, 1.2배 백오프
- 루프 내 반복 호출 금지 → 배치 처리

## 토큰 발급 순서
1. 메모리 (_access_token + _token_expiry)
2. 파일 (data/kis_token.json, 2시간 유효)
3. KIS OAuth2 신규 발급
수동: python scripts/issue_token.py

## WebSocket
- 국내: H0STCNT0 (HIGH tier)
- 해외: HDFSUSP0 (HIGH tier)
- LOW tier: REST 5분 폴링 (KisFetcher)
- 자동 재연결: 지수백오프 5~60초

## 사후장 주문
- 실전 전용 (VTS 미지원)
- KIS_ENABLE_AFTER_HOURS_ORDER=true 설정 필요

Last Updated: 2026-03-11
