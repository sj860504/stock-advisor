from fastapi import APIRouter
from services.notification.alert_service import AlertService
from models.schemas import PriceAlert, MessageResponse, TriggeredAlertsResponse, PendingAlertsResponse

router = APIRouter(
    prefix="/alerts",
    tags=["Alerts"]
)


@router.post("", response_model=MessageResponse)
def create_alert(alert: PriceAlert) -> MessageResponse:
    """가격 알림을 설정합니다. (입력한 티커/종목명을 자동 변환)"""
    AlertService.add_user_alert(alert)
    return MessageResponse(message=f"Alert set for {alert.ticker} at {alert.target_price}")


@router.get("/check", response_model=TriggeredAlertsResponse)
def check_alerts() -> TriggeredAlertsResponse:
    """설정된 알림 조건을 확인하고 트리거된 알림을 반환합니다."""
    triggered = AlertService.check_user_alerts()
    return TriggeredAlertsResponse(triggered_alerts=triggered)


@router.get("/pending", response_model=PendingAlertsResponse)
def get_pending_alerts() -> PendingAlertsResponse:
    """대기 중인 알림 조회/삭제 (Polling용)"""
    alerts = AlertService.get_pending_alerts()
    return PendingAlertsResponse(alerts=alerts)
