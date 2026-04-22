"""
KIS WebSocket 40종목 한계 검증 + 로테이션 타이밍 측정

목적:
1. KR 개장 시 KIS WebSocket이 20종목 초과(21~40종목) 구독 가능한지 확인
2. 100종목을 3그룹으로 나눠 로테이션 시 전체 1사이클 소요 시간 측정

실행 방법:
    source /root/stock-advisor/venv/bin/activate
    python scripts/test_ws_40_limit.py

결과 해석:
    - "✅ 40종목 모두 수신" → 시장당 40개 가능, 로테이션 방식 불필요
    - "⚠️ XX종목만 수신 (한계 초과)" → 20개 한계 확정, 로테이션 방식 검토
"""

import asyncio
import json
import os
import sys
import time
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config import Config
from repositories.settings_repo import SettingsRepo
from services.kis.kis_service import KisService
from services.market.data_service import DataService

# ─── 설정 ────────────────────────────────────────────────────────────────────
WAIT_SEC_PER_GROUP  = 20   # 그룹당 데이터 수신 대기 시간 (초)
TARGET_COUNT        = 40   # 한계 검증용 구독 수
ROTATION_GROUPS     = 3    # 로테이션 그룹 수
WS_APPROVAL_KEY_TTL = 23   # approval key 유효시간 (시간) — KIS 24h, 여유 1h


# ─── Approval Key (DB 캐시) ───────────────────────────────────────────────────
def get_approval_key(use_real: bool = False) -> str | None:
    """
    WebSocket approval key를 DB 캐시에서 꺼냄.
    만료 또는 없으면 신규 발급 후 DB에 저장.
    """
    key_field    = "KIS_WS_APPROVAL_KEY_REAL" if use_real else "KIS_WS_APPROVAL_KEY"
    expiry_field = "KIS_WS_APPROVAL_KEY_REAL_EXPIRY" if use_real else "KIS_WS_APPROVAL_KEY_EXPIRY"

    # 1. DB 캐시 확인
    cached_key    = SettingsRepo.get(key_field)
    cached_expiry = SettingsRepo.get(expiry_field)
    if cached_key and cached_expiry:
        try:
            expiry_dt = datetime.fromisoformat(cached_expiry)
            if datetime.now() < expiry_dt:
                print(f"  🔑 approval_key DB 캐시 사용 (만료: {expiry_dt.strftime('%H:%M')})")
                return cached_key
        except Exception:
            pass

    # 2. 신규 발급
    import requests
    if use_real and Config.has_real_credentials():
        url    = f"{Config.KIS_REAL_BASE_URL}/oauth2/Approval"
        body   = {"grant_type": "client_credentials",
                  "appkey": Config.KIS_REAL_APP_KEY,
                  "secretkey": Config.KIS_REAL_APP_SECRET}
        ws_url = Config.KIS_REAL_WS_URL
    else:
        url    = f"{Config.KIS_BASE_URL}/oauth2/Approval"
        body   = {"grant_type": "client_credentials",
                  "appkey": Config.KIS_APP_KEY,
                  "secretkey": Config.KIS_APP_SECRET}
        ws_url = Config.KIS_WS_URL

    try:
        resp = requests.post(url, headers={"content-type": "application/json; charset=utf-8"},
                             json=body, timeout=5)
        if resp.status_code != 200:
            print(f"  ❌ approval_key 발급 실패: {resp.text}")
            return None
        key = resp.json().get("approval_key")
        if not key:
            print("  ❌ approval_key 응답 없음")
            return None

        # DB 저장
        expiry = (datetime.now() + timedelta(hours=WS_APPROVAL_KEY_TTL)).isoformat()
        SettingsRepo.set(key_field, key)
        SettingsRepo.set(expiry_field, expiry)
        print(f"  🔑 approval_key 신규 발급 → DB 저장 (만료: {expiry[:16]})")
        return key
    except Exception as e:
        print(f"  ❌ approval_key 발급 오류: {e}")
        return None


def get_ws_url() -> str:
    ws_url = Config.KIS_REAL_WS_URL if Config.has_real_credentials() else Config.KIS_WS_URL
    if not Config.has_real_credentials() and "vts" in Config.KIS_BASE_URL.lower() and ":21000" in ws_url:
        ws_url = ws_url.replace(":21000", ":31000")
    return ws_url


# ─── 40종목 한계 검증 ─────────────────────────────────────────────────────────
async def test_40_limit(kr_tickers: list[str], approval_key: str, ws_url: str) -> dict:
    """
    KR 종목 40개를 단일 세션에 구독하고, 실제로 데이터가 수신되는 종목 수 측정.
    20종목 초과 시 KIS가 응답을 주지 않으면 received < sent 로 확인.
    """
    tickers = [t for t in kr_tickers if len(t) == 6][:TARGET_COUNT]
    print(f"\n{'='*60}")
    print(f"[테스트 1] {len(tickers)}종목 단일 세션 구독 한계 검증")
    print(f"{'='*60}")

    received: dict[str, float] = {}
    t0 = time.time()

    async with __import__("websockets").connect(
        ws_url, ping_interval=30, ping_timeout=20, close_timeout=20, open_timeout=60,
    ) as ws:
        print(f"  연결 완료: {time.time()-t0:.2f}초")

        # 구독 전송
        t_sub = time.time()
        for i, ticker in enumerate(tickers):
            body = {"header": {"approval_key": approval_key, "custtype": "P",
                               "tr_type": "1", "content-type": "utf-8"},
                    "body": {"input": {"tr_id": "H0STCNT0", "tr_key": ticker}}}
            await ws.send(json.dumps(body))
            await asyncio.sleep(0.05)
            if (i + 1) % 10 == 0:
                print(f"  구독 전송: {i+1}/{len(tickers)}")
        print(f"  구독 전송 완료: {time.time()-t_sub:.2f}초")

        # 수신 대기
        print(f"  데이터 수신 대기 ({WAIT_SEC_PER_GROUP}초)...")
        deadline = time.time() + WAIT_SEC_PER_GROUP
        while time.time() < deadline:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=min(deadline - time.time(), 2.0))
            except asyncio.TimeoutError:
                continue
            if not msg or msg[0] not in ('0', '1'):
                continue
            parts = msg.split('|')
            if len(parts) < 4 or parts[1] != "H0STCNT0":
                continue
            ticker_recv = parts[2]
            if len(ticker_recv) == 6 and ticker_recv.isdigit() and ticker_recv not in received:
                vals = parts[3].split('^')
                if len(vals) >= 3:
                    received[ticker_recv] = float(vals[2])
                    if len(received) % 5 == 0:
                        print(f"    수신: {len(received)}/{len(tickers)}종목")

    total_sec = time.time() - t0
    recv_n, sent_n = len(received), len(tickers)
    print(f"\n  ─ 결과 ─")
    print(f"  구독: {sent_n}종목 / 수신: {recv_n}종목 / 수신률: {recv_n/sent_n*100:.0f}%")
    print(f"  총 소요: {total_sec:.1f}초")

    if recv_n == sent_n:
        print(f"  ✅ {sent_n}종목 전부 수신 → KIS 세션 총 한계 ≥ {sent_n} (시장별 20 제한 없음)")
    elif recv_n > 20:
        print(f"  ⚠️  20종목 초과 수신({recv_n}) → 부분 가능, 정확한 한계 추가 테스트 필요")
    else:
        print(f"  ❌ {recv_n}종목만 수신 → KIS 한계 = 시장별 20종목 확정")

    missed = [t for t in tickers if t not in received]
    if missed:
        print(f"  미수신 ({len(missed)}개): {missed[:10]}{'...' if len(missed) > 10 else ''}")

    return received


# ─── 로테이션 타이밍 측정 ─────────────────────────────────────────────────────
async def test_rotation_timing(kr_tickers: list[str], approval_key: str, ws_url: str) -> None:
    """100종목을 N그룹으로 나눠 WebSocket 로테이션 시 전체 사이클 소요 시간 측정."""
    tickers   = [t for t in kr_tickers if len(t) == 6][:100]
    group_size = (len(tickers) + ROTATION_GROUPS - 1) // ROTATION_GROUPS
    groups    = [tickers[i:i+group_size] for i in range(0, len(tickers), group_size)]

    print(f"\n{'='*60}")
    print(f"[테스트 2] 로테이션 타이밍 ({len(groups)}그룹 × ~{group_size}종목, 대기 {WAIT_SEC_PER_GROUP}초/그룹)")
    print(f"{'='*60}")

    cycle_start    = time.time()
    group_timings  = []

    for gi, group in enumerate(groups):
        print(f"\n  [그룹 {gi+1}/{len(groups)}] {len(group)}종목")
        t0       = time.time()
        received = {}

        try:
            t_conn = time.time()
            async with __import__("websockets").connect(
                ws_url, ping_interval=30, ping_timeout=20, close_timeout=20, open_timeout=60,
            ) as ws:
                conn_sec = time.time() - t_conn

                t_sub = time.time()
                for ticker in group:
                    body = {"header": {"approval_key": approval_key, "custtype": "P",
                                       "tr_type": "1", "content-type": "utf-8"},
                            "body": {"input": {"tr_id": "H0STCNT0", "tr_key": ticker}}}
                    await ws.send(json.dumps(body))
                    await asyncio.sleep(0.05)
                sub_sec = time.time() - t_sub

                t_recv = time.time()
                deadline = time.time() + WAIT_SEC_PER_GROUP
                while time.time() < deadline and len(received) < len(group):
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=min(deadline-time.time(), 1.0))
                    except asyncio.TimeoutError:
                        continue
                    if not msg or msg[0] not in ('0','1'):
                        continue
                    parts = msg.split('|')
                    if len(parts) < 4 or parts[1] != "H0STCNT0":
                        continue
                    tr = parts[2]
                    if len(tr) == 6 and tr not in received:
                        vals = parts[3].split('^')
                        if len(vals) >= 3:
                            received[tr] = float(vals[2])
                recv_sec = time.time() - t_recv

            disc_sec = time.time() - (t_recv + recv_sec)
        except Exception as e:
            print(f"    오류: {e}")
            continue

        group_total = time.time() - t0
        group_timings.append(group_total)
        print(f"    연결: {conn_sec:.2f}s | 구독: {sub_sec:.2f}s | 수신대기: {recv_sec:.1f}s | 해제: {disc_sec:.2f}s")
        print(f"    수신: {len(received)}/{len(group)}종목 | 그룹 합계: {group_total:.1f}초")

    cycle_total = time.time() - cycle_start
    print(f"\n  📊 로테이션 결과 요약:")
    for i, t in enumerate(group_timings):
        print(f"    그룹 {i+1}: {t:.1f}초")
    if group_timings:
        avg = sum(group_timings) / len(group_timings)
        print(f"    평균 그룹 소요: {avg:.1f}초")
    print(f"    전체 사이클: {cycle_total:.1f}초 ({cycle_total/60:.1f}분)")
    print(f"    → 100종목 전체 업데이트 주기: 약 {cycle_total:.0f}초마다 1회")
    print(f"\n  ⚠️  참고: 그룹 2 수신 중 그룹 1·3은 가격 블라인드 (로테이션 방식의 근본 한계)")


# ─── 메인 ─────────────────────────────────────────────────────────────────────
async def main():
    print("KIS WebSocket 한계 검증 + 로테이션 타이밍 테스트")
    print(f"환경: {'실서버' if Config.has_real_credentials() else 'VTS(모의)'}")
    print(f"시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # approval_key — DB 캐시 우선
    use_real     = Config.has_real_credentials()
    approval_key = get_approval_key(use_real=use_real)
    if not approval_key:
        print("❌ approval_key 취득 실패. 종료.")
        return
    ws_url = get_ws_url()

    # KR 상위 100종목 — KisService.get_access_token() 은 내부에서 DB 캐시 사용
    print("\nKR Top100 종목 조회 중...")
    try:
        kr_tickers = DataService.get_top_krx_tickers(limit=100)
        print(f"  조회 완료: {len(kr_tickers)}종목")
    except Exception as e:
        print(f"  조회 실패: {e} → 하드코딩 fallback 사용")
        kr_tickers = [
            "005930","000660","035420","005380","051910","006400","003550","035720","068270","055550",
            "105560","028260","066570","012330","207940","034730","086790","015760","018260","011170",
            "003670","032830","000270","011200","009540","010130","096770","030200","316140","033780",
            "017670","251270","036570","000810","002790","088350","010950","009150","005490","042660",
        ]

    # 테스트 1: 40종목 단일 세션 한계 검증
    await test_40_limit(kr_tickers, approval_key, ws_url)

    # 테스트 2: 로테이션 타이밍
    await test_rotation_timing(kr_tickers, approval_key, ws_url)


if __name__ == "__main__":
    asyncio.run(main())
