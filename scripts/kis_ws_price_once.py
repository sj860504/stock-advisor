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


async def main(ticker: str = "005930", timeout_sec: int = 30):
    ws_service = KisWsService()
    if not ws_service.get_approval_key():
        print("approval_key 발급 실패")
        return

    ws_url = ws_service.ws_url
    if "vts" in Config.KIS_BASE_URL.lower() and ":21000" in ws_url:
        ws_url = ws_url.replace(":21000", ":31000")

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
                price = float(values[2])
                rate = float(values[5])
                open_price = float(values[10])
                high_price = float(values[11])
                low_price = float(values[12])
                volume = int(values[13])
                print(
                    f"{ticker} price={price} rate={rate}% "
                    f"open={open_price} high={high_price} low={low_price} vol={volume}"
                )
                return

        await asyncio.wait_for(_recv_once(), timeout=timeout_sec)


if __name__ == "__main__":
    asyncio.run(main())
