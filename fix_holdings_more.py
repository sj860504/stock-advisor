import re

files = [
    "/Users/a10941/workspace/007_private/003_quant/services/strategy/trading_strategy_service.py",
]

for file_path in files:
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    # Also handle holding['key']
    content = re.sub(r'holding\[[\'"]([a-zA-Z_]+)[\'"]\]', r'(getattr(holding, "\1", None) if not isinstance(holding, dict) else holding.get("\1", None))', content)
    
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)

print("Replacement done.")
