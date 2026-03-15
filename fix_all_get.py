import re

files = [
    "/Users/a10941/workspace/007_private/003_quant/services/strategy/trading_strategy_service.py",
]

for file_path in files:
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    # 1. Replace h.get('something', default)
    content = re.sub(r'\bh\.get\(([\'"][a-zA-Z_]+[\'"]),\s*([^)]+)\)', r'(getattr(h, \1, \2) if not isinstance(h, dict) else h.get(\1, \2))', content)
    # 2. Replace h.get('something')
    content = re.sub(r'\bh\.get\(([\'"][a-zA-Z_]+[\'"])\)', r'(getattr(h, \1, None) if not isinstance(h, dict) else h.get(\1))', content)
    
    # 3. Replace holding.get('something', default)
    content = re.sub(r'\bholding\.get\(([\'"][a-zA-Z_]+[\'"]),\s*([^)]+)\)', r'(getattr(holding, \1, \2) if not isinstance(holding, dict) else holding.get(\1, \2))', content)
    # 4. Replace holding.get('something')
    content = re.sub(r'\bholding\.get\(([\'"][a-zA-Z_]+[\'"])\)', r'(getattr(holding, \1, None) if not isinstance(holding, dict) else holding.get(\1))', content)

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)

print("Replacement done.")
