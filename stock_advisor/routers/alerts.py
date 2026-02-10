from fastapi import APIRouter
from stock_advisor.services.alert_service import AlertService
from stock_advisor.services.ticker_service import TickerService
from stock_advisor.models.schemas import PriceAlert

router = APIRouter(
    prefix="/alerts",
    tags=["Alerts"]
)

@router.post("")
def create_alert(alert: PriceAlert):
    """
    가격 알림을 설정합니다. (입력된 티커/이름 자동 변환)
    """
    real_ticker = TickerService.resolve_ticker(alert.ticker)
    alert.ticker = real_ticker # 변환된 티커로 저장
    
    AlertService.add_user_alert(alert)
    return {"message": f"Alert set for {alert.ticker} at {alert.target_price}"}

@router.get("/check")
def check_alerts():
    """
    설정된 알림 조건을 확인하고 트리거된 알림을 반환합니다.
    (주기적으로 호출하여 확인하는 용도)
    """
    triggered = AlertService.check_user_alerts()
    return {"triggered_alerts": triggered}

@router.get("/pending")
def get_pending_alerts():
    """대기 중인 알림 조회 및 삭제 (Polling용)"""
    alerts = AlertService.get_pending_alerts()
    return {"alerts": alerts}
