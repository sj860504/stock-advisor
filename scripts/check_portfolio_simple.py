import json
import os

# Load Portfolio
portfolio_path = 'stock_advisor/data/portfolio_sean.json'

if not os.path.exists(portfolio_path):
    print(f"Error: Portfolio file not found at {portfolio_path}")
    exit(1)

with open(portfolio_path, 'r', encoding='utf-8') as f:
    holdings = json.load(f)

alerts = []
total_holdings = 0

print("Checking portfolio alerts based on stored JSON data...")

for h in holdings:
    ticker = h.get('ticker')
    name = h.get('name')
    buy_price = h.get('buy_price')
    current_price = h.get('current_price')
    qty = h.get('quantity')
    
    if not buy_price or not current_price or qty == 0:
        continue
        
    total_holdings += 1
    
    # Calculate Profit
    val = current_price * qty
    inv = buy_price * qty
    
    if inv == 0:
        continue
        
    profit_pct = ((val - inv) / inv) * 100
    
    if profit_pct <= -10:
        alerts.append({
            "ticker": ticker,
            "name": name,
            "profit_pct": profit_pct,
            "current_price": current_price,
            "buy_price": buy_price
        })

# Output results
if alerts:
    print(f"\n[Alert Triggered] Found {len(alerts)} items with <= -10% profit:")
    for a in alerts:
        print(f"- {a['name']} ({a['ticker']}): {a['profit_pct']:.2f}% (Curr: {a['current_price']}, Buy: {a['buy_price']})")
else:
    print(f"\n[No Alerts] All {total_holdings} items are performing above -10% threshold.")
