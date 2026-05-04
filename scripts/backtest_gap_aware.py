"""
Cash-gap-aware buy logic 백테스트 (US 시장 중점).

새 로직:
  threshold = base_threshold + min(relax_max, int(gap_pct/5) * relax_step)
  per_trade_multiplier = 1 + min(2, int(gap_pct/25))
  cooldown = 1h if gap_pct >= 30 else 24h

KR/US 시뮬은 트레이드 시점의 가상 cash 추이를 가정한다 (아래 모델 참고).
실제 운영 데이터는 production 결과로 검증.
"""
import sqlite3
import re
from collections import defaultdict
from datetime import datetime

DB = "data/stock_advisor.db"
SINCE = "2026-03-01"

# 기본 파라미터
BASE_THRESHOLD = 40
RELAX_STEP = 5
RELAX_MAX = 20
HIGH_GAP_PCT = 30
PER_TRADE_RATIO = 0.05
TARGET_CASH_RATIO = 0.40  # NEUTRAL


def fetch_trades(market="US"):
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
    if market == "US":
        rows = [r for r in rows if not (r[1].isdigit() and len(r[1]) == 6)]
    elif market == "KR":
        rows = [r for r in rows if r[1].isdigit() and len(r[1]) == 6]
    return rows


def parse_score_from_reason(reason: str) -> int:
    """Parse score from `score N [...]` reason; return 50 if budget_buy or unparseable."""
    m = re.match(r"^score (\d+)\s", reason or "")
    return int(m.group(1)) if m else 50  # budget_buy/Strategy execution → assume mid


def simulate_gap_aware(rows, initial_cash_usd: float = 50000.0, market: str = "US"):
    """Simulate cash position over time + show gap-aware decisions."""
    cash = initial_cash_usd
    holdings_value = 0.0  # rolling estimate
    daily_gap = []
    cooldown_normal = 0
    cooldown_short = 0
    threshold_relax_count = defaultdict(int)

    # Track per-ticker last buy timestamp for cooldown
    last_buy_ts = {}

    for tid, ticker, side, qty, price, reason, ts in rows:
        ts_dt = datetime.fromisoformat(ts)
        ts_epoch = ts_dt.timestamp()
        amount = qty * price

        # Update cash/holdings
        if side == "buy":
            cash -= amount
            holdings_value += amount  # naive — doesn't track price changes between trades
        else:
            cash += amount
            holdings_value = max(0, holdings_value - amount)

        # Compute gap at this point
        total = cash + holdings_value
        cash_ratio = cash / total if total > 0 else 0
        gap_pct = max(0, (cash_ratio - TARGET_CASH_RATIO) * 100)

        # Threshold relax
        relax = min(RELAX_MAX, int(gap_pct / 5) * RELAX_STEP)
        relaxed_threshold = BASE_THRESHOLD + relax
        threshold_relax_count[relax] += 1

        # Cooldown decision
        last_ts = last_buy_ts.get(ticker, 0)
        elapsed_hours = (ts_epoch - last_ts) / 3600 if last_ts else 999
        if side == "buy":
            if gap_pct >= HIGH_GAP_PCT:
                cooldown_short += 1 if elapsed_hours < 1 else 0
                if elapsed_hours < 1:
                    pass  # would be skipped
            else:
                cooldown_normal += 1 if elapsed_hours < 24 else 0
            last_buy_ts[ticker] = ts_epoch

        if side == "buy":
            score = parse_score_from_reason(reason)
            daily_gap.append({
                "date": ts[:10], "ticker": ticker, "amount": amount,
                "gap_pct": gap_pct, "relaxed_threshold": relaxed_threshold,
                "score": score, "would_pass": score <= relaxed_threshold,
            })

    return daily_gap, threshold_relax_count, cash, holdings_value


def threshold_what_if(rows, thresholds):
    """Show how many historical buys would pass at various static thresholds."""
    buys_with_score = []
    for tid, ticker, side, qty, price, reason, ts in rows:
        if side != "buy":
            continue
        score = parse_score_from_reason(reason)
        buys_with_score.append((ticker, score, ts, qty * price))
    print(f"\n=== Static threshold what-if (US 60d 매수 {len(buys_with_score)}건) ===")
    print(f"{'threshold':>10} {'pass':>6} {'pass%':>6} {'amount_total':>14}")
    total_amount = sum(b[3] for b in buys_with_score)
    for thr in thresholds:
        passed = [b for b in buys_with_score if b[1] <= thr]
        pa = sum(b[3] for b in passed)
        print(f"{thr:>10} {len(passed):>6} {len(passed)/len(buys_with_score)*100:>5.1f}% ${pa:>13,.0f} ({pa/total_amount*100:.1f}%)")


def main():
    print("="*60)
    print("Cash-gap-aware Backtest (US 시장)")
    print(f"  base_threshold={BASE_THRESHOLD}, relax_step={RELAX_STEP}, max={RELAX_MAX}")
    print(f"  high_gap={HIGH_GAP_PCT}%pa, target_cash={TARGET_CASH_RATIO:.0%}")
    print("="*60)

    rows = fetch_trades("US")
    print(f"\nUS trades (60d): {len(rows)}")

    # Static threshold what-if (cash gap 변동성 무시한 단순 비교)
    threshold_what_if(rows, [30, 40, 50, 60, 70, 100])

    daily, relax_dist, final_cash, final_holdings = simulate_gap_aware(rows, initial_cash_usd=10000)

    # Threshold relax 분포
    print(f"\n=== Threshold 동적 완화 분포 ===")
    print(f"{'relax':>6} {'effective_thr':>14} {'occurrences':>12}")
    for k in sorted(relax_dist.keys()):
        print(f"{k:>+6} {BASE_THRESHOLD+k:>14} {relax_dist[k]:>12}")

    # 매수 트레이드만 분석
    buys = [d for d in daily if d.get("ticker")]
    print(f"\n=== 매수 트레이드 새 로직 검증 ({len(buys)}건) ===")

    pass_count = sum(1 for b in buys if b["would_pass"])
    fail_count = len(buys) - pass_count
    print(f"  새 로직 통과: {pass_count} ({pass_count/len(buys)*100:.1f}%)")
    print(f"  새 로직 차단: {fail_count} ({fail_count/len(buys)*100:.1f}%)")

    # 차단된 매수 (score > relaxed_threshold)
    blocked = [b for b in buys if not b["would_pass"]]
    if blocked:
        print(f"\n=== 새 로직이 차단할 트레이드 (top 10) ===")
        for b in blocked[:10]:
            print(f"  {b['date']} {b['ticker']:<6} score={b['score']:>3} thr={b['relaxed_threshold']:>3} (gap={b['gap_pct']:.1f}%pa)")

    # 시간별 gap 추이 (일별 평균)
    print(f"\n=== 일별 gap 추이 (US 시장 가정) ===")
    by_day = defaultdict(list)
    for d in daily:
        by_day[d["date"]].append(d["gap_pct"])
    print(f"{'date':<12} {'avg_gap%':>8} {'max_thr':>8}")
    for date in sorted(by_day.keys()):
        gaps = by_day[date]
        avg_gap = sum(gaps) / len(gaps)
        max_thr = BASE_THRESHOLD + min(RELAX_MAX, int(max(gaps) / 5) * RELAX_STEP)
        print(f"{date:<12} {avg_gap:>8.1f} {max_thr:>8}")

    # 시뮬 종료 시점
    print(f"\n=== 시뮬 종료 ===")
    total = final_cash + final_holdings
    final_ratio = final_cash / total if total > 0 else 0
    print(f"  최종 cash: ${final_cash:,.2f}")
    print(f"  최종 holdings: ${final_holdings:,.2f}")
    print(f"  cash 비중: {final_ratio:.1%}")


if __name__ == "__main__":
    main()
