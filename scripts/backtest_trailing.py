"""
Trailing-stop threshold simulation.

For each round-trip exited via trailing_stop, parse the actual drawdown%
from the reason string and compute hypothetical P&L if threshold were
relaxed (e.g., -5% → -7%).

Trades that would NOT have triggered under new threshold: assume
held until next exit signal — for simulation simplicity, we mark them
as "held longer" with unrealized P&L using the buy-time forward
trajectory unknown — instead we conservatively assume the trade did
NOT close at trailing_stop, so we **cancel that exit** and keep the
position open. Subsequent fills that match it provide an alternate exit.

Result interpretation:
  - "saved_pnl": pnl avoided by not stopping out
  - "lost_pnl": potential further losses if held (best-effort estimate)
"""
import re
import sqlite3
from collections import defaultdict

DB = "data/stock_advisor.db"
SINCE = "2026-03-01"


def parse_trailing_dd(reason: str):
    """trailing_stop(-5.2% from high 1,387,000) → -5.2"""
    m = re.search(r"trailing_stop\(([-\d.]+)%", reason)
    return float(m.group(1)) if m else None


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


def pair_with_trailing_relaxed(rows, new_threshold_pct: float):
    """FIFO match; if exit is trailing_stop with abs(drawdown) < new_threshold, cancel it (no exit).
    Cancelled trades stay open — re-paired with the next sell of same ticker (different reason)."""
    open_q = defaultdict(list)
    pairs = []
    cancelled = 0
    for tid, ticker, side, qty, price, reason, ts in rows:
        if side == "buy":
            open_q[ticker].append([price, qty, reason or "", ts, tid])
        else:
            r = reason or ""
            dd = parse_trailing_dd(r) if "trailing_stop" in r else None
            if dd is not None and abs(dd) < new_threshold_pct:
                cancelled += 1
                continue
            remaining = qty
            while remaining > 0 and open_q[ticker]:
                bp, bq, br, bt, bid = open_q[ticker][0]
                used = min(remaining, bq)
                pairs.append({
                    "ticker": ticker, "buy_price": bp, "sell_price": price,
                    "qty": used, "pnl_krw": (price - bp) * used,
                    "buy_reason": br, "sell_reason": r,
                    "buy_time": bt, "sell_time": ts,
                })
                bq -= used; remaining -= used
                if bq == 0:
                    open_q[ticker].pop(0)
                else:
                    open_q[ticker][0][1] = bq
    return pairs, cancelled


def stats(pairs):
    if not pairs:
        return {"n": 0, "total": 0, "wins": 0, "win_rate": 0}
    total = sum(p["pnl_krw"] for p in pairs)
    wins = sum(1 for p in pairs if p["pnl_krw"] > 0)
    return {"n": len(pairs), "total": total, "wins": wins, "win_rate": wins / len(pairs) * 100}


def main():
    rows = fetch_trades()
    print(f"Total trades (60d KR): {len(rows)}")

    # Original
    pairs_orig, _ = pair_with_trailing_relaxed(rows, new_threshold_pct=0.0)
    so = stats(pairs_orig)
    print(f"\nORIGINAL: pairs {so['n']}, win {so['win_rate']:.1f}%, total {so['total']:+,.0f}원")

    # Threshold sweep
    print(f"\n=== trailing_stop 임계값 완화 시뮬 ===")
    print(f"{'new_thr':>8} {'cancelled':>10} {'pairs':>6} {'win%':>5} {'total':>14} {'Δ vs orig':>12}")
    for new_thr in [5.0, 6.0, 7.0, 8.0, 10.0]:
        pairs, cancelled = pair_with_trailing_relaxed(rows, new_threshold_pct=new_thr)
        s = stats(pairs)
        delta = s["total"] - so["total"]
        print(f"{new_thr:>7.1f}% {cancelled:>10} {s['n']:>6} {s['win_rate']:>4.1f}% {s['total']:>+14,.0f} {delta:>+12,.0f}")

    # Detail at threshold 7.0
    print(f"\n=== 일별 비교 (trailing -7%) ===")
    pairs_new, cancelled = pair_with_trailing_relaxed(rows, new_threshold_pct=7.0)
    daily_orig = defaultdict(float)
    daily_new = defaultdict(float)
    for p in pairs_orig:
        daily_orig[p["sell_time"][:10]] += p["pnl_krw"]
    for p in pairs_new:
        daily_new[p["sell_time"][:10]] += p["pnl_krw"]
    cum_o = cum_n = 0
    print(f"{'date':<12} {'orig':>11} {'new':>11} {'Δ':>10}    {'cum_orig':>12} {'cum_new':>12}")
    for d in sorted(set(list(daily_orig.keys()) + list(daily_new.keys()))):
        o = daily_orig.get(d, 0); n = daily_new.get(d, 0)
        cum_o += o; cum_n += n
        print(f"{d:<12} {o:>+11,.0f} {n:>+11,.0f} {n-o:>+10,.0f}    {cum_o:>+12,.0f} {cum_n:>+12,.0f}")


if __name__ == "__main__":
    main()
