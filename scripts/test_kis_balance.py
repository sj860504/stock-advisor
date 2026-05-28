"""KIS 토큰 + 잔고 조회 독립 테스트 (SQLAlchemy 미사용 — 로컬 검증용)

사용법:
  KIS_APP_KEY=... KIS_APP_SECRET=... KIS_ACCOUNT_NO=... \
  KIS_BASE_URL=https://openapivts.koreainvestment.com:29443 \
  KIS_IS_VTS=true \
  python scripts/test_kis_balance.py

기본은 .env 자동 로드. 환경변수가 있으면 그걸 우선.
"""
import os
import sys
import json
import requests
from pathlib import Path


def load_env():
    """Lazy .env 파서 (python-dotenv 없이)."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        # 환경변수가 비어있을 때만 .env 값 사용
        if not os.environ.get(k):
            os.environ[k] = v


TOKEN_CACHE_PATH = Path(__file__).resolve().parent.parent / "data" / ".kis_token_cache.json"


def _load_cached_token(app_key):
    """파일 캐시에서 토큰 로드. app_key별 분리, 만료 미경과만 반환."""
    env_tok = os.environ.get("KIS_ACCESS_TOKEN")
    if env_tok:
        return env_tok, "env"
    if not TOKEN_CACHE_PATH.exists():
        return None, None
    try:
        from datetime import datetime as _dt
        cache = json.loads(TOKEN_CACHE_PATH.read_text())
        entry = cache.get(app_key) or {}
        tok = entry.get("token")
        exp = entry.get("expires")  # ISO format
        if tok and exp:
            try:
                exp_dt = _dt.fromisoformat(exp)
                if exp_dt > _dt.now():
                    return tok, f"file (만료 {exp})"
            except Exception:
                pass
    except Exception:
        pass
    return None, None


def _save_cached_token(app_key, token, expires):
    """app_key별로 토큰 저장 (data/.kis_token_cache.json)."""
    try:
        TOKEN_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        cache = {}
        if TOKEN_CACHE_PATH.exists():
            try:
                cache = json.loads(TOKEN_CACHE_PATH.read_text())
            except Exception:
                cache = {}
        cache[app_key] = {"token": token, "expires": expires}
        TOKEN_CACHE_PATH.write_text(json.dumps(cache, indent=2))
        # gitignore 안전망: data/는 이미 gitignored 가정
    except Exception as e:
        print(f"  ⚠ 토큰 캐시 저장 실패: {e}")


def get_token(base_url, app_key, app_secret):
    """OAuth2 토큰. 파일 캐시 만료 전이면 재사용 (1분 쿨다운 회피)."""
    cached, src = _load_cached_token(app_key)
    if cached:
        print(f"\n[1] 캐시된 토큰 재사용 ({src})")
        return cached
    url = f"{base_url}/oauth2/tokenP"
    body = {"grant_type": "client_credentials", "appkey": app_key, "appsecret": app_secret}
    print(f"\n[1] 토큰 발급 → {url}")
    r = requests.post(url, json=body, timeout=10)
    if r.status_code != 200:
        print(f"  ✗ HTTP {r.status_code}: {r.text}")
        return None
    data = r.json()
    if "access_token" not in data:
        print(f"  ✗ 응답: {json.dumps(data, ensure_ascii=False, indent=2)}")
        return None
    tok = data["access_token"]
    # KIS expires 포맷: "YYYY-MM-DD HH:MM:SS" → ISO 변환
    exp_raw = (data.get("access_token_token_expired") or "").replace(" ", "T")
    _save_cached_token(app_key, tok, exp_raw)
    print(f"  ✓ token={tok[:30]}... (expires={exp_raw}) — 캐시 저장")
    return tok


def fetch_kr_balance(base_url, app_key, app_secret, token, account_no, tr_id):
    """국내잔고 조회."""
    # KIS 계좌번호 분리: 8자리(CANO) + 2자리(PRDT). "-" 있으면 그것 우선.
    if "-" in account_no:
        cano, prdt = (account_no.split("-") + ["01"])[:2]
    elif len(account_no) >= 10:
        cano, prdt = account_no[:8], account_no[8:10]
    else:
        cano, prdt = account_no, "01"
    url = f"{base_url}/uapi/domestic-stock/v1/trading/inquire-balance"
    headers = {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": app_key, "appsecret": app_secret,
        "tr_id": tr_id, "custtype": "P",
    }
    params = {
        "CANO": cano, "ACNT_PRDT_CD": prdt,
        "AFHR_FLPR_YN": "N", "OFL_YN": "", "INQR_DVSN": "02",
        "UNPR_DVSN": "01", "FUND_STTL_ICLD_YN": "N",
        "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "01",
        "CTX_AREA_FK100": "", "CTX_AREA_NK100": "",
    }
    print(f"\n[2] 국내잔고 (tr_id={tr_id}) → CANO={cano} PRDT={prdt}")
    r = requests.get(url, headers=headers, params=params, timeout=10)
    if r.status_code != 200:
        print(f"  ✗ HTTP {r.status_code}: {r.text[:500]}")
        return None
    data = r.json()
    rt_cd = data.get("rt_cd")
    msg = data.get("msg1", "")
    print(f"  rt_cd={rt_cd}  msg={msg}")
    if rt_cd != "0":
        print(f"  ⚠ 비정상 응답: {json.dumps(data, ensure_ascii=False)[:500]}")
        return None

    output1 = data.get("output1", []) or []
    output2 = data.get("output2", []) or []
    print(f"  ✓ output1(보유): {len(output1)}건")
    for h in output1[:5]:
        print(f"     - {h.get('pdno')} {h.get('prdt_name')} "
              f"qty={h.get('hldg_qty')} avg={h.get('pchs_avg_pric')} cur={h.get('prpr')}")
    if len(output1) > 5:
        print(f"     ... +{len(output1)-5} more")
    if output2:
        s = output2[0]
        print(f"  ✓ output2(요약):")
        print(f"     예수금총금액     dnca_tot_amt        = {s.get('dnca_tot_amt')}")
        print(f"     D+2 예수금       prvs_rcdl_excc_amt  = {s.get('prvs_rcdl_excc_amt')}")
        print(f"     유가평가         scts_evlu_amt       = {s.get('scts_evlu_amt')}")
        print(f"     총평가           tot_evlu_amt        = {s.get('tot_evlu_amt')}")
        print(f"     순자산           nass_amt            = {s.get('nass_amt')}")
        print(f"     평가손익         evlu_pfls_smtl_amt  = {s.get('evlu_pfls_smtl_amt')}")
    return data


def fetch_us_balance(base_url, app_key, app_secret, token, account_no, tr_id, label="해외잔고"):
    """해외잔고 조회."""
    # KIS 계좌번호 분리: 8자리(CANO) + 2자리(PRDT). "-" 있으면 그것 우선.
    if "-" in account_no:
        cano, prdt = (account_no.split("-") + ["01"])[:2]
    elif len(account_no) >= 10:
        cano, prdt = account_no[:8], account_no[8:10]
    else:
        cano, prdt = account_no, "01"
    url = f"{base_url}/uapi/overseas-stock/v1/trading/inquire-balance"
    headers = {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": app_key, "appsecret": app_secret,
        "tr_id": tr_id, "custtype": "P",
    }
    params = {
        "CANO": cano, "ACNT_PRDT_CD": prdt,
        "OVRS_EXCG_CD": "", "TR_CRCY_CD": "USD",
        "CTX_AREA_FK200": "", "CTX_AREA_NK200": "",
    }
    print(f"\n[3] {label} (tr_id={tr_id})")
    try:
        r = requests.get(url, headers=headers, params=params, timeout=10)
    except Exception as e:
        print(f"  ✗ 요청 실패: {e}")
        return None
    if r.status_code != 200:
        print(f"  ✗ HTTP {r.status_code}: {r.text[:500]}")
        return None
    data = r.json()
    rt_cd = data.get("rt_cd")
    msg = data.get("msg1", "")
    print(f"  rt_cd={rt_cd}  msg={msg}")
    if rt_cd != "0":
        print(f"  ⚠ 비정상 응답: {json.dumps(data, ensure_ascii=False)[:500]}")
        return None
    output1 = data.get("output1", []) or []
    output2 = data.get("output2", []) or []
    print(f"  ✓ output1(보유): {len(output1)}건")
    for h in output1[:5]:
        print(f"     - {h.get('ovrs_pdno') or h.get('pdno')} {h.get('ovrs_item_name') or h.get('prdt_name')} "
              f"qty={h.get('ovrs_cblc_qty')} avg={h.get('pchs_avg_pric')} cur={h.get('now_pric2') or h.get('ovrs_now_pric')}")
    if output2:
        s = output2 if isinstance(output2, dict) else output2[0]
        print(f"  ✓ output2(요약):")
        for k in ("tot_aset_amt", "tot_evlu_pfls_amt", "ovrs_rlzt_pfls_amt", "frcr_evlu_amt2", "evlu_amt_smtl_amt", "frcr_pchs_amt1"):
            if s.get(k):
                print(f"     {k} = {s.get(k)}")
    return data


def main():
    load_env()
    base = os.environ.get("KIS_BASE_URL", "")
    is_vts = os.environ.get("KIS_IS_VTS", "false").lower() == "true"
    key = os.environ.get("KIS_APP_KEY", "")
    secret = os.environ.get("KIS_APP_SECRET", "")
    account = os.environ.get("KIS_ACCOUNT_NO", "")

    if not (base and key and secret and account):
        print("✗ 필수 환경변수 누락: KIS_BASE_URL / KIS_APP_KEY / KIS_APP_SECRET / KIS_ACCOUNT_NO")
        sys.exit(1)

    print("=" * 70)
    print("  KIS 잔고 조회 테스트")
    print("=" * 70)
    print(f"  BASE_URL  = {base}")
    print(f"  IS_VTS    = {is_vts}")
    print(f"  ACCOUNT   = {account}")
    print(f"  APP_KEY   = {key[:10]}...{key[-4:]}")

    token = get_token(base, key, secret)
    if not token:
        print("\n✗ 토큰 발급 실패 — 키/시크릿/URL 확인")
        sys.exit(1)

    # TR ID: VTS=모의투자, 실서버는 TTC/TTS
    tr_kr  = "VTTC8434R" if is_vts else "TTTC8434R"
    tr_us1 = "VTTS3012R" if is_vts else "TTTS3012R"
    tr_us2 = "VTTT3012R" if is_vts else "TTTT3012R"

    fetch_kr_balance(base, key, secret, token, account, tr_kr)
    fetch_us_balance(base, key, secret, token, account, tr_us1, label="해외잔고 (잔고조회)")
    fetch_us_balance(base, key, secret, token, account, tr_us2, label="해외잔고 (잔고종합)")

    print("\n" + "=" * 70)
    print("  완료")
    print("=" * 70)


if __name__ == "__main__":
    main()
