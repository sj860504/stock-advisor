"""
RSI hard-gate threshold simulation.

Filter buys whose entry RSI is >= new_rsi_block. Compare P&L by threshold.
Only `score N [...]` and `budget_buy [...]` reasons (others lack RSI value).
"""
import re
import sqlite3
from collections import defaultdict

DB = "data/stock_advisor.db"
SINCE = "2026-03-01"


def parse_rsi(reason: str):
    if not reason:
        return None
    m = re.search(r"RSI_(?:extreme_)?(?:oversold|overbought)\(([-\d.]+),", reason)
    if m:
        return float(m.group(1))
    m = re.search(r"RSI_deviation\(([-\d.]+),", reason)
    if m:
        return float(m.group(1))
    return None


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


def pair_with_rsi_gate(rows, rsi_block: float):
    open_q = defaultdict(list)
    pairs = []
    blocked = 0
    for tid, ticker, side, qty, price, reason, ts in rows:
        if side == "buy":
            rsi = parse_rsi(reason)
            if rsi is not None and rsi >= rsi_block:
                blocked += 1
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
    return pairs, blocked


def stats(pairs):
    if not pairs:
        return {"n": 0, "total": 0, "wins": 0, "win_rate": 0}
    return {
        "n": len(pairs),
        "total": sum(p["pnl_krw"] for p in pairs),
        "wins": sum(1 for p in pairs if p["pnl_krw"] > 0),
        "win_rate": sum(1 for p in pairs if p["pnl_krw"] > 0) / len(pairs) * 100,
    }


def main():
    rows = fetch_trades()
    print(f"Total trades (60d KR): {len(rows)}")
    pairs_orig, _ = pair_with_rsi_gate(rows, rsi_block=999)
    so = stats(pairs_orig)
    print(f"\nORIGINAL: pairs {so['n']}, win {so['win_rate']:.1f}%, total {so['total']:+,.0f}원")

    print(f"\n=== RSI hard-gate 강화 시뮬 ===")
    print(f"{'rsi_block':>10} {'blocked':>8} {'pairs':>6} {'win%':>5} {'total':>14} {'Δ vs orig':>12}")
    for thr in [50, 55, 60, 65, 70, 75]:
        pairs, blocked = pair_with_rsi_gate(rows, rsi_block=thr)
        s = stats(pairs)
        delta = s["total"] - so["total"]
        print(f"{thr:>10} {blocked:>8} {s['n']:>6} {s['win_rate']:>4.1f}% {s['total']:>+14,.0f} {delta:>+12,.0f}")


if __name__ == "__main__":
    main()
