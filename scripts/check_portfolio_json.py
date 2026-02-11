import json

portfolio_path = 'data/portfolio_sean.json'

try:
    with open(portfolio_path, 'r', encoding='utf-8') as f:
        holdings = json.load(f)
except FileNotFoundError:
    print(f"Error: Portfolio file not found at {portfolio_path}")
    exit(1)

alerts = []

for h in holdings:
    name = h.get('name')
    ticker = h.get('ticker')
    buy_price = h.get('buy_price')
    current_price = h.get('current_price')
    quantity = h.get('quantity')

    if buy_price is None or current_price is None or quantity is None or quantity == 0:
        continue

    profit_pct = ((current_price - buy_price) / buy_price) * 100

    if profit_pct <= -10:
        alerts.append({
            "name": name,
            "ticker": ticker,
            "profit_pct": profit_pct,
            "current_price": current_price,
            "buy_price": buy_price
        })

if alerts:
    print("Portfolio Alert: The following assets have a profit percentage of -10% or lower:")
    for a in alerts:
        print(f"- {a['name']} ({a['ticker']}): {a['profit_pct']:.2f}% (Current: {a['current_price']}, Buy: {a['buy_price']})")
else:
    print("No portfolio alerts. All assets are above -10% profit.")
