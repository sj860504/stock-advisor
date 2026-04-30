"""
Re-score historical buys using NEW scoring logic and re-run backtest.

NEW logic changes:
  - REMOVED: EMA200_support (-10)
  - REPLACED: target_entry_price_hit (-15) / target_sell_price_hit (+30)
              → EMA200 deviation score: clamp(int((curr - ema200)/ema200 * 100), -15, +15)

Approach:
  1. Parse `score N [reasons...]` buys → reconstruct components individually.
  2. Remove old components, add new EMA200_deviation component.
     - EMA200 reverse-engineered from target_entry_price_hit($X) → X / 1.01
     - or target_sell_price_hit($X)              → X / 1.15
     - curr_price = buy fill price
  3. Drop trades where new_score > BUY_THRESHOLD(30).
  4. Re-aggregate P&L.
"""
import re
import sqlite3
from collections import defaultdict
from datetime import datetime
from statistics import mean

DB = "data/stock_advisor.db"
SINCE = "2026-03-01"  # 60일치
BUY_THRESHOLD = 30

# --- Static component weights (mirrors signal_service.py / execution_service_v2.py) ---
WEIGHTS = {
    'DIP_BUY_5PCT': -15, 'SURGE_SELL_5PCT': +15,
    'SUPPORT_EMA': -10,
    'ADD_POSITION_LOSS': -10,
    'PANIC_MARKET_BUY': -30, 'PROFIT_TAKE_TARGET': +30,
    'BULL_MARKET_SECTOR': -15,
    'DCF_UNDERVALUE_HIGH': -25, 'DCF_UNDERVALUE_MID': -15,
    'DCF_UNDERVALUE_LOW': -10, 'DCF_FAIR_VALUE': -5,
    'DCF_OVERVALUE_LOW': +10, 'DCF_OVERVALUE_HIGH': +20,
    'DCF_UNAVAILABLE': +10,
}


def parse_score_reasons(reason: str) -> dict:
    """Extract individual component contributions from a `score N [...]` reason string."""
    components = {}
    # RSI: explicit signed delta in form ",-6" or ",+8"
    m = re.search(r"RSI_extreme_oversold\(([-\d.]+),(-?\d+)\)", reason)
    if m:
        components["rsi"] = int(m.group(2))
    else:
        m = re.search(r"RSI_oversold\(([-\d.]+),(-?\d+)\)", reason)
        if m:
            components["rsi"] = int(m.group(2))
        else:
            m = re.search(r"RSI_overbought\(([-\d.]+),\+?(-?\d+)\)", reason)
            if m:
                components["rsi"] = int(m.group(2))
            m2 = re.search(r"RSI_extreme_overbought\(([-\d.]+),\+?(-?\d+)\)", reason)
            if m2:
                components["rsi"] = int(m2.group(2))
    if "sharp_drop" in reason:
        components["dip"] = WEIGHTS['DIP_BUY_5PCT']
    if "sharp_surge" in reason:
        components["surge"] = WEIGHTS['SURGE_SELL_5PCT']
    if "DCF_high_undervalue" in reason:
        components["dcf"] = WEIGHTS['DCF_UNDERVALUE_HIGH']
    elif "DCF_mid_undervalue" in reason:
        components["dcf"] = WEIGHTS['DCF_UNDERVALUE_MID']
    elif "DCF_undervalue" in reason:
        components["dcf"] = WEIGHTS['DCF_UNDERVALUE_LOW']
    elif "DCF_fair_value" in reason:
        components["dcf"] = WEIGHTS['DCF_FAIR_VALUE']
    elif "DCF_high_overvalue" in reason:
        components["dcf"] = WEIGHTS['DCF_OVERVALUE_HIGH']
    elif "DCF_overvalue" in reason:
        components["dcf"] = WEIGHTS['DCF_OVERVALUE_LOW']
    elif "no_dcf_data" in reason:
        components["dcf"] = WEIGHTS['DCF_UNAVAILABLE']
    if "EMA200_support" in reason:
        components["ema200_support"] = WEIGHTS['SUPPORT_EMA']
    if "extreme_fear_buy_opportunity" in reason:
        components["panic"] = WEIGHTS['PANIC_MARKET_BUY']
    elif "market_overheated_partial_profit" in reason:
        components["overheated"] = WEIGHTS['PROFIT_TAKE_TARGET'] // 2
    if "bull_market_advantage" in reason:
        components["bull_advantage"] = WEIGHTS['BULL_MARKET_SECTOR']
    if "bull_market_profit_take_nudge" in reason:
        components["bull_nudge"] = +10
    # bear_market_hold = 0, no contribution
    if "top10_market_cap" in reason:
        components["top10"] = -10
    if "add_position_zone" in reason:
        components["add_pos"] = WEIGHTS['ADD_POSITION_LOSS']
    if "take_profit_zone" in reason:
        components["take_profit"] = WEIGHTS['PROFIT_TAKE_TARGET']
    if "sector_underweight" in reason:
        components["sector_under"] = -10
    if "sector_overweight" in reason:
        components["sector_over"] = +10
    # Old target prices (REMOVED in new logic)
    target_entry_match = re.search(r"target_entry_price_hit\(\$([\d.]+)\)", reason)
    target_sell_match = re.search(r"target_sell_price_hit\(\$([\d.]+)\)", reason)
    if target_entry_match:
        components["target_entry"] = -15
        components["_target_entry_value"] = float(target_entry_match.group(1))
    if target_sell_match:
        components["target_sell"] = +30
        components["_target_sell_value"] = float(target_sell_match.group(1))
    return components


def reconstruct_old_score(components: dict, base: int = 50) -> int:
    return base + sum(v for k, v in components.items() if not k.startswith("_"))


def compute_new_score(components: dict, curr_price: float, base: int = 50) -> tuple:
    """Apply new EMA200 deviation logic. Returns (new_score, ema200_deviation_delta, ema200)."""
    new_components = {k: v for k, v in components.items() if k not in ("ema200_support", "target_entry", "target_sell") and not k.startswith("_")}
    ema200 = None
    if "_target_entry_value" in components:
        ema200 = components["_target_entry_value"] / 1.01
    elif "_target_sell_value" in components:
        ema200 = components["_target_sell_value"] / 1.15
    if ema200 and ema200 > 0:
        deviation_pct = (curr_price - ema200) / ema200 * 100
        ema_delta = max(-15, min(15, int(deviation_pct)))
    else:
        ema_delta = 0
    if ema_delta != 0:
        new_components["ema_dev"] = ema_delta
    score = base + sum(new_components.values())
    score = max(1, min(100, score))
    return score, ema_delta, ema200


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


def pair_trades_with_filter(rows, blocked_buy_ids: set):
    """FIFO match. Skip buys whose id is in blocked_buy_ids (treated as if never bought)."""
    open_q = defaultdict(list)
    pairs = []
    for tid, ticker, side, qty, price, reason, ts in rows:
        if side == "buy":
            if tid in blocked_buy_ids:
                continue
            open_q[ticker].append([price, qty, reason or "", ts, tid])
        else:
            remaining = qty
            while remaining > 0 and open_q[ticker]:
                bp, bq, breason, btime, bid = open_q[ticker][0]
                used = min(remaining, bq)
                pnl_krw = (price - bp) * used
                pnl_pct = (price - bp) / bp * 100 if bp else 0
                pairs.append({
                    "ticker": ticker, "buy_price": bp, "sell_price": price,
                    "qty": used, "pnl_krw": pnl_krw, "pnl_pct": pnl_pct,
                    "buy_reason": breason, "sell_reason": reason or "",
                    "buy_time": btime, "sell_time": ts,
                })
                bq -= used
                remaining -= used
                if bq == 0:
                    open_q[ticker].pop(0)
                else:
                    open_q[ticker][0][1] = bq
    return pairs


def main():
    rows = fetch_trades()
    print(f"Total trades (last 2 weeks, KR): {len(rows)}")

    # 1) Identify which buys would NOT pass new BUY threshold
    blocked = set()
    blocked_detail = []
    rescored_detail = []
    skipped = 0
    for tid, ticker, side, qty, price, reason, ts in rows:
        if side != "buy":
            continue
        if not reason:
            skipped += 1
            continue
        # Re-score `score N [...]`, `budget_buy [...]`. Components extracted from bracket content.
        if not (reason.startswith("score ") or reason.startswith("budget_buy")):
            skipped += 1
            continue
        components = parse_score_reasons(reason)
        if not components:
            skipped += 1
            continue
        old_score = reconstruct_old_score(components)
        new_score, ema_delta, ema200 = compute_new_score(components, price)
        rescored_detail.append((tid, ticker, ts, old_score, new_score, ema_delta, ema200, reason))
        if new_score > BUY_THRESHOLD:
            blocked.add(tid)
            blocked_detail.append((tid, ticker, ts, old_score, new_score, reason[:80]))
    print(f"Buys without parseable components (skipped): {skipped}")

    print(f"\nRe-scoreable buys (`score N [...]`): {len(rescored_detail)}")
    print(f"Buys BLOCKED under new logic (new_score > {BUY_THRESHOLD}): {len(blocked)}")

    # 2) Show blocked detail
    print(f"\n=== BLOCKED 매수 (새 로직 하 매수 안 됨) ===")
    for tid, t, ts, old, new, r in blocked_detail[:20]:
        print(f"  {ts[:16]} {t}  old:{old:>3} → new:{new:>3}  {r}")

    # 3) Original P&L (no filter)
    pairs_orig = pair_trades_with_filter(rows, blocked_buy_ids=set())
    pnl_orig = sum(p["pnl_krw"] for p in pairs_orig)
    wins_orig = sum(1 for p in pairs_orig if p["pnl_krw"] > 0)
    print(f"\n=== ORIGINAL ({len(pairs_orig)} pairs) ===")
    print(f"  total P&L: {pnl_orig:+,.0f}원 | win rate: {wins_orig/len(pairs_orig)*100:.1f}%")

    # 4) New P&L (filtered)
    pairs_new = pair_trades_with_filter(rows, blocked_buy_ids=blocked)
    pnl_new = sum(p["pnl_krw"] for p in pairs_new)
    wins_new = sum(1 for p in pairs_new if p["pnl_krw"] > 0)
    print(f"\n=== NEW LOGIC ({len(pairs_new)} pairs, {len(pairs_orig) - len(pairs_new)} blocked) ===")
    print(f"  total P&L: {pnl_new:+,.0f}원 | win rate: {wins_new/len(pairs_new)*100:.1f}%")
    print(f"  P&L delta: {pnl_new - pnl_orig:+,.0f}원")

    # 5) Daily comparison
    daily_orig = defaultdict(float)
    daily_new = defaultdict(float)
    for p in pairs_orig:
        daily_orig[p["sell_time"][:10]] += p["pnl_krw"]
    for p in pairs_new:
        daily_new[p["sell_time"][:10]] += p["pnl_krw"]
    print("\n=== 일별 손익 비교 (ORIG vs NEW) ===")
    print(f"{'date':<12} {'orig':>12} {'new':>12} {'Δ':>10}")
    cum_orig = cum_new = 0.0
    for d in sorted(set(list(daily_orig.keys()) + list(daily_new.keys()))):
        o = daily_orig.get(d, 0)
        n = daily_new.get(d, 0)
        cum_orig += o
        cum_new += n
        print(f"{d:<12} {o:>+12,.0f} {n:>+12,.0f} {n-o:>+10,.0f}    cum: orig {cum_orig:>+12,.0f}  new {cum_new:>+12,.0f}")


if __name__ == "__main__":
    main()
