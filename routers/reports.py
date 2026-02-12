from fastapi import APIRouter
from services.base.scheduler_service import SchedulerService
from services.notification.alert_service import AlertService

router = APIRouter(
    prefix="/summary",
    tags=["Reports"]
)

@router.get("")
def get_daily_summary():
    """
    현재 Top 100 종목의 실시간 요약 리포트를 생성합니다.
    과매수/과매도 및 시가총액 급등 종목을 한눈에 보여줍니다.
    """
    data = SchedulerService.get_all_cached_prices()
    if not data:
        return {"message": "Data collection is starting... please wait a moment."}
    
    return AlertService.generate_daily_summary(data)
