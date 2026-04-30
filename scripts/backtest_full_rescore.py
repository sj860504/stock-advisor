"""
Full re-score backtest with NEW unified percentage-based logic.

NEW components (all linear/proportional from baselines):
  DCF_deviation        ±25  (1% per pt)
  RSI_deviation        ±15  (1 RSI unit per pt)
  EMA200_deviation     ±15  (1% per pt)
  change_deviation     ±15  (1% per 3 pts, capped)
  VIX_deviation        ±10  (1 unit per pt, baseline 20)
  FNG_deviation        ±10  (5 units per pt, baseline 50)
  Regime_deviation     ±10  (3 units per pt, baseline 50)

Approach:
  1. Parse OLD reason strings → extract RSI%, DCF%, EMA200, change_rate
  2. Lookup VIX/F&G/regime from market_regime_history by date
  3. Compute NEW score
  4. Filter buys whose new score > threshold
  5. Re-aggregate P&L for thresholds [30, 35, 40, 45, 50]
"""
import re
import sqlite3
from collections import defaultdict
from statistics import mean

DB = "data/stock_advisor.db"
SINCE = "2026-03-01"


def parse_reason(reason: str) -> dict:
    """Extract raw values from reason string (RSI, DCF undervalue%, EMA200 from target, change_rate)."""
    p = {}
    if not reason:
        return p
    # RSI value
    m = re.search(r"RSI_(?:extreme_)?(?:oversold|overbought)\(([-\d.]+),", reason)
    if m:
        p["rsi"] = float(m.group(1))
    # DCF undervalue %
    if "DCF_high_undervalue" in reason:
        m = re.search(r"DCF_high_undervalue\(([-\d.]+)%\)", reason)
        p["dcf_pct"] = float(m.group(1)) if m else 25.0
    elif "DCF_mid_undervalue" in reason:
        m = re.search(r"DCF_mid_undervalue\(([-\d.]+)%\)", reason)
        p["dcf_pct"] = float(m.group(1)) if m else 12.0
    elif "DCF_undervalue" in reason:
        m = re.search(r"DCF_undervalue\(([-\d.]+)%\)", reason)
        p["dcf_pct"] = float(m.group(1)) if m else 7.0
    elif "DCF_fair_value" in reason:
        p["dcf_pct"] = 0.0
    elif "DCF_high_overvalue" in reason:
        m = re.search(r"DCF_high_overvalue\(([-\d.]+)%\)", reason)
        p["dcf_pct"] = -float(m.group(1)) if m else -25.0
    elif "DCF_overvalue" in reason:
        m = re.search(r"DCF_overvalue\(([-\d.]+)%\)", reason)
        p["dcf_pct"] = -float(m.group(1)) if m else -10.0
    elif "no_dcf_data" in reason:
        p["no_dcf"] = True
    # EMA200 from target_entry / target_sell
    m = re.search(r"target_entry_price_hit\(\$([\d.]+)\)", reason)
    if m:
        p["ema200"] = float(m.group(1)) / 1.01
    else:
        m = re.search(r"target_sell_price_hit\(\$([\d.]+)\)", reason)
        if m:
            p["ema200"] = float(m.group(1)) / 1.15
    # change_rate
    m = re.search(r"sharp_drop\(([-\d.]+)%\)", reason)
    if m:
        p["change"] = float(m.group(1))
    else:
        m = re.search(r"sharp_surge\(([-\d.]+)%\)", reason)
        if m:
            p["change"] = float(m.group(1))
    # other markers
    p["panic"] = "extreme_fear_buy_opportunity" in reason
    p["overheated"] = "market_overheated_partial_profit" in reason
    p["bull"] = "bull_market_advantage" in reason
    p["bear"] = "bear_market_hold" in reason
    p["top10"] = "top10_market_cap" in reason
    p["ema_support"] = "EMA200_support" in reason
    return p


def fetch_regime_map():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("SELECT date, status, regime_score, vix, fear_greed FROM market_regime_history")
    m = {row[0]: {"status": row[1], "score": row[2], "vix": row[3], "fng": row[4]} for row in cur.fetchall()}
    conn.close()
    return m


def fetch_trades():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("""
        SELECT id, ticker, order_type, quantity, price, result_msg, timestamp
        FROM trade_history
        WHERE status='filled' AND timestamp >= ?
        ORDER BY timestamp ASC
    """, (SINCE,))
    rows = [r for r in cur.fetchall() if r[1].isdigit() and len(r[1]) == 6]
    conn.close()
    return rows


def compute_new_score(parsed: dict, curr_price: float, regime_info: dict) -> tuple:
    """Compute new unified score from parsed components + regime data."""
    score = 50
    components = {}

    # DCF
    if "dcf_pct" in parsed:
        d = max(-25, min(25, int(-parsed["dcf_pct"])))
        if d != 0:
            score += d; components[f"DCF({parsed['dcf_pct']:+.1f}%)"] = d
    elif parsed.get("no_dcf"):
        score += 10; components["no_dcf"] = 10

    # RSI
    if "rsi" in parsed:
        d = max(-15, min(15, int(parsed["rsi"] - 50)))
        if d != 0:
            score += d; components[f"RSI({parsed['rsi']:.1f})"] = d

    # EMA200
    if "ema200" in parsed and parsed["ema200"] > 0:
        ema_pct = (curr_price - parsed["ema200"]) / parsed["ema200"] * 100
        d = max(-15, min(15, int(ema_pct)))
        if d != 0:
            score += d; components[f"EMA200({ema_pct:+.1f}%)"] = d

    # change_rate
    if "change" in parsed:
        d = max(-15, min(15, int(parsed["change"] * 3)))
        if d != 0:
            score += d; components[f"change({parsed['change']:+.1f}%)"] = d

    # VIX
    if regime_info and regime_info.get("vix") is not None:
        d = max(-10, min(10, int(-(regime_info["vix"] - 20))))
        if d != 0:
            score += d; components[f"VIX({regime_info['vix']:.1f})"] = d

    # F&G
    if regime_info and regime_info.get("fng") is not None:
        d = max(-10, min(10, int((regime_info["fng"] - 50) / 5)))
        if d != 0:
            score += d; components[f"FNG({regime_info['fng']})"] = d

    # Regime score
    if regime_info and regime_info.get("score") is not None:
        d = max(-10, min(10, int(-(regime_info["score"] - 50) / 3)))
        if d != 0:
            score += d; components[f"Regime({regime_info['score']})"] = d

    score = max(1, min(100, score))
    return score, components


def pair_trades(rows, blocked_ids: set):
    open_q = defaultdict(list)
    pairs = []
    for tid, ticker, side, qty, price, reason, ts in rows:
        if side == "buy":
            if tid in blocked_ids:
                continue
            open_q[ticker].append([price, qty, reason or "", ts, tid])
        else:
            remaining = qty
            while remaining > 0 and open_q[ticker]:
                bp, bq, br, bt, bid = open_q[ticker][0]
                used = min(remaining, bq)
                pairs.append({
                    "ticker": ticker, "buy_price": bp, "sell_price": price,
                    "qty": used, "pnl_krw": (price - bp) * used,
                    "buy_reason": br, "sell_reason": reason or "",
                    "buy_time": bt, "sell_time": ts,
                })
                bq -= used; remaining -= used
                if bq == 0:
                    open_q[ticker].pop(0)
                else:
                    open_q[ticker][0][1] = bq
    return pairs


def stats(pairs):
    if not pairs:
        return None
    total = sum(p["pnl_krw"] for p in pairs)
    wins = sum(1 for p in pairs if p["pnl_krw"] > 0)
    return {"n": len(pairs), "total": total, "wins": wins, "win_rate": wins / len(pairs) * 100}


def main():
    rows = fetch_trades()
    regime_map = fetch_regime_map()
    print(f"Total trades (60d KR): {len(rows)}")

    # Compute new score for each buy
    rescored = {}  # tid -> (new_score, components)
    parseable = 0
    for tid, ticker, side, qty, price, reason, ts in rows:
        if side != "buy":
            continue
        parsed = parse_reason(reason)
        if not parsed:
            continue
        date = ts[:10]
        # Find latest regime info on or before this date
        regime_info = regime_map.get(date)
        if not regime_info:
            for d in sorted(regime_map.keys(), reverse=True):
                if d <= date:
                    regime_info = regime_map[d]
                    break
        score, comps = compute_new_score(parsed, price, regime_info)
        rescored[tid] = (score, comps)
        parseable += 1

    print(f"Re-scoreable buys: {parseable}")

    # Score distribution
    score_dist = defaultdict(int)
    for s, _ in rescored.values():
        bucket = (s // 10) * 10
        score_dist[bucket] += 1
    print("\n=== 새 점수 분포 ===")
    for b in sorted(score_dist.keys()):
        print(f"  [{b:>3}~{b+9:>3}]: {score_dist[b]:>4}건  {'#' * (score_dist[b] // 5)}")

    # Original P&L
    pairs_orig = pair_trades(rows, blocked_ids=set())
    so = stats(pairs_orig)
    print(f"\n=== ORIGINAL ===")
    print(f"  pairs: {so['n']}, win: {so['win_rate']:.1f}%, total: {so['total']:+,.0f}원")

    # Try multiple thresholds
    print(f"\n=== THRESHOLD 별 새 로직 결과 ===")
    print(f"{'threshold':>10} {'pairs':>6} {'blocked':>8} {'win%':>6} {'total':>14} {'Δ vs orig':>12}")
    for thr in [25, 30, 35, 40, 45, 50]:
        blocked = {tid for tid, (s, _) in rescored.items() if s > thr}
        pairs_new = pair_trades(rows, blocked_ids=blocked)
        s_new = stats(pairs_new)
        if s_new:
            delta = s_new['total'] - so['total']
            print(f"{thr:>10} {s_new['n']:>6} {len(blocked):>8} {s_new['win_rate']:>5.1f}% {s_new['total']:>+14,.0f} {delta:>+12,.0f}")

    # Daily breakdown for best threshold
    print(f"\n=== 일별 손익 비교 (THRESHOLD 35) ===")
    best_thr = 35
    blocked = {tid for tid, (s, _) in rescored.items() if s > best_thr}
    pairs_new = pair_trades(rows, blocked_ids=blocked)
    daily_orig = defaultdict(float)
    daily_new = defaultdict(float)
    for p in pairs_orig:
        daily_orig[p["sell_time"][:10]] += p["pnl_krw"]
    for p in pairs_new:
        daily_new[p["sell_time"][:10]] += p["pnl_krw"]
    cum_o = cum_n = 0.0
    print(f"{'date':<12} {'orig':>11} {'new':>11} {'Δ':>10}    {'cum_orig':>12} {'cum_new':>12}")
    for d in sorted(set(list(daily_orig.keys()) + list(daily_new.keys()))):
        o = daily_orig.get(d, 0); n = daily_new.get(d, 0)
        cum_o += o; cum_n += n
        print(f"{d:<12} {o:>+11,.0f} {n:>+11,.0f} {n-o:>+10,.0f}    {cum_o:>+12,.0f} {cum_n:>+12,.0f}")


if __name__ == "__main__":
    main()
