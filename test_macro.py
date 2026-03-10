from services.market.macro_service import MacroService
import json

try:
    data = MacroService.get_macro_data()
    res = {
        "crypto": {k: v.model_dump() for k, v in data.get("crypto", {}).items()},
        "commodities": {k: v.model_dump() for k, v in data.get("commodities", {}).items()},
        "regime": data.get("market_regime").model_dump() if data.get("market_regime") else None
    }
    print(json.dumps(res, indent=2))
except Exception as e:
    print(f"Error: {e}")
