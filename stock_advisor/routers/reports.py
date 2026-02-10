from fastapi import APIRouter
from stock_advisor.services.scheduler_service import SchedulerService
from stock_advisor.services.alert_service import AlertService

router = APIRouter(
    prefix="/summary",
    tags=["Reports"]
)

@router.get("")
def get_daily_summary():
    """
    현재 Top 20 종목의 일일 요약 리포트를 생성합니다.
    과매도/과매수/저평가 종목을 한눈에 보여줍니다.
    """
    data = SchedulerService.get_all_cached_prices()
    if not data:
        return {"message": "Data collection is starting... please wait a moment."}
    
    return AlertService.generate_daily_summary(data)
