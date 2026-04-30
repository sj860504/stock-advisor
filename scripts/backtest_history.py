"""
Backtest analysis from trade_history.

1. FIFO-pairs buys with sells per ticker → realized P&L per round-trip.
2. Aggregates by exit reason and entry reason features.
3. Prints diagnostic tables to identify profit-killing patterns.
"""
import sqlite3
import re
from collections import defaultdict, Counter
from datetime import datetime
from statistics import mean

DB = "data/stock_advisor.db"


SINCE = "2026-04-16"  # 최근 2주 (2026-04-30 기준)


def fetch_trades(kr_only: bool = True):
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("""
        SELECT id, ticker, order_type, quantity, price, result_msg, timestamp
        FROM trade_history
        WHERE status='filled' AND timestamp >= ?
        ORDER BY timestamp ASC
    """, (SINCE,))
    rows = cur.fetchall()
    conn.close()
    if kr_only:
        rows = [r for r in rows if r[1].isdigit() and len(r[1]) == 6]
    return rows


def pair_trades(rows):
    """FIFO match per ticker: returns list of round-trips."""
    open_q = defaultdict(list)  # ticker -> [(buy_price, qty, buy_reason, buy_time), ...]
    pairs = []
    for tid, ticker, side, qty, price, reason, ts in rows:
        if side == "buy":
            open_q[ticker].append([price, qty, reason or "", ts])
        else:  # sell
            remaining = qty
            while remaining > 0 and open_q[ticker]:
                bp, bq, breason, btime = open_q[ticker][0]
                used = min(remaining, bq)
                pnl_krw = (price - bp) * used
                pnl_pct = (price - bp) / bp * 100 if bp else 0
                hold_sec = (datetime.fromisoformat(ts) - datetime.fromisoformat(btime)).total_seconds()
                pairs.append({
                    "ticker": ticker,
                    "buy_price": bp, "sell_price": price, "qty": used,
                    "pnl_krw": pnl_krw, "pnl_pct": pnl_pct,
                    "hold_sec": hold_sec,
                    "buy_reason": breason, "sell_reason": reason or "",
                    "buy_time": btime, "sell_time": ts,
                })
                bq -= used
                remaining -= used
                if bq == 0:
                    open_q[ticker].pop(0)
                else:
                    open_q[ticker][0][1] = bq
    return pairs, open_q


def classify_exit(reason: str) -> str:
    r = reason.lower()
    if "stop_loss" in r:
        return "stop_loss"
    if "trailing_stop" in r:
        return "trailing_stop"
    if "take_profit" in r or "익절" in r or "분할 매도" in r:
        return "take_profit"
    if "asset_management" in r:
        return "asset_management"
    if r.startswith("score") or "score " in r:
        return "score_sell"
    return "other"


def has_feature(reason: str, feature: str) -> bool:
    return feature in reason


def stats(pairs):
    if not pairs:
        return None
    pnl_pcts = [p["pnl_pct"] for p in pairs]
    pnl_krw = [p["pnl_krw"] for p in pairs]
    wins = [p for p in pairs if p["pnl_krw"] > 0]
    return {
        "n": len(pairs),
        "win_rate": len(wins) / len(pairs) * 100,
        "total_krw": sum(pnl_krw),
        "avg_pct": mean(pnl_pcts),
        "median_pct": sorted(pnl_pcts)[len(pnl_pcts) // 2],
        "avg_hold_min": mean(p["hold_sec"] for p in pairs) / 60,
    }


def print_table(title, rows):
    print(f"\n=== {title} ===")
    if not rows:
        print("(empty)")
        return
    cols = list(rows[0].keys())
    widths = {c: max(len(c), max(len(str(r[c])) for r in rows)) for c in cols}
    print(" | ".join(c.ljust(widths[c]) for c in cols))
    print("-+-".join("-" * widths[c] for c in cols))
    for r in rows:
        print(" | ".join(str(r[c]).ljust(widths[c]) for c in cols))


def fmt_stats(label, s):
    if s is None:
        return {"label": label, "n": 0, "win%": "-", "total₩": "-", "avg%": "-", "med%": "-", "hold(min)": "-"}
    return {
        "label": label, "n": s["n"],
        "win%": f"{s['win_rate']:.1f}",
        "total₩": f"{s['total_krw']:>+,.0f}",
        "avg%": f"{s['avg_pct']:+.2f}",
        "med%": f"{s['median_pct']:+.2f}",
        "hold(min)": f"{s['avg_hold_min']:.1f}",
    }


def main():
    rows = fetch_trades()
    print(f"Total trades: {len(rows)}")
    pairs, open_q = pair_trades(rows)
    print(f"Round-trip pairs: {len(pairs)}")
    open_count = sum(len(v) for v in open_q.values())
    print(f"Unmatched open positions: {open_count}")

    overall = stats(pairs)
    print(f"\nOverall realized P&L: ₩{overall['total_krw']:+,.0f} | win rate {overall['win_rate']:.1f}% | avg {overall['avg_pct']:+.2f}% | hold avg {overall['avg_hold_min']:.1f}min")

    # 일별 실현손익 추이 (sell_time 기준)
    daily = defaultdict(lambda: {"pnl": 0.0, "n": 0, "wins": 0})
    for p in pairs:
        d = p["sell_time"][:10]
        daily[d]["pnl"] += p["pnl_krw"]
        daily[d]["n"] += 1
        if p["pnl_krw"] > 0:
            daily[d]["wins"] += 1
    print("\n=== 일별 실현손익 추이 ===")
    print(f"{'date':<12} {'n':>4} {'win':>4} {'win%':>5} {'day_pnl':>14} {'cum_pnl':>14}  bar")
    cum = 0.0
    for d in sorted(daily.keys()):
        info = daily[d]
        cum += info["pnl"]
        win_rate = info["wins"] / info["n"] * 100 if info["n"] else 0
        bar_units = int(abs(info["pnl"]) / 5000)
        bar = ("+" if info["pnl"] >= 0 else "-") * min(bar_units, 40)
        print(f"{d:<12} {info['n']:>4} {info['wins']:>4} {win_rate:>4.0f}% {info['pnl']:>+14,.0f} {cum:>+14,.0f}  {bar}")

    # By exit reason class
    by_exit = defaultdict(list)
    for p in pairs:
        by_exit[classify_exit(p["sell_reason"])].append(p)
    rows_by_exit = [fmt_stats(k, stats(v)) for k, v in sorted(by_exit.items(), key=lambda x: -len(x[1]))]
    print_table("EXIT 사유별 (sell side)", rows_by_exit)

    # By entry feature
    features = [
        "extreme_fear_buy_opportunity",
        "DCF_high_undervalue",
        "DCF_mid_undervalue",
        "DCF_undervalue",
        "DCF_fair_value",
        "DCF_overvalue",
        "no_dcf_data",
        "RSI_extreme_oversold",
        "RSI_oversold",
        "EMA200_support",
        "target_entry_price_hit",
        "target_sell_price_hit",
        "sharp_drop",
        "sharp_surge",
        "bull_market_advantage",
        "bear_market_hold",
        "top10_market_cap",
    ]
    rows_by_feat = []
    for f in features:
        sub = [p for p in pairs if has_feature(p["buy_reason"], f)]
        rows_by_feat.append(fmt_stats(f, stats(sub)))
    rows_by_feat.sort(key=lambda x: -x["n"] if isinstance(x["n"], int) else 0)
    print_table("ENTRY 시그널 feature별", rows_by_feat)

    # Hold-time buckets
    buckets = [(0, 5), (5, 30), (30, 120), (120, 360), (360, 1440), (1440, 99999)]
    rows_buckets = []
    for lo, hi in buckets:
        sub = [p for p in pairs if lo <= p["hold_sec"] / 60 < hi]
        rows_buckets.append(fmt_stats(f"{lo}-{hi}min", stats(sub)))
    print_table("보유시간 버킷별", rows_buckets)

    # Top losers/winners
    print("\n=== TOP 10 LOSER PAIRS ===")
    losers = sorted(pairs, key=lambda p: p["pnl_krw"])[:10]
    for p in losers:
        print(f"  {p['ticker']:<8} {p['pnl_krw']:>+10,.0f}₩ ({p['pnl_pct']:+.2f}%) hold={p['hold_sec']/60:.1f}min  buy:{p['buy_reason'][:60]}  exit:{p['sell_reason'][:40]}")
    print("\n=== TOP 10 WINNER PAIRS ===")
    winners = sorted(pairs, key=lambda p: -p["pnl_krw"])[:10]
    for p in winners:
        print(f"  {p['ticker']:<8} {p['pnl_krw']:>+10,.0f}₩ ({p['pnl_pct']:+.2f}%) hold={p['hold_sec']/60:.1f}min  buy:{p['buy_reason'][:60]}  exit:{p['sell_reason'][:40]}")

    # Loss-cycle detection: same-day buy → sell → buy → sell pattern
    print("\n=== 매수→손절→재매수→손절 사이클 (당일 동일 종목 ≥3회) ===")
    daily_count = defaultdict(int)
    for p in pairs:
        d = p["buy_time"][:10]
        daily_count[(d, p["ticker"])] += 1
    cycles = sorted([(k, v) for k, v in daily_count.items() if v >= 3], key=lambda x: -x[1])[:15]
    for (d, t), c in cycles:
        # Sum of pnl for that day/ticker
        day_pairs = [p for p in pairs if p["buy_time"][:10] == d and p["ticker"] == t]
        net = sum(p["pnl_krw"] for p in day_pairs)
        print(f"  {d} {t}: {c}회 round-trip, net P&L: {net:+,.0f}원")


if __name__ == "__main__":
    main()
