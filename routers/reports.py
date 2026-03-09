from fastapi import APIRouter
from typing import Dict, Any
from services.base.scheduler_service import SchedulerService
from services.notification.alert_service import AlertService

router = APIRouter(
    prefix="/summary",
    tags=["Reports"]
)

@router.get("", response_model=Dict[str, Any])
def get_daily_summary() -> Dict[str, Any]:
    """
    Generate real-time summary report for current Top 100 tickers.
    Shows overbought/oversold and market-cap surging tickers at a glance.
    """
    data = SchedulerService.get_all_cached_prices()
    if not data:
        return {"message": "Data collection is starting... please wait a moment."}

    return AlertService.generate_daily_summary(data)
