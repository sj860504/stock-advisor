from fastapi import APIRouter
from services.scheduler_service import SchedulerService
from services.alert_service import AlertService

router = APIRouter(
    prefix="/summary",
    tags=["Reports"]
)

@router.get("")
def get_daily_summary():
    """
    ?꾩옱 Top 20 醫낅ぉ???쇱씪 ?붿빟 由ы룷?몃? ?앹꽦?⑸땲??
    怨쇰ℓ??怨쇰ℓ????됯? 醫낅ぉ???쒕늿??蹂댁뿬以띾땲??
    """
    data = SchedulerService.get_all_cached_prices()
    if not data:
        return {"message": "Data collection is starting... please wait a moment."}
    
    return AlertService.generate_daily_summary(data)
