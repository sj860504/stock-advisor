import asyncio
import json
import os
import sys
import websockets

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config import Config
from services.kis.kis_ws_service import KisWsService
from services.market.market_data_service import MarketDataService
from services.market.macro_service import MacroService
from services.trading.portfolio_service import PortfolioService
from services.strategy.trading_strategy_service import TradingStrategyService


async def main(ticker: str = "005930", timeout_sec: int = 30, user_id: str = "sean"):
    ws_service = KisWsService()
    if not ws_service.get_approval_key():
        print("approval_key 발급 실패")
        return

    ws_url = ws_service.ws_url
    if "vts" in Config.KIS_BASE_URL.lower() and ":21000" in ws_url:
        ws_url = ws_url.replace(":21000", ":31000")

    MarketDataService.register_ticker(ticker)

    async with websockets.connect(
        ws_url,
        ping_interval=30,
        ping_timeout=20,
        close_timeout=20,
        open_timeout=60,
    ) as websocket:
        body = {
            "header": {
                "approval_key": ws_service.approval_key,
                "custtype": "P",
                "tr_type": "1",
                "content-type": "utf-8",
            },
            "body": {"input": {"tr_id": "H0STCNT0", "tr_key": ticker}},
        }
        await websocket.send(json.dumps(body))

        async def _recv_once():
            while True:
                msg = await websocket.recv()
                if not msg or msg[0] not in ("0", "1"):
                    continue
                parts = msg.split("|")
                if len(parts) < 4:
                    continue
                tr_id = parts[1]
                if tr_id != "H0STCNT0":
                    continue
                data_str = parts[3]
                values = data_str.split("^")
                if len(values) < 14:
                    continue
                parsed = {
                    "price": float(values[2]),
                    "rate": float(values[5]),
                    "open": float(values[10]),
                    "high": float(values[11]),
                    "low": float(values[12]),
                    "volume": int(values[13]),
                }
                MarketDataService.on_realtime_data(ticker, parsed)
                return

        await asyncio.wait_for(_recv_once(), timeout=timeout_sec)

    state = MarketDataService.get_state(ticker)
    if not state:
        print("state 로드 실패")
        return

    holdings = PortfolioService.load_portfolio(user_id)
    holding = next((h for h in holdings if h["ticker"] == ticker), None)
    macro = MacroService.get_macro_data()

    cash_balance = PortfolioService.load_cash(user_id)
    total_market_value = sum(h["current_price"] * h["quantity"] for h in holdings)
    total_assets = total_market_value + cash_balance

    result = TradingStrategyService.analyze_ticker(
        ticker=ticker,
        state=state,
        holding=holding,
        macro=macro,
        user_state={"panic_locks": {}},
        total_assets=total_assets,
        cash_balance=cash_balance,
        exchange_rate=MacroService.get_exchange_rate(),
    )

    print(result)


if __name__ == "__main__":
    asyncio.run(main())
