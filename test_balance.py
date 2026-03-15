import sys, os, pprint, asyncio
from services.kis.kis_service import KisService
from config import Config
Config.init_config()
balance = KisService.get_balance()
pprint.pprint(balance.get("summary", []))
